from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.database import Base, SessionLocal, engine
from app.models import RankHistory, User, UserRank
from scripts.backfill_user_ranks import backfill, main
from tests.test_game_engine import client, now
from tests.test_ranks import finished_room, rp_of, save
from tests.test_websocket import clear_state

REACHED = datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def db_reset():
    clear_state()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    clear_state()


def add_users(count, ranks=None):
    with SessionLocal() as db:
        rows = [User(username=f"old{i}", password_hash="unused") for i in range(count)]
        db.add_all(rows)
        db.flush()
        for i, (rp, reached) in (ranks or {}).items():
            db.add(UserRank(user_id=rows[i].id, total_rp=rp, rp_reached_at=reached, created_at=reached, updated_at=reached))
        db.commit()
        return [row.id for row in rows]


def snapshot():
    with SessionLocal() as db:
        return {
            row.user_id: (row.total_rp, row.rp_reached_at, row.created_at, row.updated_at)
            for row in db.scalars(select(UserRank))
        }


def run(apply=True):
    with SessionLocal() as db:
        return backfill(db, apply=apply)


def test_missing_user_gets_zero_rp():
    [uid] = add_users(1)
    before = datetime.now(timezone.utc)
    assert run() == {"total_users": 1, "existing_ranks": 0, "missing_ranks": 1, "created": 1}
    total_rp, reached, created, updated = snapshot()[uid]
    assert total_rp == 0
    assert reached is not None and created >= before.replace(microsecond=0) and updated == created


def test_existing_ranks_preserved_and_only_missing_created():
    ids = add_users(5, {1: (500, REACHED), 3: (1720, REACHED)})
    before = snapshot()
    assert run() == {"total_users": 5, "existing_ranks": 2, "missing_ranks": 3, "created": 3}
    after = snapshot()
    assert after[ids[1]] == before[ids[1]] and after[ids[1]][0] == 500
    assert after[ids[3]] == before[ids[3]] and after[ids[3]][:2] == (1720, REACHED)
    assert {uid: after[uid][0] for uid in (ids[0], ids[2], ids[4])} == {ids[0]: 0, ids[2]: 0, ids[4]: 0}
    assert set(after) == set(ids)


def test_second_run_creates_nothing():
    ids = add_users(3, {0: (500, REACHED)})
    run()
    first = snapshot()
    assert run() == {"total_users": 3, "existing_ranks": 3, "missing_ranks": 0, "created": 0}
    assert snapshot() == first
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UserRank)) == len(ids)


def test_empty_database():
    assert run() == {"total_users": 0, "existing_ranks": 0, "missing_ranks": 0, "created": 0}
    assert snapshot() == {}


def test_dry_run_changes_nothing():
    add_users(3, {2: (900, REACHED)})
    before = snapshot()
    assert run(apply=False) == {"total_users": 3, "existing_ranks": 1, "missing_ranks": 2, "created": 0}
    assert snapshot() == before


def test_db_error_rolls_back():
    add_users(3)
    with SessionLocal() as db:
        def broken():
            raise RuntimeError("commit failed")

        db.commit = broken
        with pytest.raises(RuntimeError):
            backfill(db, apply=True)
    assert snapshot() == {}


def test_cli_dry_run_and_apply(capsys):
    add_users(2)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert f"Target database: {engine.url.database}" in out
    assert "Mode: DRY RUN" in out and "Missing ranks: 2" in out and "Created: 0 (dry run)" in out
    assert snapshot() == {}

    assert main(["--apply", "--database", engine.url.database]) == 0
    out = capsys.readouterr().out
    assert "Mode: APPLY" in out and "Created: 2" in out
    assert len(snapshot()) == 2


@pytest.mark.parametrize("argv", [["--apply"], ["--apply", "--database", "paint_rush"]])
def test_cli_apply_requires_matching_database(capsys, argv):
    add_users(2)
    assert main(argv) == 2
    assert "Aborted" in capsys.readouterr().out
    assert snapshot() == {}


def test_cli_hides_credentials(capsys):
    main([])
    out = capsys.readouterr().out
    assert engine.url.password and engine.url.password not in out
    assert engine.url.username not in out


def test_register_still_creates_rank(client):
    response = client.post("/api/v1/auth/register", json={"username": "fresh", "password": "password123"})
    assert response.status_code == 201
    assert rp_of([response.json()["id"]]) == [0]
    assert run()["created"] == 0


def test_settlement_after_backfill(now):
    ids = add_users(4, {0: (500, REACHED)})
    run()
    room = finished_room(ids, {ids[3]: 9, ids[2]: 5, ids[1]: 3})
    save(room)
    assert rp_of(ids) == [470, 0, 10, 30]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RankHistory)) == 4
