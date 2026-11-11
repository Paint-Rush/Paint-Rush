import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app import levels
from app.database import SessionLocal, engine
from app.game_room import RoomStatus, START_COST, get_player_room
from app.levels import read_level, save_level
from app.main import app
from app.models import Match, MatchPlayerResult, User, UserLevel
from tests.test_action_events import card_message
from tests.test_game_engine import client, now, receive_until
from tests.test_user_deck import CUSTOM, headers, join, put
from tests.test_websocket import WS_URL, auth, clear_state, users, wait_until

URL = "/users/me/level"
DEFAULT = {"level": 1, "total_experience": 0}


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def get(client, user_id):
    return client.get(URL, headers=headers(user_id))


def save(user_id, level, total_experience):
    with SessionLocal() as db:
        return save_level(db, user_id, level, total_experience)


def rows():
    with SessionLocal() as db:
        return {r.user_id: (r.level, r.total_experience) for r in db.scalars(select(UserLevel))}


def test_table_created(client):
    inspector = inspect(engine)
    assert inspector.has_table("user_levels")
    columns = {c["name"]: c for c in inspector.get_columns("user_levels")}
    assert set(columns) == {"user_id", "level", "total_experience"}
    assert not columns["level"]["nullable"] and not columns["total_experience"]["nullable"]
    assert inspector.get_pk_constraint("user_levels")["constrained_columns"] == ["user_id"]
    assert inspector.get_foreign_keys("user_levels")[0]["referred_table"] == "users"
    checks = {c["name"] for c in inspector.get_check_constraints("user_levels")}
    assert checks == {"ck_user_levels_level", "ck_user_levels_total_experience"}


def test_server_defaults(client, users):
    with SessionLocal() as db:
        db.execute(text("INSERT INTO user_levels (user_id) VALUES (:id)"), {"id": users[0]})
        db.commit()
    assert rows() == {users[0]: (1, 0)}


@pytest.mark.parametrize("column, value", [("level", 0), ("level", -1), ("total_experience", -1)])
def test_db_rejects_invalid_values(client, users, column, value):
    with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            db.execute(text(f"INSERT INTO user_levels (user_id, {column}) VALUES (:id, :v)"), {"id": users[0], "v": value})
            db.commit()
    assert rows() == {}


def test_default_level(client, users):
    r = get(client, users[0])
    assert r.status_code == 200
    assert r.json() == DEFAULT
    assert rows() == {}


def test_registered_account_default(client):
    r = client.post("/api/v1/auth/register", json={"username": "Leveler", "password": "password1234"})
    assert r.status_code == 201
    token = client.post("/api/v1/auth/login", json={"username": "leveler", "password": "password1234"}).json()
    r = client.get(URL, headers={"Authorization": f"Bearer {token['access_token']}"})
    assert r.status_code == 200 and r.json() == DEFAULT


def test_save_and_reload(client, users):
    assert save(users[0], 5, 1234) == {"level": 5, "total_experience": 1234}
    assert rows() == {users[0]: (5, 1234)}
    assert get(client, users[0]).json() == {"level": 5, "total_experience": 1234}
    with SessionLocal() as db:
        assert read_level(db, users[0]) == {"level": 5, "total_experience": 1234}


def test_update_existing_record(client, users):
    save(users[0], 2, 10)
    save(users[0], 3, 0)
    save(users[0], 1, 999)
    assert rows() == {users[0]: (1, 999)}


def test_level_and_experience_not_linked(client, users):
    save(users[0], 1, 100000)
    save(users[1], 50, 0)
    assert get(client, users[0]).json() == {"level": 1, "total_experience": 100000}
    assert get(client, users[1]).json() == {"level": 50, "total_experience": 0}


@pytest.mark.parametrize("level", [0, -1, -100])
def test_invalid_level_rejected(client, users, level):
    save(users[0], 2, 5)
    with pytest.raises(ValueError):
        save(users[0], level, 5)
    assert rows() == {users[0]: (2, 5)}


@pytest.mark.parametrize("experience", [-1, -1000])
def test_negative_experience_rejected(client, users, experience):
    with pytest.raises(ValueError):
        save(users[0], 1, experience)
    assert rows() == {}


@pytest.mark.parametrize("level, experience", [
    (True, 0), (1, False), (1.0, 0), (1, 2.5), ("1", 0), (1, "0"), (None, 0), (1, None), ([1], 0), (2**31, 0), (1, 2**31),
])
def test_invalid_types_rejected(client, users, level, experience):
    with pytest.raises(ValueError):
        save(users[0], level, experience)
    assert rows() == {}


@pytest.mark.parametrize("user_id", [9999, -1, 0, "1", None, True])
def test_unknown_user_rejected(client, users, user_id):
    with pytest.raises(ValueError):
        save(user_id, 2, 10)
    assert rows() == {}


def test_users_independent(client, users):
    save(users[0], 3, 30)
    save(users[1], 7, 700)
    assert get(client, users[0]).json() == {"level": 3, "total_experience": 30}
    assert get(client, users[1]).json() == {"level": 7, "total_experience": 700}
    assert get(client, users[2]).json() == DEFAULT


