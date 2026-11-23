import asyncio

import pytest

from app import game_engine, game_room, maps, matchmaking, websocket
from app.database import SessionLocal
from app.game_room import MAP_SIZE, SPAWN_POINTS, Color, RoomStatus, create_room, get_player_room
from app.maps import load_map
from app.matchmaking import cancel_queue, get_waiting_players, join_queue, match_deadline, try_match
from app.models import User
from tests.test_game_engine import client, now, receive_until
from tests.test_websocket import WS_URL, auth, clear_state, wait_until


@pytest.fixture(autouse=True)
def real_rules(now, monkeypatch):
    assert (matchmaking.MATCH_SIZE, matchmaking.MAX_MATCH_SIZE, matchmaking.MATCH_WAIT_SECONDS) == (2, 4, 10)
    assert matchmaking.MAP_IDS == ("default", "crossroads", "glass_garden")
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("default",))
    clear_state()
    yield
    clear_state()


def join_at(now, t, *user_ids):
    now[0] = t
    for user_id in user_ids:
        join_queue(user_id)


def test_two_players_match_after_ten_seconds(now):
    join_at(now, 0, 1, 2)
    assert match_deadline() == 10
    assert try_match(9.9) is None
    room = try_match(10)
    assert room.player_order == [1, 2]
    assert get_waiting_players() == []
    assert match_deadline() is None


def test_single_player_never_matches(now):
    join_at(now, 0, 1)
    assert match_deadline() is None
    assert try_match(100) is None
    assert game_room.rooms == {}


def test_four_players_match_immediately(now):
    join_at(now, 0, 1, 2, 3, 4)
    room = try_match(0)
    assert room.player_order == [1, 2, 3, 4]
    assert get_waiting_players() == []


def test_three_players_timeout_matches_first_two(now):
    join_at(now, 0, 1, 2, 3)
    assert try_match(5) is None
    room = try_match(10)
    assert room.player_order == [1, 2]
    assert get_waiting_players() == [3]
    assert match_deadline() is None
    assert try_match(100) is None

    join_at(now, 12, 4)
    assert match_deadline() == 22
    assert try_match(21.9) is None
    assert try_match(22).player_order == [3, 4]


def test_five_or_more_players_fifo(now):
    join_at(now, 0, 6, 5, 4, 3, 2, 1)
    assert try_match(0).player_order == [6, 5, 4, 3]
    assert get_waiting_players() == [2, 1]
    assert match_deadline() == 10
    assert try_match(9) is None
    assert try_match(10).player_order == [2, 1]


def test_cancel_during_timer_stops_it(now):
    join_at(now, 0, 1, 2)
    now[0] = 5
    assert cancel_queue(2) is True
    assert cancel_queue(2) is False
    assert match_deadline() is None
    assert try_match(10) is None
    join_at(now, 12, 3)
    assert match_deadline() == 22
    assert try_match(22).player_order == [1, 3]


def test_cancel_with_three_waiting_keeps_timer(now):
    join_at(now, 0, 1, 2, 3)
    now[0] = 5
    cancel_queue(1)
    assert match_deadline() == 10
    assert try_match(10).player_order == [2, 3]


def test_fourth_player_wins_race_with_expired_timer(now):
    join_at(now, 0, 1, 2, 3)
    join_at(now, 10, 4)
    assert try_match(10).player_order == [1, 2, 3, 4]


def test_cancel_and_fourth_join_do_not_make_four_room(now):
    join_at(now, 0, 1, 2, 3)
    now[0] = 9
    cancel_queue(2)
    join_queue(4)
    assert try_match(9) is None
    assert get_waiting_players() == [1, 3, 4]
    assert try_match(10).player_order == [1, 3]
    assert get_waiting_players() == [4]


def test_duplicate_and_matched_player_rejected(now):
    join_at(now, 0, 1, 2)
    with pytest.raises(ValueError):
        join_queue(1)
    try_match(10)
    with pytest.raises(ValueError):
        join_queue(1)
    assert get_waiting_players() == []


@pytest.mark.parametrize("size", [3, 5])
def test_invalid_room_sizes_rejected(size):
    with pytest.raises(ValueError):
        create_room(list(range(1, size + 1)))
    assert game_room.rooms == {}


def test_four_player_room_colors_and_corner_spawns(now):
    for _ in range(20):
        clear_state()
        join_at(now, 0, 1, 2, 3, 4)
        room = try_match(0)
        players = room.players.values()
        assert sorted(p.color for p in players) == sorted(Color)
        assert {p.spawn for p in players} == {(0, 0), (0, MAP_SIZE - 1), (MAP_SIZE - 1, 0), (MAP_SIZE - 1, MAP_SIZE - 1)}
        assert {p.spawn for p in players} == set(SPAWN_POINTS)
        assert all(p.position == p.spawn for p in players)


def test_two_player_room_keeps_corner_spawn_rule(now):
    join_at(now, 0, 1, 2)
    room = try_match(10)
    spawns = [p.spawn for p in room.players.values()]
    assert len(set(spawns)) == 2 and set(spawns) <= set(SPAWN_POINTS)
    assert len({p.color for p in room.players.values()}) == 2


