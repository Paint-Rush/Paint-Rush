import uuid

import pytest
from sqlalchemy import inspect, select, text

from app.database import Base, SessionLocal, engine
from app.models import Match, RankHistory, UserRank
from app.ranks import load_settlements, read_history
from scripts import migrate_rank_history
from scripts.migrate_rank_history import COLUMNS, main, migrate
from tests.test_ranks import BASE_TIME, make_users, rp_of
from tests.test_rank_settlement import settle
from tests.test_websocket import clear_state

NEW_COLUMNS = list(COLUMNS)
OLD_COLUMNS = ["id", "match_id", "user_id", "placement", "rp_before", "placement_rp_change", "rp_change", "rp_after", "created_at"]


@pytest.fixture(autouse=True)
def legacy_db():
    clear_state()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    clear_state()


def drop_columns(*names):
    with engine.begin() as connection:
        for name in names:
            connection.execute(text(f"ALTER TABLE rank_history DROP COLUMN {name}"))


def columns():
    return {c["name"]: c for c in inspect(engine).get_columns("rank_history")}


def add_legacy_match(user_ids):
    match_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(Match.__table__.insert().values(
            id=match_id, map_id="default", started_at=BASE_TIME, ended_at=BASE_TIME, winner_id=user_ids[0],
        ))
        for placement, (uid, change) in enumerate(zip(user_ids, (30, 10, -10, -30)), 1):
            connection.execute(text(
                "INSERT INTO rank_history (match_id, user_id, placement, rp_before, placement_rp_change, rp_change, rp_after) "
                "VALUES (:m, :u, :p, 500, :c, :c, :a)"
            ), {"m": match_id, "u": uid, "p": placement, "c": change, "a": 500 + change})
    return match_id


def legacy_rows():
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(f"SELECT {', '.join(OLD_COLUMNS)} FROM rank_history ORDER BY id"))]


@pytest.fixture
def legacy():
    ids = make_users(4, rps=[530, 510, 490, 470])
    drop_columns(*NEW_COLUMNS)
    match_id = add_legacy_match(ids)
    return ids, match_id


def test_adds_missing_columns_on_legacy_db(legacy):
    ids, _ = legacy
    before = legacy_rows()
    assert not set(NEW_COLUMNS) & set(columns())
    assert migrate(engine, apply=True) == {"table_exists": True, "rows": 4, "missing": NEW_COLUMNS, "added": NEW_COLUMNS}
    after = columns()
    assert set(NEW_COLUMNS) <= set(after)
    assert all(after[name]["nullable"] for name in NEW_COLUMNS)
    assert legacy_rows() == before
    assert rp_of(ids) == [530, 510, 490, 470]
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM rank_history WHERE tier_before IS NOT NULL OR tier_after IS NOT NULL")).scalar() == 0


def test_only_missing_columns_added():
    drop_columns("tier_after", "division_after")
    assert migrate(engine, apply=True)["added"] == ["tier_after", "division_after"]
    assert set(NEW_COLUMNS) <= set(columns())


def test_repeated_runs_are_safe(legacy):
    migrate(engine, apply=True)
    snapshot = legacy_rows()
    for _ in range(2):
        assert migrate(engine, apply=True) == {"table_exists": True, "rows": 4, "missing": [], "added": []}
    assert legacy_rows() == snapshot


def test_up_to_date_db_untouched():
    assert migrate(engine, apply=True) == {"table_exists": True, "rows": 0, "missing": [], "added": []}


def test_dry_run_changes_nothing(legacy):
    before = legacy_rows()
    assert migrate(engine, apply=False) == {"table_exists": True, "rows": 4, "missing": NEW_COLUMNS, "added": []}
    assert not set(NEW_COLUMNS) & set(columns())
    assert legacy_rows() == before


def test_missing_table_is_reported():
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE rank_history"))
    assert migrate(engine, apply=True) == {"table_exists": False, "rows": 0, "missing": [], "added": []}
    assert not inspect(engine).has_table("rank_history")


def test_error_rolls_back_every_column(legacy, monkeypatch):
    monkeypatch.setitem(migrate_rank_history.COLUMNS, "zz_broken", "NOT_A_TYPE")
    with pytest.raises(Exception):
        migrate(engine, apply=True)
    assert not set(NEW_COLUMNS) & set(columns())
    assert len(legacy_rows()) == 4


def test_cli_dry_run_apply_and_wrong_database(legacy, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert f"Target database: {engine.url.database}" in out and "Mode: DRY RUN" in out
    assert "Missing columns: tier_before, division_before, tier_after, division_after" in out
    assert "Added columns: none (dry run)" in out
    assert engine.url.password not in out
    for argv in (["--apply"], ["--apply", "--database", "paint_rush"]):
        assert main(argv) == 2
        assert "Aborted" in capsys.readouterr().out
    assert not set(NEW_COLUMNS) & set(columns())

    assert main(["--apply", "--database", engine.url.database]) == 0
    assert "Added columns: tier_before, division_before, tier_after, division_after" in capsys.readouterr().out
    assert main(["--apply", "--database", engine.url.database]) == 0
    assert "Added columns: none" in capsys.readouterr().out


def test_schema_matches_model_after_migration(legacy):
    migrate(engine, apply=True)
    actual = columns()
    model = RankHistory.__table__.columns
    assert set(actual) == {c.name for c in model}
    for name in NEW_COLUMNS:
        assert actual[name]["nullable"] is model[name].nullable is True


def test_legacy_null_tiers_and_new_settlements(legacy):
    ids, legacy_match = legacy
    migrate(engine, apply=True)
    results = [{"user_id": uid, "rank": i + 1} for i, uid in enumerate(ids)]
    with SessionLocal() as db:
        old = load_settlements(db, legacy_match, results)
        assert old[ids[0]]["applied"] is True
        assert (old[ids[0]]["rp_change"], old[ids[0]]["tier_before"], old[ids[0]]["tier_after"]) == (30, None, None)
        assert [h["rp_after"] for h in read_history(db, ids[0], 10)] == [530]

    changes = settle(ids)
    assert (changes[ids[0]]["tier_before"], changes[ids[0]]["tier_after"]) == ("SILVER", "SILVER")
    assert rp_of(ids) == [560, 520, 480, 440]
    with SessionLocal() as db:
        rows = db.scalars(select(RankHistory).where(RankHistory.user_id == ids[3]).order_by(RankHistory.id)).all()
        assert [(r.rp_after, r.tier_after, r.division_after) for r in rows] == [(470, None, None), (440, "SILVER", 2)]
        assert db.get(UserRank, ids[3]).total_rp == 440