def test_jwt_required(client, users):
    save(users[0], 4, 40)
    for h in [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": headers(users[0])["Authorization"].removeprefix("Bearer ")},
        headers(users[0], expires=timedelta(seconds=-1)),
        headers(users[0], key="wrong-secret-key-wrong-secret-key"),
        headers(9999),
    ]:
        r = client.get(URL, headers=h)
        assert r.status_code == 401
        assert "level" not in r.json()


def test_no_other_account_access(client, users):
    save(users[1], 9, 900)
    assert get(client, users[0]).json() == DEFAULT
    for path in (f"/users/{users[1]}/level", "/users/level"):
        assert client.get(path, headers=headers(users[0])).status_code == 404
    assert client.get(URL, params={"user_id": users[1]}, headers=headers(users[0])).json() == DEFAULT


@pytest.mark.parametrize("method", ["put", "patch", "post", "delete"])
def test_no_public_write_api(client, users, method):
    r = client.request(method.upper(), URL, json={"level": 99, "total_experience": 99}, headers=headers(users[0]))
    assert r.status_code == 405
    assert rows() == {}


def test_save_failure_rolls_back(client, users, monkeypatch):
    save(users[0], 2, 20)

    def fail(self):
        raise OperationalError("commit", {}, Exception("db down"))

    monkeypatch.setattr(Session, "commit", fail)
    with pytest.raises(OperationalError):
        save(users[0], 3, 30)
    with pytest.raises(OperationalError):
        save(users[1], 3, 30)
    monkeypatch.undo()
    assert rows() == {users[0]: (2, 20)}
    assert save(users[1], 3, 30) == {"level": 3, "total_experience": 30}


def test_read_failure_returns_500(client, users, monkeypatch):
    def fail(db, user_id):
        raise OperationalError("select", {}, Exception("db down"))

    monkeypatch.setattr(levels, "read_level", fail)
    r = get(client, users[0])
    assert r.status_code == 500
    assert set(r.json()) == {"detail"}


def test_no_duplicate_records(client, users):
    for i in range(5):
        save(users[0], i + 1, i * 10)
    with SessionLocal() as db:
        assert db.scalar(text("SELECT COUNT(*) FROM user_levels WHERE user_id = :id"), {"id": users[0]}) == 1
        with pytest.raises(IntegrityError):
            db.add(UserLevel(user_id=users[0], level=1, total_experience=0))
            db.commit()


def test_concurrent_saves_consistent(client, users):
    values = [(i + 1, i * 100) for i in range(8)]
    barrier = threading.Barrier(len(values) * 2)
    errors = []

    def run(user_id, level, experience):
        barrier.wait()
        try:
            save(user_id, level, experience)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run, args=(uid, *v)) for uid in users[:2] for v in values]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    stored = rows()
    assert set(stored) == {users[0], users[1]}
    for uid in users[:2]:
        assert stored[uid] in values


def test_persists_after_restart(client, users):
    save(users[0], 6, 600)
    with TestClient(app) as restarted:
        assert get(restarted, users[0]).json() == {"level": 6, "total_experience": 600}
    with SessionLocal() as db:
        assert db.get(User, users[0]) is not None


def test_game_finish_does_not_change_level(client, users, now):
    save(users[0], 4, 44)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        join(ws2)
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        ws1.send_json(card_message(room, users[0], 0))
        receive_until(ws1, "hand_updated")
        now[0] = 310
        result = receive_until(ws1, "game_result")
        receive_until(ws2, "game_result")
        assert wait_until(lambda: room.result_saved)
        assert "level" not in str(result) and "experience" not in str(result)
    assert rows() == {users[0]: (4, 44)}
    assert get(client, users[1]).json() == DEFAULT
    with SessionLocal() as db:
        assert db.get(Match, room.room_id) is not None
        assert len(db.scalars(select(MatchPlayerResult).where(MatchPlayerResult.match_id == room.room_id)).all()) == 2
    assert set(c.key for c in MatchPlayerResult.__table__.columns).isdisjoint({"level", "total_experience", "experience"})


def test_cost_independent_from_experience(client, users, now):
    save(users[0], 9, 5000)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        join(ws2)
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        a, b = room.players[users[0]], room.players[users[1]]
        assert a.cost == b.cost == START_COST
        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        ws1.send_json(card_message(room, users[0], 0))
        receive_until(ws1, "hand_updated")
        assert a.cost < START_COST
        before = a.cost, a.cost_used, b.cost
        save(users[0], 10, 9999)
        assert (a.cost, a.cost_used, b.cost) == before
        assert a.cost_used == START_COST - a.cost
    assert rows() == {users[0]: (10, 9999)}


def test_profile_and_deck_apis_unchanged(client, users):
    save(users[0], 3, 33)
    assert put(client, users[0], CUSTOM).status_code == 200
    assert client.get("/users/me/deck", headers=headers(users[0])).json() == {"cards": CUSTOM}
    r = client.patch("/users/me/profile", json={"display_name": "Lv3"}, headers=headers(users[0]))
    assert r.status_code == 200
    body = client.get("/users/me/profile", headers=headers(users[0])).json()
    assert set(body) == {"user_id", "username", "display_name", "preferred_character_id"}
    assert body["display_name"] == "Lv3"
    assert get(client, users[0]).json() == {"level": 3, "total_experience": 33}
