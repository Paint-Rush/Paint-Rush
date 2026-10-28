import asyncio
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import game_room, maps, matchmaking, websocket
from app.database import Base, SessionLocal, engine
from app.game_room import MAP_SIZE, START_COST, get_player_room
from app.main import app
from app.maps import load_map
from app.models import User

WS_URL = "/ws/game"


def clear_state():
    game_room.rooms.clear()
    game_room.player_rooms.clear()
    matchmaking.waiting_queue.clear()
    websocket.connections.clear()


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    clear_state()
    with TestClient(app) as c:
        yield c
    clear_state()


@pytest.fixture
def users(client):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(5)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def make_token(user_id, expires=timedelta(hours=1), key=None, **extra):
    payload = {"sub": str(user_id), "exp": datetime.now(timezone.utc) + expires, **extra}
    return jwt.encode(payload, key or os.environ["JWT_SECRET_KEY"], algorithm="HS256")


def auth(ws, user_id):
    ws.send_json({"type": "auth", "token": make_token(user_id)})
    return ws.receive_json()


def wait_until(check):
    for _ in range(200):
        if check():
            return True
        time.sleep(0.01)
    return check()


def expect_close(ws, code):
    with pytest.raises(WebSocketDisconnect) as e:
        ws.receive_json()
    assert e.value.code == code


def join_and_match(ws1, ws2):
    ws1.send_json({"type": "queue_join"})
    assert ws1.receive_json()["type"] == "queue_waiting"
    ws2.send_json({"type": "queue_join"})
    assert ws2.receive_json()["type"] == "queue_waiting"
    return ws1.receive_json(), ws2.receive_json()


def test_auth_success_with_login_token(client):
    data = {"username": "painter123", "password": "password1234"}
    user_id = client.post("/api/v1/auth/register", json=data).json()["id"]
    token = client.post("/api/v1/auth/login", json=data).json()["access_token"]

    with client.websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "auth", "token": token})
        assert ws.receive_json() == {"type": "auth_success", "user_id": user_id}
        assert websocket.is_connected(user_id)


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    lambda uid: make_token(uid, key="another-secret-key-for-testing-only"),
    lambda uid: make_token(uid + 1000),
    lambda uid: jwt.encode({"sub": str(uid)}, os.environ["JWT_SECRET_KEY"], algorithm="HS256"),
    lambda uid: jwt.encode({"exp": time.time() + 60}, os.environ["JWT_SECRET_KEY"], algorithm="HS256"),
    lambda uid: make_token("abc"),
    lambda uid: jwt.encode({"sub": str(uid), "exp": time.time() + 60}, os.environ["JWT_SECRET_KEY"], algorithm="HS512"),
    123,
])
def test_reject_invalid_token(client, users, token):
    token = token(users[0]) if callable(token) else token
    with client.websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "auth", "token": token, "user_id": users[0]})
        assert ws.receive_json()["code"] == "auth_failed"
        expect_close(ws, 1008)
    assert websocket.connections == {}


def test_reject_expired_token(client, users):
    with client.websocket_connect(WS_URL) as ws:
        ws.send_json({"type": "auth", "token": make_token(users[0], expires=timedelta(seconds=-1))})
        assert ws.receive_json()["code"] == "auth_failed"
        expect_close(ws, 1008)


@pytest.mark.parametrize("first", [{"type": "queue_join"}, {"type": "room_state"}, "{broken", "[]"])
def test_reject_request_before_auth(client, users, first):
    with client.websocket_connect(WS_URL) as ws:
        if isinstance(first, dict):
            ws.send_json(first)
        else:
            ws.send_text(first)
        assert ws.receive_json()["code"] == "auth_required"
        expect_close(ws, 1008)
    assert matchmaking.get_waiting_players() == []


def test_auth_timeout(client, monkeypatch):
    monkeypatch.setattr(websocket, "AUTH_TIMEOUT", 0.1)
    with client.websocket_connect(WS_URL) as ws:
        expect_close(ws, 1008)


