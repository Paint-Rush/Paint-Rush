import threading
from collections import Counter
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import matchmaking, profiles
from app.database import SessionLocal
from app.game_room import DEFAULT_CHARACTER_ID, RoomStatus, get_player_room
from app.main import app
from app.matchmaking import get_waiting_players
from app.models import User, UserProfile
from tests.test_game_engine import client, now, receive_until
from tests.test_user_deck import BOMBS, CUSTOM, all_cards, headers, join, put
from tests.test_websocket import WS_URL, auth, clear_state, users, wait_until

URL = "/users/me/profile"


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def get(client, user_id):
    return client.get(URL, headers=headers(user_id))


def patch(client, user_id, body):
    return client.patch(URL, json=body, headers=headers(user_id))


def stored(user_id):
    with SessionLocal() as db:
        row = db.get(UserProfile, user_id)
        return (row.display_name, row.preferred_character_id) if row else None


def username(user_id):
    with SessionLocal() as db:
        return db.get(User, user_id).username


def profile(user_id, display_name=None, character_id=DEFAULT_CHARACTER_ID):
    name = username(user_id)
    return {
        "user_id": user_id,
        "username": name,
        "display_name": display_name or name,
        "preferred_character_id": character_id,
    }


def test_default_profile(client, users):
    r = get(client, users[0])
    assert r.status_code == 200
    assert r.json() == profile(users[0])
    assert stored(users[0]) is None


def test_registered_account_default_profile(client):
    r = client.post("/api/v1/auth/register", json={"username": "Painter01", "password": "password1234"})
    assert r.status_code == 201
    user_id = r.json()["id"]
    token = client.post("/api/v1/auth/login", json={"username": "painter01", "password": "password1234"}).json()
    auth_header = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.get(URL, headers=auth_header).json() == {
        "user_id": user_id,
        "username": "painter01",
        "display_name": "painter01",
        "preferred_character_id": DEFAULT_CHARACTER_ID,
    }
    r = client.patch(URL, json={"display_name": "Painter"}, headers=auth_header)
    assert r.status_code == 200 and r.json()["display_name"] == "Painter"


def test_update_profile(client, users):
    r = patch(client, users[0], {"display_name": "NewPlayer", "preferred_character_id": "character_02"})
    assert r.status_code == 200
    assert r.json() == profile(users[0], "NewPlayer", "character_02")
    assert stored(users[0]) == ("NewPlayer", "character_02")
    assert get(client, users[0]).json() == profile(users[0], "NewPlayer", "character_02")


def test_update_display_name_only(client, users):
    assert patch(client, users[0], {"display_name": "닉네임"}).json() == profile(users[0], "닉네임")
    assert stored(users[0]) == ("닉네임", DEFAULT_CHARACTER_ID)


def test_update_character_only(client, users):
    assert patch(client, users[0], {"preferred_character_id": "character_02"}).json() == profile(users[0], None, "character_02")
    assert stored(users[0]) == (username(users[0]), "character_02")


def test_partial_update_keeps_other_field(client, users):
    patch(client, users[0], {"display_name": "First", "preferred_character_id": "character_02"})
    assert patch(client, users[0], {"display_name": "Second"}).json() == profile(users[0], "Second", "character_02")
    assert patch(client, users[0], {"preferred_character_id": "character_01"}).json() == profile(users[0], "Second", "character_01")
    with SessionLocal() as db:
        assert len(db.scalars(select(UserProfile)).all()) == 1


@pytest.mark.parametrize("character_id", ["ghost", "", "CHARACTER_01", None, 1, ["character_01"]])
def test_invalid_character_rejected(client, users, character_id):
    patch(client, users[0], {"display_name": "Keep"})
    r = patch(client, users[0], {"preferred_character_id": character_id})
    assert r.status_code == 422
    assert stored(users[0]) == ("Keep", DEFAULT_CHARACTER_ID)


@pytest.mark.parametrize("name", ["", " ", "     ", "\t\n", None, 123, ["name"]])
def test_blank_or_invalid_name_rejected(client, users, name):
    r = patch(client, users[0], {"display_name": name})
    assert r.status_code == 422
    assert stored(users[0]) is None


def test_display_name_length(client, users):
    assert patch(client, users[0], {"display_name": "a" * 20}).status_code == 200
    assert patch(client, users[0], {"display_name": "가" * 20}).status_code == 200
    assert patch(client, users[0], {"display_name": "a" * 21}).status_code == 422
    assert patch(client, users[0], {"display_name": "  padded  "}).json()["display_name"] == "padded"
    assert stored(users[0])[0] == "padded"


@pytest.mark.parametrize("body", [{}, [], {"unknown": 1}, {"username": "hacker"}, {"user_id": 2, "display_name": "x"}])
def test_invalid_body_rejected(client, users, body):
    r = patch(client, users[0], body)
    assert r.status_code == 422
    assert stored(users[0]) is None


