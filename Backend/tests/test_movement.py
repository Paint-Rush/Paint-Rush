import copy
import threading
from collections import Counter

import pytest

from app import websocket
from app.algorithms import bfs
from app.algorithms.bfs import find_path
from app.cards import CARDS, DEFAULT_DECK, MOVE_STEPS, CardType
from app.game_engine import use_card
from app.game_room import create_room, get_player_room
from app.maps import create_default_map, set_tile
from app.movement import check_move, move_player
from tests.test_action_events import collect_until, read_log, start_game, sync, users
from tests.test_card_use import playing_room, snapshot
from tests.test_game_engine import client, now
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def grid(blocked=(), glass=()):
    map_data = create_default_map()
    for x, y in blocked:
        set_tile(map_data, x, y, "BLOCKED")
    for x, y in glass:
        set_tile(map_data, x, y, "GLASS")
    return map_data


def room_at(position=(5, 5), t=10, user_ids=(1, 2)):
    room = playing_room(t, user_ids)
    room.players[user_ids[0]].position = position
    return room


def state(room):
    return (
        {uid: p.position for uid, p in room.players.items()},
        copy.deepcopy(room.tile_owners),
        dict(room.tile_counts),
        {uid: p.total_painted for uid, p in room.players.items()},
    )


def test_bfs_basic_path():
    assert find_path(grid(), (0, 0), (0, 0), 0) == [(0, 0)]
    assert find_path(grid(), (0, 0), (1, 0), 1) == [(0, 0), (1, 0)]
    path = find_path(grid(), (0, 0), (2, 3))
    assert path[0] == (0, 0) and path[-1] == (2, 3)
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        assert abs(ax - bx) + abs(ay - by) == 1


def test_bfs_shortest_path():
    assert len(find_path(grid(), (0, 0), (5, 7))) - 1 == 12
    assert len(find_path(grid(), (19, 19), (0, 0))) - 1 == 38


def test_bfs_detours_around_blocked():
    wall = [(5, y) for y in range(0, 10)]
    path = find_path(grid(blocked=wall), (4, 0), (6, 0))
    assert path[0] == (4, 0) and path[-1] == (6, 0)
    assert not set(path) & set(wall)
    assert len(path) - 1 == 22
    assert find_path(grid(blocked=wall), (4, 0), (6, 0), 21) is None


def test_bfs_unreachable():
    walls = [(1, 0), (0, 1)]
    assert find_path(grid(blocked=walls), (0, 0), (10, 10)) is None
    assert find_path(grid(blocked=[(3, 3)]), (0, 0), (3, 3)) is None
    assert find_path(grid(), (0, 0), (20, 0)) is None
    assert find_path(grid(), (0, 0), (-1, 0)) is None


def test_bfs_max_steps():
    assert find_path(grid(), (5, 5), (5, 7), 1) is None
    assert find_path(grid(), (5, 5), (5, 7), 2) == [(5, 5), (5, 6), (5, 7)]
    assert find_path(grid(), (5, 5), (6, 5), 0) is None
    assert find_path(grid(), (5, 5), (6, 5), -1) is None


def test_bfs_uses_linked_queue(monkeypatch):
    created = []

    class Spy(bfs.LinkedQueue):
        def __init__(self):
            super().__init__()
            created.append(self)

    monkeypatch.setattr(bfs, "LinkedQueue", Spy)
    assert find_path(grid(), (0, 0), (1, 1)) is not None
    assert len(created) == 1


def test_move_card_steps_defined():
    move_cards = {card_id for card_id, card in CARDS.items() if card.type is CardType.MOVE}
    assert set(MOVE_STEPS) == move_cards
    assert MOVE_STEPS == {"move_dash": 1, "move_jump": 1}


@pytest.mark.parametrize("tile", ["NORMAL", "GLASS"])
def test_move_to_passable_tile(tile):
    room = room_at()
    map_data = grid(glass=[(5, 4)] if tile == "GLASS" else [])
    assert move_player(room, 1, 5, 4, map_data, 1, 10) == [(5, 5), (5, 4)]
    assert room.players[1].position == (5, 4)