def test_single_user_waits(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        ws.send_json({"type": "queue_join"})
        assert ws.receive_json() == {"type": "queue_waiting", "waiting": 1, "match_size": 2}
        assert matchmaking.get_waiting_players() == [users[0]]
        assert game_room.rooms == {}


def test_two_users_match(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)

        room = get_player_room(users[0])
        assert len(game_room.rooms) == 1
        assert room is get_player_room(users[1])
        assert room.player_order == users[:2]
        assert matchmaking.get_waiting_players() == []


def test_both_receive_match_found(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        msg1, msg2 = join_and_match(ws1, ws2)

        room = get_player_room(users[0])
        assert msg1 == msg2
        assert msg1 == {
            "type": "match_found",
            "room_id": str(room.room_id),
            "map_id": room.map_id,
            "players": [
                {
                    "user_id": uid,
                    "color": room.players[uid].color.value,
                    "spawn": list(room.players[uid].spawn),
                    "character_id": room.players[uid].character_id,
                }
                for uid in users[:2]
            ],
        }
        assert all(p.connected for p in room.players.values())


@pytest.mark.parametrize("size", [3, 4])
def test_match_size_setting(client, users, monkeypatch, size):
    monkeypatch.setattr(matchmaking, "MATCH_SIZE", size)
    sockets = [client.websocket_connect(WS_URL) for _ in range(size)]
    try:
        for ws, uid in zip(sockets, users):
            ws.__enter__()
            auth(ws, uid)
        for ws in sockets:
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        messages = [ws.receive_json() for ws in sockets]
    finally:
        for ws in sockets:
            ws.__exit__(None, None, None)

    assert all(m["type"] == "match_found" and len(m["players"]) == size for m in messages)
    assert len(game_room.rooms) == 1


def test_room_state(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])

        ws1.send_json({"type": "room_state"})
        state = ws1.receive_json()

    map_data = load_map("default")
    assert state["type"] == "room_state"
    assert state["room_id"] == str(room.room_id)
    assert state["status"] == "READY"
    assert state["map_id"] == "default"
    assert state["map"] == {"width": MAP_SIZE, "height": MAP_SIZE, "tiles": map_data["tiles"]}
    assert state["my_cost"] == START_COST
    assert state["tile_owners"] == [[None] * MAP_SIZE for _ in range(MAP_SIZE)]
    assert state["started_at"] is None
    assert [p["user_id"] for p in state["players"]] == users[:2]
    for p in state["players"]:
        player = room.players[p["user_id"]]
        assert p == {
            "user_id": player.user_id,
            "color": player.color.value,
            "character_id": player.character_id,
            "spawn": list(player.spawn),
            "position": list(player.position),
            "alive": True,
            "connected": True,
            "tile_count": 0,
        }


def test_room_state_without_room(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        ws.send_json({"type": "room_state"})
        assert ws.receive_json()["code"] == "not_in_room"


def test_queue_cancel(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        ws.send_json({"type": "queue_join"})
        ws.receive_json()
        ws.send_json({"type": "queue_cancel"})
        assert ws.receive_json() == {"type": "queue_cancelled"}
        assert matchmaking.get_waiting_players() == []

        ws.send_json({"type": "queue_cancel"})
        assert ws.receive_json()["code"] == "not_in_queue"


def test_disconnect_while_waiting_leaves_queue(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        ws.send_json({"type": "queue_join"})
        ws.receive_json()

    assert wait_until(lambda: matchmaking.get_waiting_players() == [])
    assert not websocket.is_connected(users[0])


def test_disconnect_in_game_keeps_room(client, users):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            auth(ws2, users[1])
            join_and_match(ws1, ws2)
            room = get_player_room(users[0])

        assert ws2.receive_json() == {"type": "player_disconnected", "user_id": users[0]}
        assert get_player_room(users[0]) is room
        assert game_room.rooms == {room.room_id: room}
        assert room.players[users[0]].connected is False
        assert room.players[users[1]].connected is True


def test_reconnect_restores_room(client, users):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            auth(ws2, users[1])
            join_and_match(ws1, ws2)
            room = get_player_room(users[0])
        ws2.receive_json()

        with client.websocket_connect(WS_URL) as ws1:
            assert auth(ws1, users[0]) == {"type": "auth_success", "user_id": users[0]}
            state = ws1.receive_json()
            assert state["type"] == "room_state"
            assert state["room_id"] == str(room.room_id)
            assert all(p["connected"] for p in state["players"])
            assert ws2.receive_json() == {"type": "player_reconnected", "user_id": users[0]}
            assert room.players[users[0]].connected is True

            ws1.send_json({"type": "queue_join"})
            assert ws1.receive_json()["code"] == "already_in_game"

        assert len(game_room.rooms) == 1
        assert matchmaking.get_waiting_players() == []


def test_duplicate_connection_replaces_old(client, users):
    with client.websocket_connect(WS_URL) as old:
        auth(old, users[0])
        with client.websocket_connect(WS_URL) as new:
            assert auth(new, users[0])["type"] == "auth_success"
            assert old.receive_json() == {"type": "session_replaced"}
            expect_close(old, websocket.SESSION_REPLACED)

            new.send_json({"type": "room_state"})
            assert new.receive_json()["code"] == "not_in_room"
            assert len(websocket.connections) == 1


def test_old_connection_close_does_not_affect_new_in_queue(client, users):
    with client.websocket_connect(WS_URL) as new:
        with client.websocket_connect(WS_URL) as old:
            auth(old, users[0])
            old.send_json({"type": "queue_join"})
            old.receive_json()
            auth(new, users[0])
            old.receive_json()
        time.sleep(0.1)

        assert websocket.is_connected(users[0])
        assert matchmaking.get_waiting_players() == [users[0]]
        new.send_json({"type": "queue_cancel"})
        assert new.receive_json() == {"type": "queue_cancelled"}


def test_old_connection_close_does_not_affect_new_in_game(client, users):
    with client.websocket_connect(WS_URL) as ws2, client.websocket_connect(WS_URL) as new:
        with client.websocket_connect(WS_URL) as old:
            auth(old, users[0])
            auth(ws2, users[1])
            join_and_match(old, ws2)
            room = get_player_room(users[0])

            auth(new, users[0])
            assert new.receive_json()["type"] == "room_state"
            old.receive_json()
        time.sleep(0.1)

        assert room.players[users[0]].connected is True
        assert websocket.is_connected(users[0])
        ws2.send_json({"type": "room_state"})
        assert ws2.receive_json()["type"] == "room_state"


def test_invalid_messages(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        for text, code in [
            ("{broken", "invalid_json"),
            ("[1, 2]", "invalid_json"),
            ('"text"', "invalid_json"),
            ("{}", "unknown_type"),
            ('{"type": "dance"}', "unknown_type"),
            ('{"type": ["queue_join"]}', "unknown_type"),
            ('{"type": "auth", "token": "x"}', "already_authenticated"),
        ]:
            ws.send_text(text)
            assert ws.receive_json()["code"] == code

        ws.send_json({"type": "queue_join"})
        assert ws.receive_json()["type"] == "queue_waiting"
        ws.send_json({"type": "queue_join"})
        assert ws.receive_json()["code"] == "already_in_queue"
        assert matchmaking.get_waiting_players() == [users[0]]


def test_concurrent_queue_join(client, users):
    sockets = [client.websocket_connect(WS_URL) for _ in users]
    try:
        for ws, uid in zip(sockets, users):
            ws.__enter__()
            auth(ws, uid)
        barrier = threading.Barrier(len(sockets))

        def join(ws):
            barrier.wait()
            ws.send_json({"type": "queue_join"})

        threads = [threading.Thread(target=join, args=(ws,)) for ws in sockets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert wait_until(lambda: len(matchmaking.get_waiting_players()) + 2 * len(game_room.rooms) == len(users))
        waiting = matchmaking.get_waiting_players()
    finally:
        for ws in sockets:
            ws.__exit__(None, None, None)

    matched = [uid for room in game_room.rooms.values() for uid in room.player_order]
    assert len(game_room.rooms) == 2
    assert len(matched) == len(set(matched)) == 4
    assert len(waiting) == 1
    assert set(matched) | set(waiting) == set(users)
    assert all(game_room.player_rooms[uid] for uid in matched)


def test_private_info_hidden(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        room.players[users[0]].cost = 7
        room.players[users[1]].cost = 9
        room.players[users[1]].deck = ["secret_card"]

        ws1.send_json({"type": "room_state"})
        state = ws1.receive_json()

    text = json.dumps(state)
    assert state["my_cost"] == 7
    assert "secret_card" not in text
    assert "deck" not in text
    assert "token" not in text
    for p in state["players"]:
        assert "cost" not in p


def test_invalid_map_data(client, users, tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "MAPS_DIR", tmp_path)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)

        small = {"map_id": "default", "name": "Small", "width": 10, "height": 10,
                 "tiles": [["NORMAL"] * 10 for _ in range(10)]}
        for content in [None, "{broken", json.dumps(small)]:
            if content is not None:
                (tmp_path / "default.json").write_text(content, encoding="utf-8")
            ws1.send_json({"type": "room_state"})
            assert ws1.receive_json()["code"] == "map_unavailable"

        ws2.send_json({"type": "queue_cancel"})
        assert ws2.receive_json()["code"] == "not_in_queue"
    assert len(game_room.rooms) == 1


class BrokenSocket:
    async def send_json(self, message):
        raise RuntimeError("connection lost")


def test_send_failure_does_not_break_match(client, users):
    assert asyncio.run(websocket.send_to(users[0], {"type": "ping"})) is False

    websocket.connections[users[0]] = BrokenSocket()
    matchmaking.join_queue(users[0])
    assert asyncio.run(websocket.send_to(users[0], {"type": "ping"})) is False

    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[1])
        ws.send_json({"type": "queue_join"})
        assert ws.receive_json()["type"] == "queue_waiting"
        assert ws.receive_json()["type"] == "match_found"

    room = get_player_room(users[0])
    assert room is get_player_room(users[1])
    assert room.players[users[0]].connected is True