def test_jwt_required(client, users):
    bad = [
        {},
        {"Authorization": "Bearer nope"},
        headers(users[0], expires=timedelta(seconds=-1)),
        headers(9999),
    ]
    for h in bad:
        assert client.get(URL, headers=h).status_code == 401
        assert client.patch(URL, json={"display_name": "x"}, headers=h).status_code == 401
    with SessionLocal() as db:
        assert db.scalars(select(UserProfile)).all() == []


def test_cannot_change_other_account(client, users):
    patch(client, users[1], {"display_name": "Other"})
    assert patch(client, users[0], {"display_name": "Mine"}).json()["user_id"] == users[0]
    assert stored(users[1]) == ("Other", DEFAULT_CHARACTER_ID)
    for path in (f"/users/{users[1]}/profile", "/users/profile"):
        assert client.patch(path, json={"display_name": "x"}, headers=headers(users[0])).status_code in (404, 405)
    assert stored(users[1]) == ("Other", DEFAULT_CHARACTER_ID)


def test_profiles_independent(client, users):
    patch(client, users[0], {"display_name": "A", "preferred_character_id": "character_02"})
    patch(client, users[1], {"display_name": "B"})
    assert get(client, users[0]).json() == profile(users[0], "A", "character_02")
    assert get(client, users[1]).json() == profile(users[1], "B")
    assert get(client, users[2]).json() == profile(users[2])


def test_profile_survives_restart(client, users):
    patch(client, users[0], {"display_name": "Persist", "preferred_character_id": "character_02"})
    with TestClient(app) as restarted:
        assert get(restarted, users[0]).json() == profile(users[0], "Persist", "character_02")


def test_save_failure_rolls_back(client, users, monkeypatch):
    patch(client, users[0], {"display_name": "Before"})

    def fail(self):
        raise OperationalError("commit", {}, Exception("db down"))

    monkeypatch.setattr(Session, "commit", fail)
    assert patch(client, users[0], {"display_name": "After"}).status_code == 500
    assert patch(client, users[1], {"display_name": "New"}).status_code == 500
    monkeypatch.undo()
    assert stored(users[0]) == ("Before", DEFAULT_CHARACTER_ID)
    assert stored(users[1]) is None
    assert patch(client, users[1], {"display_name": "New"}).status_code == 200