@pytest.mark.parametrize("target, code", [
    ((5, 4), "blocked_target"),
    ((20, 5), "invalid_target"),
    ((5, 20), "invalid_target"),
    ((-1, 5), "invalid_target"),
    ((5, -1), "invalid_target"),
    ((6, 6), "unreachable_target"),
    ((5, 7), "unreachable_target"),
    ((5, 5), "invalid_target"),
])
def test_invalid_move_target(target, code):
    room = room_at()
    before = state(room)
    with pytest.raises(ValueError, match=code):
        move_player(room, 1, *target, grid(blocked=[(5, 4)]), 1, 10)
    assert state(room) == before


def test_move_from_edge_out_of_map():
    room = room_at((0, 0))
    for target in [(-1, 0), (0, -1)]:
        with pytest.raises(ValueError, match="invalid_target"):
            move_player(room, 1, *target, grid(), 1, 10)
    assert room.players[1].position == (0, 0)


@pytest.mark.parametrize("x, y", [("5", 4), (5, 4.0), (True, 4), (None, 4), (5, [4]), (5, None)])
def test_invalid_coordinate_type(x, y):
    room = room_at()
    with pytest.raises(ValueError, match="invalid_target"):
        move_player(room, 1, x, y, grid(), 1, 10)
    assert room.players[1].position == (5, 5)


def test_move_changes_only_own_position():
    room = room_at()
    other = room.players[2].position
    move_player(room, 1, 6, 5, grid(), 1, 10)
    assert room.players[1].position == (6, 5)
    assert room.players[2].position == other


def test_rooms_independent():
    a = room_at(user_ids=(1, 2))
    b = room_at(user_ids=(3, 4))
    b.players[3].position = (5, 5)
    move_player(a, 1, 5, 6, grid(), 1, 10)
    assert a.players[1].position == (5, 6)
    assert b.players[3].position == (5, 5)


def test_move_rejects_non_participant():
    room = room_at()
    for user_id in (99, "1", None):
        with pytest.raises(ValueError, match="not_in_room"):
            move_player(room, user_id, 5, 6, grid(), 1, 10)


@pytest.mark.parametrize("t", [5, 310])
def test_move_rejected_when_not_playing(t):
    room = room_at(t=t)
    with pytest.raises(ValueError, match="game_not_playing"):
        move_player(room, 1, 5, 6, grid(), 1, t)
    assert room.players[1].position == (5, 5)


def test_move_rejected_after_time_up_before_tick():
    room = room_at(t=309)
    with pytest.raises(ValueError, match="game_not_playing"):
        move_player(room, 1, 5, 6, grid(), 1, 310)
    assert move_player(room, 1, 5, 6, grid(), 1, 309.9) == [(5, 5), (5, 6)]


def test_move_rejected_when_dead():
    room = room_at()
    room.players[1].alive = False
    with pytest.raises(ValueError, match="player_dead"):
        move_player(room, 1, 5, 6, grid(), 1, 10)
    assert room.players[1].position == (5, 5)


def test_move_rejects_wrong_map():
    room = room_at()
    for map_data in [create_default_map("other"), {"map_id": "default"}]:
        with pytest.raises(ValueError, match="map_unavailable"):
            move_player(room, 1, 5, 6, map_data, 1, 10)
    room.tile_owners = [[None] * 10 for _ in range(10)]
    with pytest.raises(ValueError, match="map_unavailable"):
        move_player(room, 1, 5, 6, grid(), 1, 10)
    assert room.players[1].position == (5, 5)


def test_move_keeps_paint_data():
    room = room_at()
    room.tile_owners[5][6] = 2
    room.tile_counts[2] = 1
    room.players[2].total_painted = 1
    _, tiles, counts, painted = state(room)
    move_player(room, 1, 6, 5, grid(), 1, 10)
    assert state(room)[1:] == (tiles, counts, painted)


def test_check_move_does_not_change_position():
    room = room_at()
    assert check_move(room, 1, 5, 6, grid(), 1, 10) == [(5, 5), (5, 6)]
    assert room.players[1].position == (5, 5)


