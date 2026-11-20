import threading

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.levels import (
    MAX_INT,
    add_experience,
    experience_to_next,
    level_for_experience,
    progress,
    read_level,
    required_experience,
    save_level,
)
from app.models import User, UserLevel
from tests.test_game_engine import client, now
from tests.test_user_deck import headers


@pytest.fixture(autouse=True)
def db_reset():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def make_users(count):
    with SessionLocal() as db:
        rows = [User(username=f"lvl{i}", password_hash="unused") for i in range(count)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def give(user_id, amount):
    with SessionLocal() as db:
        result = add_experience(db, user_id, amount)
        db.commit()
    return result


def stored(user_id):
    with SessionLocal() as db:
        row = db.get(UserLevel, user_id)
        return None if row is None else (row.level, row.total_experience)


@pytest.mark.parametrize("experience, level", [
    (0, 1), (99, 1), (100, 2), (299, 2), (300, 3), (599, 3), (600, 4), (999, 4), (1000, 5),
    (1499, 5), (1500, 6), (4499, 9), (4500, 10), (495_000, 100), (494_999, 99), (MAX_INT, 6554),
])
def test_level_for_experience(experience, level):
    assert level_for_experience(experience) == level
    assert required_experience(level) <= experience < required_experience(level + 1)


@pytest.mark.parametrize("level, total, step", [(1, 0, 100), (2, 100, 200), (3, 300, 300), (5, 1000, 500), (6, 1500, 600), (10, 4500, 1000)])
def test_required_experience_table(level, total, step):
    assert required_experience(level) == total
    assert experience_to_next(level) == step == level * 100


def test_formula_matches_boundaries_exhaustively():
    for level in range(1, 400):
        start = required_experience(level)
        assert level_for_experience(start) == level
        assert level_for_experience(start + experience_to_next(level) - 1) == level


def test_progress():
    assert progress(250) == {"level": 2, "total_experience": 250, "level_experience": 100, "next_level_experience": 300, "experience_to_next": 50}
    assert progress(0)["experience_to_next"] == 100


@pytest.mark.parametrize("value", [-1, 1.0, "100", None, True])
def test_invalid_calculation_input(value):
    with pytest.raises(ValueError):
        level_for_experience(value)
    with pytest.raises(ValueError):
        required_experience(value if value != -1 else 0)


def test_add_experience_without_level_row():
    [uid] = make_users(1)
    assert stored(uid) is None
    assert give(uid, 40) == {
        "user_id": uid, "experience_earned": 40, "experience_before": 0, "experience_after": 40,
        "level_before": 1, "level_after": 1, "leveled_up": False,
    }
    assert stored(uid) == (1, 40)


def test_accumulates_and_levels_up():
    [uid] = make_users(1)
    give(uid, 60)
    result = give(uid, 50)
    assert (result["experience_before"], result["experience_after"], result["level_before"], result["level_after"], result["leveled_up"]) == (60, 110, 1, 2, True)
    assert stored(uid) == (2, 110)
    with SessionLocal() as db:
        assert read_level(db, uid) == {"level": 2, "total_experience": 110}


def test_multiple_levels_in_one_grant():
    [uid] = make_users(1)
    result = give(uid, 1000)
    assert (result["level_before"], result["level_after"]) == (1, 5)
    assert give(uid, 3500)["level_after"] == 10
    assert stored(uid) == (10, 4500)


def test_experience_without_level_up_and_zero_grant():
    [uid] = make_users(1)
    give(uid, 100)
    assert give(uid, 199)["leveled_up"] is False
    assert stored(uid) == (2, 299)
    zero = give(uid, 0)
    assert (zero["experience_before"], zero["experience_after"], zero["leveled_up"]) == (299, 299, False)
    assert stored(uid) == (2, 299)


@pytest.mark.parametrize("amount", [-1, -100, 1.5, "10", None, True, 10.0])
def test_invalid_amount_rejected(amount):
    [uid] = make_users(1)
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            add_experience(db, uid, amount)
    assert stored(uid) is None


@pytest.mark.parametrize("user_id", [999999, 0, -1, "1", None, True])
def test_unknown_user_rejected(user_id):
    make_users(1)
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            add_experience(db, user_id, 10)
    with SessionLocal() as db:
        assert db.scalars(select(UserLevel)).all() == []


def test_overflow_rejected():
    [uid] = make_users(1)
    give(uid, MAX_INT - 5)
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            add_experience(db, uid, 6)
    assert stored(uid)[1] == MAX_INT - 5


def test_does_not_commit_and_caller_can_roll_back():
    [uid] = make_users(1)
    give(uid, 150)
    with SessionLocal() as db:
        result = add_experience(db, uid, 500)
        assert result["level_after"] == 4
        assert db.get(UserLevel, uid).total_experience == 650
        assert stored(uid) == (2, 150)
        db.rollback()
    assert stored(uid) == (2, 150)
    with SessionLocal() as db:
        add_experience(db, uid, 500)
    assert stored(uid) == (2, 150)


def test_rollback_after_later_failure_restores_everything():
    ids = make_users(2)
    with SessionLocal() as db:
        add_experience(db, ids[0], 300)
        with pytest.raises(ValueError):
            add_experience(db, ids[1], -1)
        db.rollback()
    assert stored(ids[0]) is None


def test_existing_data_preserved():
    ids = make_users(2)
    with SessionLocal() as db:
        save_level(db, ids[0], 7, 50)
    result = give(ids[0], 10)
    assert (result["level_before"], result["level_after"], result["experience_after"]) == (7, 7, 60)
    assert stored(ids[0]) == (7, 60)
    assert stored(ids[1]) is None


def test_concurrent_grants_not_lost():
    [uid] = make_users(1)
    barrier = threading.Barrier(8)
    errors = []

    def run():
        barrier.wait()
        try:
            for _ in range(5):
                give(uid, 30)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert stored(uid) == (level_for_experience(1200), 1200)


def test_level_api_unchanged(client):
    [uid] = make_users(1)
    assert client.get("/users/me/level", headers=headers(uid)).json() == {"level": 1, "total_experience": 0}
    give(uid, 350)
    assert client.get("/users/me/level", headers=headers(uid)).json() == {"level": 3, "total_experience": 350}
    assert client.get("/users/me/level").status_code == 401


def test_no_public_grant_api(client):
    [uid] = make_users(1)
    for method in ("post", "put", "patch"):
        response = getattr(client, method)("/users/me/level", headers=headers(uid), json={"amount": 100})
        assert response.status_code == 405
    assert "experience" not in str(client.get("/openapi.json").json()["paths"])
    assert stored(uid) is None