def test_map_assigned_from_repository_maps(now, monkeypatch):
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("default", "test"))
    assert matchmaking.available_maps() == ["default", "test"]
    assert maps.list_maps() == ["crossroads", "default", "glass_garden", "test"]
    seen = set()
    for map_id in ("default", "test"):
        clear_state()
        monkeypatch.setattr(matchmaking.random, "choice", lambda options, m=map_id: m if m in options else None)
        join_at(now, 0, 1, 2, 3, 4)
        room = try_match(0)
        assert room.map_id == map_id
        seen.add(room.map_id)
    assert seen == {"default", "test"}


def test_invalid_or_missing_maps_skipped(monkeypatch):
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("missing", "default", "BAD ID"))
    assert matchmaking.available_maps() == ["default"]
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("missing",))
    assert matchmaking.choose_map() == "default"


@pytest.fixture
def players(client):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(5)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def open_sockets(client, user_ids):
    sockets = [client.websocket_connect(WS_URL) for _ in user_ids]
    for ws, uid in zip(sockets, user_ids):
        ws.__enter__()
        auth(ws, uid)
    return sockets


def close_sockets(sockets):
    for ws in sockets:
        ws.__exit__(None, None, None)


def join_all(sockets):
    for ws in sockets:
        ws.send_json({"type": "queue_join"})
        assert ws.receive_json()["type"] == "queue_waiting"


def test_ws_two_players_wait_then_match(client, players, now):
    sockets = open_sockets(client, players[:2])
    try:
        join_all(sockets)
        assert websocket.match_task is not None
        assert game_room.rooms == {}
        now[0] = 10
        found = [ws.receive_json() for ws in sockets]
        ready = [ws.receive_json() for ws in sockets]
    finally:
        close_sockets(sockets)
    assert found[0] == found[1] and found[0]["type"] == "match_found"
    assert [p["user_id"] for p in found[0]["players"]] == players[:2]
    assert found[0]["map_id"] == "default"
    assert [m["type"] for m in ready] == ["game_ready"] * 2


def test_ws_four_players_match_immediately_and_start(client, players, now):
    sockets = open_sockets(client, players[:4])
    try:
        join_all(sockets)
        found = [ws.receive_json() for ws in sockets]
        assert all(ws.receive_json()["type"] == "game_ready" for ws in sockets)
        room = get_player_room(players[0])
        states = []
        for ws in sockets:
            ws.send_json({"type": "room_state"})
            states.append(receive_until(ws, "room_state"))
        now[0] = 10
        started = [receive_until(ws, "game_started") for ws in sockets]
    finally:
        close_sockets(sockets)
    assert all(m == found[0] for m in found)
    assert len(found[0]["players"]) == 4
    assert len({p["color"] for p in found[0]["players"]}) == 4
    assert {tuple(p["spawn"]) for p in found[0]["players"]} == set(SPAWN_POINTS)
    map_data = load_map(room.map_id)
    assert all(s["map_id"] == room.map_id and s["map"]["tiles"] == map_data["tiles"] for s in states)
    assert all(s["status"] == "READY" for s in states)
    assert len({m["room_id"] for m in started}) == 1
    assert room.status is RoomStatus.PLAYING
    assert get_waiting_players() == []


def test_ws_three_players_timeout_leaves_third_waiting(client, players, now):
    sockets = open_sockets(client, players[:3])
    try:
        join_all(sockets)
        now[0] = 10
        assert [sockets[i].receive_json()["type"] for i in (0, 1)] == ["match_found"] * 2
        assert wait_until(lambda: get_waiting_players() == [players[2]])
        room = get_player_room(players[0])
        assert room.player_order == players[:2]
        assert get_player_room(players[2]) is None
    finally:
        close_sockets(sockets)


def test_ws_cancel_during_timer(client, players, now):
    sockets = open_sockets(client, players[:2])
    try:
        join_all(sockets)
        sockets[1].send_json({"type": "queue_cancel"})
        assert sockets[1].receive_json() == {"type": "queue_cancelled"}
        now[0] = 20
        assert wait_until(lambda: websocket.match_task is None)
        assert game_room.rooms == {}
        assert get_waiting_players() == [players[0]]
    finally:
        close_sockets(sockets)


def test_ws_reconnect_keeps_room_and_map(client, players, now):
    sockets = open_sockets(client, players[:4])
    try:
        join_all(sockets)
        found = sockets[0].receive_json()
        sockets[0].__exit__(None, None, None)
        room = get_player_room(players[0])
        assert wait_until(lambda: not room.players[players[0]].connected)
        with client.websocket_connect(WS_URL) as ws:
            auth(ws, players[0])
            state = ws.receive_json()
    finally:
        close_sockets(sockets[1:])
    assert state["type"] == "room_state"
    assert state["room_id"] == found["room_id"] and state["map_id"] == found["map_id"] == room.map_id
    assert state["map"]["tiles"] == load_map(room.map_id)["tiles"]
    assert [p["spawn"] for p in state["players"]] == [p["spawn"] for p in found["players"]]
    assert get_player_room(players[0]) is room


def test_shutdown_cancels_match_timer(players, now):
    async def main():
        with websocket.state_lock:
            join_at(now, 0, players[0], players[1])
            websocket.schedule_match_timer()
        task = websocket.match_task
        await websocket.stop_timers()
        return task

    task = asyncio.run(main())
    assert task.cancelled()
    assert websocket.match_task is None
    assert game_room.rooms == {}