def test_concurrent_patch_keeps_both_fields(client, users):
    barrier = threading.Barrier(2)
    results = []

    def run(body):
        barrier.wait()
        results.append(patch(client, users[0], body).status_code)

    threads = [
        threading.Thread(target=run, args=({"display_name": "Racer"},)),
        threading.Thread(target=run, args=({"preferred_character_id": "character_02"},)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200, 200]
    assert stored(users[0]) == ("Racer", "character_02")


def test_display_name_not_in_game_logic(client, users):
    patch(client, users[0], {"display_name": "Winner"})
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        join(ws2)
        found = receive_until(ws1, "match_found")
        assert "Winner" not in str(found)


def test_queue_join_uses_preferred_character(client, users):
    patch(client, users[0], {"preferred_character_id": "character_02"})
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        assert join(ws1)["type"] == "queue_waiting"
        assert matchmaking.selections == {users[0]: "character_02"}
        assert join(ws2)["type"] == "queue_waiting"
        found = receive_until(ws1, "match_found")
        characters = {p["user_id"]: p["character_id"] for p in found["players"]}
        assert characters == {users[0]: "character_02", users[1]: DEFAULT_CHARACTER_ID}


def test_explicit_character_overrides_preference(client, users):
    patch(client, users[0], {"preferred_character_id": "character_02"})
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        assert join(ws1, character_id="character_01")["type"] == "queue_waiting"
        assert join(ws2, character_id="character_02")["type"] == "queue_waiting"
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        assert room.players[users[0]].character_id == "character_01"
        assert room.players[users[1]].character_id == "character_02"
    assert stored(users[0]) == (username(users[0]), "character_02")
    assert stored(users[1]) is None


@pytest.mark.parametrize("character_id", ["ghost", None, 1])
def test_explicit_invalid_character_still_rejected(client, users, character_id):
    patch(client, users[0], {"preferred_character_id": "character_02"})
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        assert join(ws, character_id=character_id)["code"] == "unknown_character"
        assert get_waiting_players() == [] and matchmaking.selections == {}


def test_stored_invalid_preference_rejected(client, users):
    with SessionLocal() as db:
        db.add(UserProfile(user_id=users[0], display_name="x", preferred_character_id="removed_character"))
        db.commit()
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        assert join(ws)["code"] == "unknown_character"
        assert join(ws, character_id="character_02")["type"] == "queue_waiting"


def test_profile_load_failure_isolated(client, users, monkeypatch):
    real = profiles.load_preferred_character

    def flaky(user_id):
        if user_id == users[0]:
            raise OperationalError("select", {}, Exception("db down"))
        return real(user_id)

    monkeypatch.setattr(profiles, "load_preferred_character", flaky)
    with (
        client.websocket_connect(WS_URL) as ws1,
        client.websocket_connect(WS_URL) as ws2,
        client.websocket_connect(WS_URL) as ws3,
    ):
        auth(ws1, users[0])
        auth(ws2, users[1])
        auth(ws3, users[2])
        assert join(ws1)["code"] == "profile_unavailable"
        assert join(ws1, character_id="character_02")["type"] == "queue_waiting"
        assert join(ws2)["type"] == "queue_waiting"
        receive_until(ws2, "game_ready")
        assert get_player_room(users[0]).players[users[0]].character_id == "character_02"
        assert get_player_room(users[2]) is None


def test_join_does_not_change_preference(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        join(ws, character_id="character_02")
        ws.send_json({"type": "queue_cancel"})
        assert ws.receive_json() == {"type": "queue_cancelled"}
    assert stored(users[0]) is None
    assert get(client, users[0]).json()["preferred_character_id"] == DEFAULT_CHARACTER_ID


def test_duplicate_join_and_cancel_keep_state(client, users):
    patch(client, users[0], {"preferred_character_id": "character_02"})
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        assert join(ws)["type"] == "queue_waiting"
        assert join(ws)["code"] == "already_in_queue"
        assert join(ws, character_id="character_01")["code"] == "already_in_queue"
        assert matchmaking.selections == {users[0]: "character_02"}
        assert get_waiting_players() == [users[0]]
        ws.send_json({"type": "queue_cancel"})
        assert ws.receive_json() == {"type": "queue_cancelled"}
        assert matchmaking.selections == {} and matchmaking.decks == {}
        assert get_waiting_players() == []


def test_preference_change_while_waiting_keeps_queued_character(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        patch(client, users[0], {"preferred_character_id": "character_02"})
        assert matchmaking.selections == {users[0]: DEFAULT_CHARACTER_ID}
        join(ws2)
        receive_until(ws1, "game_ready")
        assert get_player_room(users[0]).players[users[0]].character_id == DEFAULT_CHARACTER_ID


def test_reconnect_keeps_match_character(client, users, now):
    patch(client, users[0], {"preferred_character_id": "character_02"})
    with client.websocket_connect(WS_URL) as ws2:
        ws1 = client.websocket_connect(WS_URL).__enter__()
        try:
            auth(ws1, users[0])
            auth(ws2, users[1])
            join(ws1)
            join(ws2)
            receive_until(ws1, "game_ready")
            room = get_player_room(users[0])
        finally:
            ws1.__exit__(None, None, None)
        assert wait_until(lambda: not room.players[users[0]].connected)
        patch(client, users[0], {"preferred_character_id": "character_01", "display_name": "Changed"})
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            state = receive_until(ws1, "room_state")
            me = next(p for p in state["players"] if p["user_id"] == users[0])
            assert me["character_id"] == "character_02"
            assert me["ultimate_id"] == "ultimate_02"
            assert room.players[users[0]].character_id == "character_02"


def test_profile_change_keeps_running_game(client, users, now):
    put(client, users[0], CUSTOM)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        join(ws2)
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        ws1.send_json({"type": "ultimate_use"})
        assert receive_until(ws1, "ultimate_activated")["ultimate_id"] == "ultimate_01"
        player = room.players[users[0]]
        before = player.character_id, list(player.deck), list(player.hand), list(player.draw_queue), player.ultimate_used
        assert patch(client, users[0], {"display_name": "Mid", "preferred_character_id": "character_02"}).status_code == 200
        assert put(client, users[0], BOMBS).status_code == 200
        after = player.character_id, list(player.deck), list(player.hand), list(player.draw_queue), player.ultimate_used
        assert after == before
        assert all_cards(player) == Counter(CUSTOM)
        ws1.send_json({"type": "ultimate_use"})
        assert receive_until(ws1, "error")["code"] == "ultimate_already_used"


def test_next_match_uses_updated_preference(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        join(ws2)
        receive_until(ws1, "game_ready")
        old = get_player_room(users[0])
        assert old.players[users[0]].character_id == DEFAULT_CHARACTER_ID
        patch(client, users[0], {"preferred_character_id": "character_02"})
        now[0] = 310
        receive_until(ws1, "game_result")
        receive_until(ws2, "game_result")
        for ws in (ws1, ws2):
            ws.send_json({"type": "room_leave"})
            receive_until(ws, "room_left")
        now[0] = 0
        join(ws1)
        join(ws2)
        receive_until(ws1, "game_ready")
        new = get_player_room(users[0])
        assert new is not old
        assert new.players[users[0]].character_id == "character_02"
        assert new.players[users[0]].ultimate_used is False
        assert old.players[users[0]].character_id == DEFAULT_CHARACTER_ID
