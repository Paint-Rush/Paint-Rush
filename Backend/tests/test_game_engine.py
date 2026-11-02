import asyncio
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import game_engine, game_room, websocket
from app.database import Base, SessionLocal, engine
from app.game_engine import advance, charge_at, spend_cost, start, timer_state
from app.game_room import MAX_COST, START_COST, RoomStatus, create_room, get_player_room, remove_room
from app.main import app
from app.models import User
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, wait_until


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def new_room(user_ids=(1, 2), now=0.0):
    room = create_room(list(user_ids))
    start(room, now)
    return room


def types(events):
    return [message["type"] for _, message in events]


def drain(room, now, start_at, end_at, step=0.01):
    t = start_at
    while t < end_at:
        t = min(end_at, t + step)
        advance(room, now + t)


def test_ready_lasts_ten_seconds():
    room = new_room()
    assert room.status is RoomStatus.READY
    assert advance(room, 9.99) == [(None, {"type": "game_tick", "status": "READY", "remaining_seconds": 1})]
    assert room.status is RoomStatus.READY
    assert room.started_at is None
    assert all(p.cost == START_COST for p in room.players.values())
    assert timer_state(room, 3) == {"ready_remaining_seconds": 7, "remaining_seconds": 300, "max_cost": MAX_COST}


def test_ready_to_playing():
    room = new_room()
    events = advance(room, 10)
    assert room.status is RoomStatus.PLAYING
    assert events == [
        (None, {"type": "game_started", "room_id": str(room.room_id), "duration_seconds": 300}),
        (None, {"type": "game_tick", "status": "PLAYING", "remaining_seconds": 300}),
    ]
    assert room.started_at.tzinfo is timezone.utc
    assert abs((datetime.now(timezone.utc) - room.started_at).total_seconds()) < 1


def test_playing_to_deathmatch():
    room = new_room()
    advance(room, 10)
    advance(room, 249.99)
    assert room.status is RoomStatus.PLAYING
    events = advance(room, 250)
    assert room.status is RoomStatus.DEATHMATCH
    assert events == [
        (None, {"type": "deathmatch_started", "remaining_seconds": 60, "cost_interval": 0.45}),
        (None, {"type": "game_tick", "status": "DEATHMATCH", "remaining_seconds": 60}),
    ]


def test_deathmatch_to_finished():
    room = new_room()
    advance(room, 250)
    advance(room, 309.99)
    assert room.status is RoomStatus.DEATHMATCH
    assert advance(room, 310) == [(None, {"type": "game_finished", "room_id": str(room.room_id)})]
    assert room.status is RoomStatus.FINISHED
    assert game_room.get_room(room.room_id) is room


def test_initial_cost():
    room = new_room([1, 2, 3, 4])
    assert [p.cost for p in room.players.values()] == [6, 6, 6, 6]


def test_max_cost_limit():
    room = new_room()
    advance(room, 10)
    drain(room, 10, 0, 30)
    assert all(p.cost == MAX_COST for p in room.players.values())
    assert not [e for e in advance(room, 50) if e[1]["type"] == "cost_updated"]


def test_playing_cost_interval():
    room = new_room([1, 2])
    advance(room, 10)
    assert advance(room, 10.85) == []
    events = advance(room, 10.86)
    assert events == [
        (1, {"type": "cost_updated", "cost": 7, "max_cost": 10}),
        (2, {"type": "cost_updated", "cost": 7, "max_cost": 10}),
    ]
    advance(room, 11.71)
    assert room.players[1].cost == 7
    advance(room, 11.72)
    assert room.players[1].cost == 8
    advance(room, 10 + 0.86 * 4)
    assert room.players[1].cost == MAX_COST


def test_deathmatch_cost_interval():
    room = new_room()
    advance(room, 250)
    room.players[1].cost = 0
    advance(room, 250.44)
    assert room.players[1].cost == 0
    advance(room, 250.45)
    assert room.players[1].cost == 1
    advance(room, 250.9)
    assert room.players[1].cost == 2
    advance(room, 250 + 0.45 * 10)
    assert room.players[1].cost == MAX_COST