def test_concurrent_moves_consistent():
    room = room_at((0, 0))
    results = []
    barrier = threading.Barrier(8)

    def run():
        barrier.wait()
        with websocket.state_lock:
            try:
                results.append(move_player(room, 1, 1, 0, grid(), 1, 10))
            except ValueError as e:
                results.append(str(e))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count([(0, 0), (1, 0)]) == 1
    assert results.count("invalid_target") == 7
    assert room.players[1].position == (1, 0)


def set_position(room, user_id, position):
    with websocket.state_lock:
        room.players[user_id].position = position


def test_move_card_over_websocket(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        set_position(room, users[0], (5, 5))
        other_position = room.players[users[1]].position
        next_card = next(iter(player.draw_queue))
        queue_before = list(player.draw_queue)
        _, tiles, counts, painted = state(room)

        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 6})
        moved = {"type": "player_moved", "user_id": users[0], "x": 5, "y": 6, "path": [[5, 5], [5, 6]]}
        assert collect_until(ws1, "player_moved") == [
            {"type": "card_used", "user_id": users[0], "card_id": "move_dash", "hand_index": 0},
            {"type": "hand_updated", "hand": player.hand, "cost": 4},
            {"type": "cost_updated", "cost": 4, "max_cost": 10},
            {"type": "move_requested", "user_id": users[0], "card_id": "move_dash"},
            moved,
        ]
        assert collect_until(ws2, "player_moved") == [
            {"type": "card_used", "user_id": users[0], "card_id": "move_dash", "hand_index": 0},
            {"type": "move_requested", "user_id": users[0], "card_id": "move_dash"},
            moved,
        ]
        assert player.position == (5, 6)
        assert player.cost == 4 and player.cards_used == 1 and player.cost_used == 2
        assert player.hand[0] == next_card
        assert list(player.draw_queue) == queue_before[1:] + ["move_dash"]
        assert room.players[users[1]].position == other_position
        assert state(room)[1:] == (tiles, counts, painted)

        ws2.send_json({"type": "room_state"})
        players = {p["user_id"]: p for p in collect_until(ws2, "room_state")[-1]["players"]}
        assert players[users[0]]["position"] == [5, 6]
        assert players[users[1]]["position"] == list(other_position)

    records = read_log(room)
    names = [r["type"] for r in records]
    i = names.index("card_used")
    assert names[i:i + 4] == ["card_used", "cost_changed", "move_requested", "player_moved"]
    assert records[i + 3]["data"] == {
        "user_id": users[0],
        "from": [5, 5],
        "to": [5, 6],
        "path": [[5, 5], [5, 6]],
        "card_id": "move_dash",
    }
    assert names.count("player_moved") == 1


def test_attack_card_keeps_position(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        set_position(room, users[0], (5, 5))
        ws1.send_json({"type": "card_use", "hand_index": 1, "target_x": 5, "target_y": 6})
        messages = collect_until(ws1, "attack_requested")
        assert sync(ws1) == []
        assert "player_moved" not in [m["type"] for m in messages]
        assert room.players[users[0]].position == (5, 5)
        assert room.players[users[0]].cards_used == 1
    assert "player_moved" not in [r["type"] for r in read_log(room)]


def test_move_card_without_target_keeps_legacy(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        set_position(room, users[0], (5, 5))
        ws1.send_json({"type": "card_use", "hand_index": 0})
        collect_until(ws1, "move_requested")
        assert sync(ws1) == []
        assert room.players[users[0]].position == (5, 5)
        assert room.players[users[0]].cards_used == 1
    assert "player_moved" not in [r["type"] for r in read_log(room)]


@pytest.mark.parametrize("target, code", [
    ({"target_x": 6, "target_y": 6}, "unreachable_target"),
    ({"target_x": 5, "target_y": 7}, "unreachable_target"),
    ({"target_x": 5, "target_y": 5}, "invalid_target"),
    ({"target_x": 20, "target_y": 5}, "invalid_target"),
    ({"target_x": -1, "target_y": 5}, "invalid_target"),
    ({"target_x": "5", "target_y": 6}, "invalid_target"),
    ({"target_x": 5}, "invalid_target"),
])
def test_invalid_target_keeps_card_and_cost(client, users, now, target, code):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        set_position(room, users[0], (5, 5))
        before = snapshot(player), state(room)
        ws1.send_json({"type": "card_use", "hand_index": 0, **target})
        reply = collect_until(ws1, "error")
        assert len(reply) == 1 and reply[0]["code"] == code
        assert reply[0]["message"] == websocket.CARD_ERRORS[code]
        assert (snapshot(player), state(room)) == before
        assert sync(ws2) == []
    names = [r["type"] for r in read_log(room)]
    assert not {"card_used", "move_requested", "player_moved"} & set(names)


def test_move_card_blocked_target(client, users, now, monkeypatch):
    blocked = grid(blocked=[(5, 6)])
    monkeypatch.setattr(websocket, "load_map", lambda map_id: blocked)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        set_position(room, users[0], (5, 5))
        before = snapshot(player)
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 6})
        assert collect_until(ws1, "error")[0]["code"] == "blocked_target"
        assert snapshot(player) == before and player.position == (5, 5)


