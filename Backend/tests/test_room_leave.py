import threading
import time

import pytest

from app import game_room, matchmaking, websocket
from app.database import SessionLocal
from app.game_room import START_COST, RoomStatus, create_room, get_player_room, leave_room, remove_room
from app.models import User
from app.results import load_match_record
from tests.test_game_engine import client, now, receive_until
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, wait_until


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def users(client):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(3)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def stored(room):
    with SessionLocal() as db:
        return load_match_record(db, room.room_id)


def leave(ws):
    ws.send_json({"type": "room_leave"})
    return ws.receive_json()


def test_leave_unit_keeps_other_mapping():
    old = create_room([1, 2])
    assert leave_room(1) is old
    assert get_player_room(1) is None
    assert get_player_room(2) is old
    new = create_room([1, 3])
    assert leave_room(2) is old
    assert old.room_id not in game_room.rooms
    assert get_player_room(1) is new
    assert get_player_room(3) is new
    assert leave_room(2) is None

    remove_room(old.room_id)
    assert get_player_room(1) is new


def test_leave_without_room(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        reply = leave(ws)
        assert reply["type"] == "error"
        assert reply["code"] == "not_in_room"


def test_leave_rejected_before_finished(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        for t, status in [(0, RoomStatus.READY), (10, RoomStatus.PLAYING), (255, RoomStatus.DEATHMATCH)]:
            now[0] = t
            assert wait_until(lambda: room.status is status)
            ws1.send_json({"type": "room_leave"})
            assert receive_until(ws1, "error")["code"] == "game_not_finished"
            assert get_player_room(users[0]) is room
            assert game_room.rooms == {room.room_id: room}


def test_leave_without_result(client, users, now, monkeypatch):
    def broken(room):
        raise ValueError("boom")

    monkeypatch.setattr(websocket, "calculate_results", broken)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        now[0] = 310
        receive_until(ws1, "game_finished")
        assert wait_until(lambda: not websocket.timers)
        ws1.send_json({"type": "room_leave"})
        assert receive_until(ws1, "error")["code"] == "result_not_available"
        assert get_player_room(users[0]) is room


def test_leave_and_rematch(client, users, now):
    a, b, c = users
    with client.websocket_connect(WS_URL) as ws_a, client.websocket_connect(WS_URL) as ws_b, \
            client.websocket_connect(WS_URL) as ws_c:
        auth(ws_a, a)
        auth(ws_b, b)
        auth(ws_c, c)
        join_and_match(ws_a, ws_b)
        old = get_player_room(a)
        with websocket.state_lock:
            old.players[a].kills = 2
            old.players[a].cards_used = 4
            old.tile_owners[0][0] = a
            old.tile_counts[a] = 1
        now[0] = 310
        result = receive_until(ws_a, "game_result")
        receive_until(ws_b, "game_result")
        assert wait_until(lambda: old.result_saved)
        assert wait_until(lambda: not websocket.timers)
        record = stored(old)

        assert leave(ws_a) == {"type": "room_left", "room_id": str(old.room_id)}
        assert get_player_room(a) is None
        assert get_player_room(b) is old
        assert game_room.rooms == {old.room_id: old}
        assert websocket.is_connected(a)
        assert old.player_order == [a, b]
        assert set(old.players) == {a, b}
        assert old.result == {k: v for k, v in result.items() if k != "type"}

        ws_b.send_json({"type": "room_state"})
        assert receive_until(ws_b, "room_state")["status"] == "FINISHED"

        ws_a.send_json({"type": "queue_join"})
        assert ws_a.receive_json()["type"] == "queue_waiting"
        ws_c.send_json({"type": "queue_join"})
        assert ws_c.receive_json()["type"] == "queue_waiting"
        assert ws_a.receive_json()["type"] == "match_found"
        assert ws_a.receive_json()["type"] == "game_ready"

        new = get_player_room(a)
        assert new is get_player_room(c)
        assert new is not old
        assert new.player_order == [a, c]
        assert new.status is RoomStatus.READY
        assert new.ready_at == 310
        assert new.result is None and new.result_saved is False
        assert websocket.timers.keys() == {new.room_id}
        player = new.players[a]
        assert player.cost == START_COST
        assert len(player.hand) == 3 and len(player.draw_queue) == 3
        assert player.hand is not old.players[a].hand
        assert (player.kills, player.deaths, player.cards_used, player.cost_used, player.total_painted) == (0, 0, 0, 0, 0)
        assert new.tile_owners[0][0] is None
        assert new.tile_counts == {a: 0, c: 0}

        assert leave(ws_b) == {"type": "room_left", "room_id": str(old.room_id)}
        assert old.room_id not in game_room.rooms
        assert get_player_room(a) is new
        assert get_player_room(c) is new
        assert get_player_room(b) is None
        assert stored(old) == record

        ws_b.send_json({"type": "queue_join"})
        assert ws_b.receive_json() == {"type": "queue_waiting", "waiting": 1, "match_size": 2}
        assert matchmaking.get_waiting_players() == [b]


def test_save_retry_on_leave(client, users, now, monkeypatch):
    original = websocket.store_result
    failing = [True]

    def flaky(room):
        if failing[0]:
            raise RuntimeError("db down")
        return original(room)

    monkeypatch.setattr(websocket, "store_result", flaky)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        now[0] = 310
        receive_until(ws1, "game_result")
        assert wait_until(lambda: not websocket.timers)
        assert room.result_saved is False
        result = room.result

        assert leave(ws1)["code"] == "result_save_failed"
        assert get_player_room(users[0]) is room
        assert room.result is result
        assert room.result_saved is False
        assert stored(room) is None

        failing[0] = False
        assert leave(ws1) == {"type": "room_left", "room_id": str(room.room_id)}
        assert room.result_saved is True
        assert stored(room)[0]["winner_id"] == result["winner_id"]
        assert get_player_room(users[1]) is room


def test_concurrent_leave(client, users, now, monkeypatch):
    original = websocket.store_result
    calls = []
    failing = [True]

    def slow(room):
        calls.append(room.room_id)
        if failing[0]:
            raise RuntimeError("db down")
        time.sleep(0.1)
        return original(room)

    monkeypatch.setattr(websocket, "store_result", slow)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        now[0] = 310
        receive_until(ws1, "game_result")
        receive_until(ws2, "game_result")
        assert wait_until(lambda: not websocket.timers)
        failing[0] = False

        barrier = threading.Barrier(2)

        def send(ws):
            barrier.wait()
            ws.send_json({"type": "room_leave"})

        threads = [threading.Thread(target=send, args=(ws,)) for ws in (ws1, ws2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        expected = {"type": "room_left", "room_id": str(room.room_id)}
        assert ws1.receive_json() == expected
        assert ws2.receive_json() == expected

    assert room.room_id not in game_room.rooms
    assert game_room.player_rooms == {}
    assert room.result_saved is True
    match, players = stored(room)
    assert len(players) == 2


def test_duplicate_leave(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        now[0] = 310
        receive_until(ws1, "game_result")
        assert wait_until(lambda: room.result_saved)

        assert leave(ws1)["type"] == "room_left"
        for _ in range(3):
            assert leave(ws1)["code"] == "not_in_room"
        assert get_player_room(users[1]) is room
        assert game_room.rooms == {room.room_id: room}


def test_reconnect_after_finish(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            auth(ws2, users[1])
            join_and_match(ws1, ws2)
            room = get_player_room(users[0])
            now[0] = 310
            result = receive_until(ws1, "game_result")
            assert wait_until(lambda: room.result_saved)
            assert leave(ws1)["type"] == "room_left"

        with client.websocket_connect(WS_URL) as ws1:
            assert auth(ws1, users[0])["type"] == "auth_success"
            ws1.send_json({"type": "room_state"})
            assert ws1.receive_json()["code"] == "not_in_room"
            assert get_player_room(users[0]) is None

    with client.websocket_connect(WS_URL) as ws2:
        assert auth(ws2, users[1])["type"] == "auth_success"
        state = ws2.receive_json()
        assert state["type"] == "room_state"
        assert state["status"] == "FINISHED"
        assert ws2.receive_json() == result
        assert get_player_room(users[1]) is room