def test_spend_cost_resumes_regen():
    room = new_room()
    advance(room, 20)
    assert room.players[1].cost == MAX_COST
    assert spend_cost(room, 1, 3, 20)
    assert not spend_cost(room, 1, 8, 20)
    assert not spend_cost(room, 1, 0, 20)
    assert room.players[1].cost == 7
    assert room.players[2].cost == MAX_COST
    advance(room, 20.85)
    assert room.players[1].cost == 7
    assert advance(room, 20.86) == [(1, {"type": "cost_updated", "cost": 8, "max_cost": 10})]


def test_deathmatch_transition_keeps_state():
    stepped, jumped = new_room([1, 2]), new_room([3, 4])
    for room in (stepped, jumped):
        advance(room, 249)
        room.players[room.player_order[0]].cost = 0
        room.players[room.player_order[0]].position = (5, 5)
    drain(stepped, 0, 249, 251.5)
    advance(jumped, 251.5)

    expected = int(charge_at(241.5) - charge_at(239))
    assert stepped.players[1].cost == jumped.players[3].cost == expected
    assert stepped.players[1].position == (5, 5)
    assert stepped.players[2].cost == MAX_COST
    assert stepped.status is jumped.status is RoomStatus.DEATHMATCH


def test_transition_does_not_double_grant():
    room = new_room()
    advance(room, 249.5)
    room.players[1].cost = 0
    events = advance(room, 250.3)
    assert room.players[1].cost == 1
    assert types(events) == ["deathmatch_started", "cost_updated", "game_tick"]
    assert advance(room, 250.3) == []


def test_rooms_are_independent():
    a = new_room([1, 2], now=0)
    b = new_room([3, 4], now=100)
    advance(a, 260)
    advance(b, 260)
    assert a.status is RoomStatus.DEATHMATCH
    assert b.status is RoomStatus.PLAYING
    a.players[1].cost = 0
    advance(a, 261)
    assert b.players[3].cost == MAX_COST
    assert b.last_tick == 150


def test_no_cost_after_finished():
    room = new_room()
    advance(room, 305)
    room.players[1].cost = 0
    advance(room, 310)
    assert room.players[1].cost > 0
    cost = room.players[1].cost
    assert advance(room, 400) == []
    assert room.players[1].cost == cost
    assert not spend_cost(room, 1, 1, 400)


def test_disconnected_player_keeps_gaining():
    room = new_room()
    game_room.set_player_connected(1, False)
    advance(room, 10)
    events = advance(room, 10.86)
    assert (1, {"type": "cost_updated", "cost": 7, "max_cost": 10}) in events
    assert room.players[1].connected is False


def test_start_twice_does_not_reset():
    room = new_room()
    assert start(room, 50) is None
    assert room.ready_at == 0


def test_concurrent_advance_single_transition():
    room = new_room()
    events = []
    barrier = threading.Barrier(8)

    def run():
        barrier.wait()
        with websocket.state_lock:
            events.extend(advance(room, 310))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert types(events).count("game_started") == 1
    assert types(events).count("deathmatch_started") == 1
    assert types(events).count("game_finished") == 1


@pytest.fixture
def now(monkeypatch):
    value = [0.0]
    monkeypatch.setattr(game_engine, "clock", lambda: value[0])
    return value


def test_timer_survives_failure_in_other_room(now, monkeypatch):
    original = game_engine.advance

    def broken(room, t):
        if room.player_order[0] == 1:
            raise RuntimeError("boom")
        return original(room, t)

    monkeypatch.setattr(game_engine, "advance", broken)

    async def main():
        a, b = create_room([1, 2]), create_room([3, 4])
        websocket.start_timer(a)
        websocket.start_timer(b)
        assert len(websocket.timers) == 2
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return a, b

    a, b = asyncio.run(main())
    assert a.status is RoomStatus.READY
    assert b.status is RoomStatus.FINISHED