def test_move_with_insufficient_cost(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        set_position(room, users[0], (5, 5))
        with websocket.state_lock:
            player.cost = 1
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 6})
        assert collect_until(ws1, "error")[0]["code"] == "insufficient_cost"
        assert player.position == (5, 5) and player.cards_used == 0


def test_move_rejected_after_time_up(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        set_position(room, users[0], (5, 5))
        with websocket.state_lock:
            now[0] = 310
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 6})
        assert collect_until(ws1, "error")[0]["code"] == "game_not_playing"
        assert room.players[users[0]].position == (5, 5)


def test_concurrent_move_cards(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        set_position(room, users[0], (5, 5))
        with websocket.state_lock:
            player.hand = ["move_dash", "move_dash", "move_jump"]
        barrier = threading.Barrier(2)

        def send(index):
            barrier.wait()
            ws1.send_json({"type": "card_use", "hand_index": index, "target_x": 5, "target_y": 6})

        threads = [threading.Thread(target=send, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seen = []
        while sum(m["type"] in ("player_moved", "error") for m in seen) < 2:
            seen += sync(ws1)
        assert [m["type"] for m in seen].count("player_moved") == 1
        assert [m["code"] for m in seen if m["type"] == "error"] == ["invalid_target"]
        assert player.position == (5, 6)
        assert player.cards_used == 1 and player.cost == 4
        assert len(player.hand) == 3 and len(player.draw_queue) == 3


def test_move_card_not_sent_to_other_room(client, users, now):
    with (
        client.websocket_connect(WS_URL) as ws1,
        client.websocket_connect(WS_URL) as ws2,
        client.websocket_connect(WS_URL) as ws3,
        client.websocket_connect(WS_URL) as ws4,
    ):
        auth(ws3, users[2])
        auth(ws4, users[3])
        join_and_match(ws3, ws4)
        other = get_player_room(users[2])
        room = start_game(ws1, ws2, users, now)
        sync(ws3)
        sync(ws4)
        set_position(room, users[0], (5, 5))
        other_positions = {uid: p.position for uid, p in other.players.items()}
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 6, "target_y": 5})
        collect_until(ws2, "player_moved")
        assert sync(ws3) == [] and sync(ws4) == []
        assert {uid: p.position for uid, p in other.players.items()} == other_positions


def test_reconnect_restores_position(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        ws1 = client.websocket_connect(WS_URL).__enter__()
        try:
            room = start_game(ws1, ws2, users, now)
            set_position(room, users[0], (5, 5))
            ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 4, "target_y": 5})
            collect_until(ws2, "player_moved")
        finally:
            ws1.__exit__(None, None, None)
        collect_until(ws2, "player_disconnected")
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            players = {p["user_id"]: p for p in ws1.receive_json()["players"]}
            assert players[users[0]]["position"] == [4, 5]
            assert room.players[users[0]].position == (4, 5)


def test_new_game_starts_at_spawn():
    played = room_at()
    move_player(played, 1, 5, 6, grid(), 1, 10)
    room = create_room([3, 4])
    for player in room.players.values():
        assert player.position == player.spawn
    assert played.players[1].position == (5, 6)


def test_use_card_alone_does_not_move():
    room = room_at()
    assert use_card(room, 1, 0, 10) == "move_dash"
    assert room.players[1].position == (5, 5)
    assert Counter(room.players[1].hand + list(room.players[1].draw_queue)) == Counter(DEFAULT_DECK)
