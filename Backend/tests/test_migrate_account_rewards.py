import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text

from app.database import Base, SessionLocal, engine
from app.models import Match, MatchReward, User, UserRank, UserWallet
from scripts import migrate_account_rewards
from scripts.migrate_account_rewards import main, migrate

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
NEW_TABLES = ["user_wallets", "match_rewards"]


@pytest.fixture(autouse=True)
def db_reset():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def drop_new_tables():
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE match_rewards"))
        connection.execute(text("DROP TABLE user_wallets"))


def make_users(count, prefix="old"):
    with SessionLocal() as db:
        rows = [User(username=f"{prefix}{i}", password_hash="unused") for i in range(count)]
        db.add_all(rows)
        db.flush()
        db.add_all(UserRank(user_id=row.id, total_rp=100 * i) for i, row in enumerate(rows))
        db.commit()
        return [row.id for row in rows]


def wallets():
    with engine.connect() as connection:
        return dict(connection.execute(text("SELECT user_id, gold FROM user_wallets")).all())


def ranks():
    with engine.connect() as connection:
        return dict(connection.execute(text("SELECT user_id, total_rp FROM user_ranks")).all())


def tables():
    return set(inspect(engine).get_table_names())


def add_reward(user_id):
    match_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Match(id=match_id, map_id="default", started_at=BASE_TIME, ended_at=BASE_TIME, winner_id=user_id))
        db.flush()
        db.add(MatchReward(
            match_id=match_id, user_id=user_id, placement=1, gold_earned=50, gold_before=700, gold_after=750,
            experience_earned=20, experience_before=0, experience_after=20, level_before=1, level_after=1,
        ))
        db.commit()
    return match_id


def rewards():
    with engine.connect() as connection:
        return [tuple(r) for r in connection.execute(text("SELECT match_id, user_id, gold_after, experience_after FROM match_rewards"))]


def test_creates_tables_and_wallets_on_legacy_db():
    ids = make_users(3)
    drop_new_tables()
    before_ranks = ranks()
    assert not set(NEW_TABLES) & tables()
    result = migrate(engine, apply=True)
    assert result == {
        "missing_tables": NEW_TABLES, "total_users": 3, "existing_wallets": 0, "missing_wallets": 3,
        "created_tables": NEW_TABLES, "created_wallets": 3,
    }
    assert set(NEW_TABLES) <= tables()
    assert wallets() == {uid: 0 for uid in ids}
    assert ranks() == before_ranks


def test_existing_gold_and_rewards_preserved():
    ids = make_users(3)
    with SessionLocal() as db:
        db.add(UserWallet(user_id=ids[0], gold=750))
        db.commit()
    match_id = add_reward(ids[0])
    before = rewards()
    result = migrate(engine, apply=True)
    assert (result["missing_tables"], result["existing_wallets"], result["missing_wallets"], result["created_wallets"]) == ([], 1, 2, 2)
    assert wallets() == {ids[0]: 750, ids[1]: 0, ids[2]: 0}
    assert rewards() == before == [(match_id, ids[0], 750, 20)]


def test_repeated_runs_create_nothing_new():
    ids = make_users(2)
    drop_new_tables()
    migrate(engine, apply=True)
    with engine.begin() as connection:
        connection.execute(text("UPDATE user_wallets SET gold = 300 WHERE user_id = :u"), {"u": ids[1]})
    for _ in range(2):
        result = migrate(engine, apply=True)
        assert (result["created_tables"], result["created_wallets"], result["missing_wallets"]) == ([], 0, 0)
    assert wallets() == {ids[0]: 0, ids[1]: 300}


def test_new_users_after_migration_get_wallets_on_rerun():
    make_users(1)
    migrate(engine, apply=True)
    later = make_users(2, prefix="late")
    assert migrate(engine, apply=True)["created_wallets"] == 2
    assert {uid: wallets()[uid] for uid in later} == {later[0]: 0, later[1]: 0}


def test_empty_database():
    drop_new_tables()
    result = migrate(engine, apply=True)
    assert (result["total_users"], result["created_wallets"], result["created_tables"]) == (0, 0, NEW_TABLES)


def test_dry_run_changes_nothing():
    make_users(2)
    drop_new_tables()
    before = tables()
    result = migrate(engine, apply=False)
    assert (result["missing_tables"], result["missing_wallets"], result["created_tables"], result["created_wallets"]) == (NEW_TABLES, 2, [], 0)
    assert tables() == before

    migrate(engine, apply=True)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM user_wallets"))
    assert migrate(engine, apply=False)["missing_wallets"] == 2
    assert wallets() == {}


def test_error_rolls_back_everything(monkeypatch):
    make_users(2)
    drop_new_tables()

    def broken():
        raise RuntimeError("wallet step failed")

    monkeypatch.setattr(migrate_account_rewards, "missing_wallets", broken)
    with pytest.raises(RuntimeError):
        migrate(engine, apply=True)
    assert not set(NEW_TABLES) & tables()


def test_requires_base_tables():
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE match_rewards"))
        connection.execute(text("DROP TABLE rank_history"))
        connection.execute(text("DROP TABLE match_player_results"))
        connection.execute(text("DROP TABLE matches"))
    with pytest.raises(RuntimeError, match="matches"):
        migrate(engine, apply=True)
    assert "match_rewards" not in tables()


def test_only_reward_tables_created():
    drop_new_tables()
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE user_decks"))
    migrate(engine, apply=True)
    assert "user_decks" not in tables()
    assert set(NEW_TABLES) <= tables()


def test_cli_dry_run_apply_and_wrong_database(capsys):
    make_users(2)
    drop_new_tables()
    assert main([]) == 0
    out = capsys.readouterr().out
    assert f"Target database: {engine.url.database}" in out and "Mode: DRY RUN" in out
    assert "Missing tables: user_wallets, match_rewards" in out and "Created wallets: 0 (dry run)" in out
    assert engine.url.password not in out and engine.url.username not in out
    for argv in (["--apply"], ["--apply", "--database", "paint_rush"]):
        assert main(argv) == 2
        assert "Aborted" in capsys.readouterr().out
    assert not set(NEW_TABLES) & tables()

    assert main(["--apply", "--database", engine.url.database]) == 0
    out = capsys.readouterr().out
    assert "Created tables: user_wallets, match_rewards" in out and "Created wallets: 2" in out
    assert main(["--apply", "--database", engine.url.database]) == 0
    assert "Created wallets: 0" in capsys.readouterr().out


def test_migrated_schema_matches_model():
    make_users(1)
    drop_new_tables()
    migrate(engine, apply=True)
    inspector = inspect(engine)
    for model in (UserWallet, MatchReward):
        actual = {c["name"]: c["nullable"] for c in inspector.get_columns(model.__table__.name)}
        assert actual == {c.name: c.nullable for c in model.__table__.columns}
    assert ["user_id"] in [i["column_names"] for i in inspector.get_indexes("match_rewards")]
    assert inspector.get_pk_constraint("match_rewards")["constrained_columns"] == ["match_id", "user_id"]
