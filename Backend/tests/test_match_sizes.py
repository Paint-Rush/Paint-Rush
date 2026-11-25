import pytest

from app import game_engine, game_room, maps, matchmaking, websocket
from app.database import SessionLocal
from app.game_room import MAP_SIZE, SPAWN_POINTS, Color, RoomStatus, create_room, get_player_room
from app.maps import load_map
from app.matchmaking import cancel_queue, get_waiting_players, join_queue, try_match
from app.models import User
from tests.test_game_engine import client, now, receive_until
from tests.test_websocket import WS_URL, auth, clear_state, wait_until


@pytest.fixture(autouse=True)
def real_rules(now, monkeypatch):
    assert matchmaking.MATCH_SIZE == 4
    assert not hasattr(matchmaking, "MATCH_WAIT_SECONDS") and not hasattr(websocket, "match_task")
    assert matchmaking.MAP_IDS == ("default", "crossroads", "glass_garden")
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("default",))
    clear_state()
    yield
    clear_state()


def join_at(now, t, *user_ids):
    now[0] = t
    for user_id in user_ids:
        join_queue(user_id)


@pytest.mark.parametrize("size", [1, 2, 3])
def test_fewer_than_four_never_match(now, size):
    join_at(now, 0, *range(1, size + 1))
    for t in (0, 10, 60, 1000):
        now[0] = t
        assert try_match() is None
    assert game_room.rooms == {}
    assert get_waiting_players() == list(range(1, size + 1))


def test_four_players_match_immediately(now):
    join_at(now, 0, 1, 2, 3, 4)
    room = try_match()
    assert room.player_order == [1, 2, 3, 4]
    assert get_waiting_players() == []


def test_five_players_match_four_fifo_and_fifth_waits(now):
    join_at(now, 0, 6, 5, 4, 3, 2)
    assert try_match().player_order == [6, 5, 4, 3]
    assert get_waiting_players() == [2]
    now[0] = 100
    assert try_match() is None


def test_eight_players_make_two_rooms_in_fifo_order(now):
    join_at(now, 0, *range(1, 9))
    assert [try_match().player_order, try_match().player_order] == [[1, 2, 3, 4], [5, 6, 7, 8]]
    assert try_match() is None
    assert get_waiting_players() == []
    assert len(game_room.rooms) == 2


def test_cancel_while_waiting(now):
    join_at(now, 0, 1, 2, 3)
    assert cancel_queue(2) is True
    assert cancel_queue(2) is False
    join_at(now, 5, 4)
    assert try_match() is None
    assert get_waiting_players() == [1, 3, 4]
    join_at(now, 6, 5)
    assert try_match().player_order == [1, 3, 4, 5]


def test_duplicate_and_matched_player_rejected(now):
    join_at(now, 0, 1, 2, 3)
    with pytest.raises(ValueError):
        join_queue(1)
    join_at(now, 0, 4)
    try_match()
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
        room = try_match()
        players = room.players.values()
        assert sorted(p.color for p in players) == sorted(Color)
        assert {p.spawn for p in players} == {(0, 0), (0, MAP_SIZE - 1), (MAP_SIZE - 1, 0), (MAP_SIZE - 1, MAP_SIZE - 1)}
        assert {p.spawn for p in players} == set(SPAWN_POINTS)
        assert all(p.position == p.spawn for p in players)


def test_two_player_room_keeps_corner_spawn_rule(now):
    room = create_room([1, 2])
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
        room = try_match()
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


def test_ws_three_players_wait_without_match(client, players, now):
    sockets = open_sockets(client, players[:3])
    try:
        for count, ws in enumerate(sockets, 1):
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json() == {"type": "queue_waiting", "waiting": count, "match_size": 4}
        now[0] = 100
        sockets[0].send_json({"type": "queue_join"})
        assert sockets[0].receive_json()["code"] == "already_in_queue"
        assert game_room.rooms == {}
        assert get_waiting_players() == players[:3]
    finally:
        close_sockets(sockets)


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


def test_ws_fifth_player_waits_after_four_match(client, players, now):
    sockets = open_sockets(client, players)
    try:
        join_all(sockets[:4])
        assert all(ws.receive_json()["type"] == "match_found" for ws in sockets[:4])
        sockets[4].send_json({"type": "queue_join"})
        assert sockets[4].receive_json() == {"type": "queue_waiting", "waiting": 1, "match_size": 4}
        assert get_player_room(players[0]).player_order == players[:4]
        assert get_waiting_players() == [players[4]]
        sockets[0].send_json({"type": "queue_join"})
        assert receive_until(sockets[0], "error")["code"] == "already_in_game"
    finally:
        close_sockets(sockets)


def test_ws_cancel_while_waiting(client, players, now):
    sockets = open_sockets(client, players[:4])
    try:
        join_all(sockets[:3])
        sockets[1].send_json({"type": "queue_cancel"})
        assert sockets[1].receive_json() == {"type": "queue_cancelled"}
        join_all(sockets[3:])
        assert game_room.rooms == {}
        assert get_waiting_players() == [players[0], players[2], players[3]]
    finally:
        close_sockets(sockets)


def test_ws_disconnect_while_waiting(client, players, now):
    sockets = open_sockets(client, players)
    try:
        join_all(sockets[:3])
        sockets[1].__exit__(None, None, None)
        assert wait_until(lambda: get_waiting_players() == [players[0], players[2]])
        join_all(sockets[3:])
        assert all(sockets[i].receive_json()["type"] == "match_found" for i in (0, 2, 3, 4))
        assert get_player_room(players[0]).player_order == [players[0], players[2], players[3], players[4]]
        assert get_player_room(players[1]) is None
    finally:
        close_sockets(sockets[:1] + sockets[2:])


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