def test_remove_room_stops_timer(now):
    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        with websocket.state_lock:
            remove_room(room.room_id)
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return room

    room = asyncio.run(main())
    assert room.status is RoomStatus.READY
    assert room.players[1].cost == START_COST


def test_stop_timers_cancels(now):
    async def main():
        for ids in ([1, 2], [3, 4]):
            websocket.start_timer(create_room(ids))
        tasks = list(websocket.timers.values())
        await websocket.stop_timers()
        return tasks

    assert all(t.cancelled() for t in asyncio.run(main()))
    assert websocket.timers == {}


async def async_wait(check):
    for _ in range(200):
        if check():
            return True
        await asyncio.sleep(0.01)
    return check()


@pytest.fixture
def client(now):
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def users(client):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(2)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def receive_until(ws, event):
    while True:
        message = ws.receive_json()
        if message["type"] == event:
            return message


def test_game_events(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        ws1.send_json({"type": "queue_join"})
        ws1.receive_json()
        ws2.send_json({"type": "queue_join"})
        ws2.receive_json()
        assert ws1.receive_json()["type"] == ws2.receive_json()["type"] == "match_found"
        room = get_player_room(users[0])
        room_id = str(room.room_id)
        for ws in (ws1, ws2):
            assert ws.receive_json() == {"type": "game_ready", "room_id": room_id, "remaining_seconds": 10}

        now[0] = 1
        for ws in (ws1, ws2):
            assert ws.receive_json() == {"type": "game_tick", "status": "READY", "remaining_seconds": 9}

        now[0] = 10
        for ws in (ws1, ws2):
            assert ws.receive_json() == {"type": "game_started", "room_id": room_id, "duration_seconds": 300}
            assert ws.receive_json() == {"type": "game_tick", "status": "PLAYING", "remaining_seconds": 300}

        with websocket.state_lock:
            room.players[users[1]].cost = 4
        now[0] = 10.86
        assert ws1.receive_json() == {"type": "cost_updated", "cost": 7, "max_cost": 10}
        assert ws2.receive_json() == {"type": "cost_updated", "cost": 5, "max_cost": 10}

        now[0] = 250
        for ws in (ws1, ws2):
            assert ws.receive_json() == {"type": "deathmatch_started", "remaining_seconds": 60, "cost_interval": 0.45}
            assert ws.receive_json()["type"] == "cost_updated"
            assert ws.receive_json() == {"type": "game_tick", "status": "DEATHMATCH", "remaining_seconds": 60}

        now[0] = 310
        for ws in (ws1, ws2):
            assert ws.receive_json() == {"type": "game_finished", "room_id": room_id}
        assert wait_until(lambda: not websocket.timers)
        assert room.status is RoomStatus.FINISHED
        assert game_room.get_room(room.room_id) is room


def test_room_state_and_reconnect(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            auth(ws2, users[1])
            join_and_match(ws1, ws2)
            room = get_player_room(users[0])
            task = websocket.timers[room.room_id]

            now[0] = 4
            ws1.send_json({"type": "room_state"})
            state = receive_until(ws1, "room_state")
            assert state["status"] == "READY"
            assert state["ready_remaining_seconds"] == 6
            assert state["remaining_seconds"] == 300
            assert state["my_cost"] == START_COST
            assert state["max_cost"] == MAX_COST
            assert state["started_at"] is None

        now[0] = 15
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        with websocket.state_lock:
            room.players[users[0]].cost = 2
        now[0] = 15.86
        assert wait_until(lambda: room.players[users[0]].cost == 3)

        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            state = receive_until(ws1, "room_state")
            assert state["status"] == "PLAYING"
            assert state["ready_remaining_seconds"] == 0
            assert state["remaining_seconds"] == 295
            assert state["my_cost"] == 3
            assert state["started_at"] == room.started_at.isoformat()
            assert websocket.timers == {room.room_id: task}
            assert room.ready_at == 0
