import threading

import pytest

from app import cards, websocket
from app.game_room import create_room, get_player_room
from app.movement import move_player, paint_path
from tests.test_action_events import collect_until, read_log, start_game, sync, users
from tests.test_card_use import playing_room, snapshot
from tests.test_game_engine import client, now
from tests.test_movement import grid, room_at, set_position, state
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def move_and_paint(room, user_id, target, map_data, max_steps=1, now=10):
    path = move_player(room, user_id, *target, map_data, max_steps, now)
    return path, paint_path(room, user_id, path, map_data, now)


def owned(room):
    counts = {uid: 0 for uid in room.player_order}
    for row in room.tile_owners:
        for owner in row:
            if owner is not None:
                counts[owner] += 1
    return counts


def test_move_paints_destination_not_start():
    room = room_at()
    path, tiles = move_and_paint(room, 1, (5, 6), grid())
    assert path == [(5, 5), (5, 6)]
    assert tiles == [{"x": 5, "y": 6, "previous_owner": None, "new_owner": 1}]
    assert room.players[1].position == (5, 6)
    assert room.tile_owners[5][5] is None
    assert room.tile_owners[6][5] == 1
    assert room.tile_counts == {1: 1, 2: 0}
    assert room.players[1].total_painted == 1


def test_long_path_paints_every_step_except_start():
    room = room_at()
    path, tiles = move_and_paint(room, 1, (5, 8), grid(), max_steps=3)
    assert path == [(5, 5), (5, 6), (5, 7), (5, 8)]
    assert [(t["x"], t["y"]) for t in tiles] == path[1:]
    assert room.tile_owners[5][5] is None
    assert room.tile_counts[1] == room.players[1].total_painted == 3


def test_glass_moves_without_paint():
    room = room_at()
    _, tiles, counts, painted = state(room)
    path, changed = move_and_paint(room, 1, (5, 6), grid(glass=[(5, 6)]))
    assert path == [(5, 5), (5, 6)]
    assert changed == []
    assert room.players[1].position == (5, 6)
    assert state(room)[1:] == (tiles, counts, painted)


def test_blocked_rejected_without_changes():
    room = room_at()
    before = state(room), snapshot(room.players[1])
    with pytest.raises(ValueError, match="blocked_target"):
        move_and_paint(room, 1, (5, 6), grid(blocked=[(5, 6)]))
    assert (state(room), snapshot(room.players[1])) == before


def test_overwrite_opponent_tile():
    room = room_at()
    room.tile_owners[6][5] = 2
    room.tile_counts[2] = 1
    room.players[2].total_painted = 1
    _, tiles = move_and_paint(room, 1, (5, 6), grid())
    assert tiles == [{"x": 5, "y": 6, "previous_owner": 2, "new_owner": 1}]
    assert room.tile_owners[6][5] == 1
    assert room.tile_counts == {1: 1, 2: 0}
    assert room.players[1].total_painted == 1
    assert room.players[2].total_painted == 1


def test_own_tile_not_counted_twice():
    room = room_at()
    move_and_paint(room, 1, (5, 6), grid())
    move_and_paint(room, 1, (5, 5), grid())
    _, tiles = move_and_paint(room, 1, (5, 6), grid())
    assert tiles == []
    assert room.tile_counts[1] == 2
    assert room.players[1].total_painted == 2


def test_tile_counts_match_owners():
    room = room_at()
    room.players[2].position = (10, 10)
    map_data = grid(glass=[(6, 5)])
    for user_id, target in [(1, (6, 5)), (1, (7, 5)), (2, (10, 11)), (2, (10, 12)), (1, (7, 6)), (2, (10, 13))]:
        move_and_paint(room, user_id, target, map_data)
    assert room.tile_counts == owned(room) == {1: 2, 2: 3}
    assert room.players[1].total_painted == 2
    assert room.players[2].total_painted == 3


@pytest.mark.parametrize("t", [5, 310])
def test_rejected_when_not_playing(t):
    room = room_at(t=t)
    before = state(room)
    with pytest.raises(ValueError, match="game_not_playing"):
        move_and_paint(room, 1, (5, 6), grid(), now=t)
    assert state(room) == before


def test_rejected_after_time_up_before_tick():
    room = room_at(t=309)
    before = state(room)
    with pytest.raises(ValueError, match="game_not_playing"):
        move_and_paint(room, 1, (5, 6), grid(), now=300 + 10)
    assert state(room) == before


@pytest.mark.parametrize("size", [2, 3, 4])
def test_two_to_four_players(size):
    user_ids = tuple(range(1, size + 1))
    room = playing_room(user_ids=user_ids)
    for i, user_id in enumerate(user_ids):
        room.players[user_id].position = (2 + 4 * i, 5)
    for i, user_id in enumerate(user_ids):
        _, tiles = move_and_paint(room, user_id, (2 + 4 * i, 6), grid())
        assert tiles == [{"x": 2 + 4 * i, "y": 6, "previous_owner": None, "new_owner": user_id}]
    assert room.tile_counts == owned(room) == {user_id: 1 for user_id in user_ids}


def test_new_room_starts_clean():
    played = room_at()
    move_and_paint(played, 1, (5, 6), grid())
    room = create_room([3, 4])
    assert all(owner is None for row in room.tile_owners for owner in row)
    assert room.tile_counts == {3: 0, 4: 0}
    assert all(p.total_painted == 0 and p.position == p.spawn for p in room.players.values())
    assert played.tile_owners[6][5] == 1


def test_move_card_paints_over_websocket(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a, b = users[0], users[1]
        set_position(room, a, (5, 5))
        with websocket.state_lock:
            room.tile_owners[6][5] = b
            room.tile_counts[b] = 1
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 6})
        moved = {"type": "player_moved", "user_id": a, "x": 5, "y": 6, "path": [[5, 5], [5, 6]]}
        updated = {
            "type": "tiles_updated",
            "room_id": str(room.room_id),
            "tiles": [{"x": 5, "y": 6, "previous_owner": b, "new_owner": a}],
        }
        assert [m["type"] for m in collect_until(ws1, "tiles_updated")] == [
            "card_used", "hand_updated", "cost_updated", "move_requested", "player_moved", "tiles_updated",
        ]
        other = collect_until(ws2, "tiles_updated")
        assert [m["type"] for m in other] == ["card_used", "move_requested", "player_moved", "tiles_updated"]
        assert other[-2:] == [moved, updated]
        assert sync(ws1) == [] and sync(ws2) == []

        assert room.players[a].position == (5, 6)
        assert room.tile_owners[6][5] == a
        assert room.tile_counts == {a: 1, b: 0}
        assert room.players[a].total_painted == 1

        ws2.send_json({"type": "room_state"})
        state_message = collect_until(ws2, "room_state")[-1]
        players = {p["user_id"]: p for p in state_message["players"]}
        assert state_message["tile_owners"][6][5] == a
        assert players[a]["position"] == [5, 6]
        assert players[a]["tile_count"] == 1 and players[b]["tile_count"] == 0

    records = read_log(room)
    names = [r["type"] for r in records]
    i = names.index("card_used")
    assert names[i:i + 5] == ["card_used", "cost_changed", "move_requested", "player_moved", "tile_painted"]
    assert records[i + 4]["data"] == {"user_id": a, "x": 5, "y": 6, "previous_owner": b, "new_owner": a}
    assert names.count("tile_painted") == 1


def test_only_changed_tiles_sent(client, users, now, monkeypatch):
    monkeypatch.setitem(cards.MOVE_STEPS, "move_dash", 4)
    map_data = grid(glass=[(5, 7)])
    monkeypatch.setattr(websocket, "load_map", lambda map_id: map_data)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a, b = users[0], users[1]
        set_position(room, a, (5, 5))
        with websocket.state_lock:
            room.tile_owners[6][5] = a
            room.tile_owners[8][5] = b
            room.tile_counts.update({a: 1, b: 1})
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 9})
        messages = collect_until(ws2, "tiles_updated")
        assert messages[-2]["path"] == [[5, 5], [5, 6], [5, 7], [5, 8], [5, 9]]
        assert messages[-1]["tiles"] == [
            {"x": 5, "y": 8, "previous_owner": b, "new_owner": a},
            {"x": 5, "y": 9, "previous_owner": None, "new_owner": a},
        ]
        assert room.tile_counts == owned(room) == {a: 3, b: 0}
        assert room.tile_owners[7][5] is None
        assert room.players[a].total_painted == 2
    assert [r["type"] for r in read_log(room)].count("tile_painted") == 2


@pytest.mark.parametrize("setup", ["glass", "own"])
def test_no_tiles_updated_without_change(client, users, now, monkeypatch, setup):
    map_data = grid(glass=[(5, 6)] if setup == "glass" else [])
    monkeypatch.setattr(websocket, "load_map", lambda map_id: map_data)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a = users[0]
        set_position(room, a, (5, 5))
        if setup == "own":
            with websocket.state_lock:
                room.tile_owners[6][5] = a
                room.tile_counts[a] = 1
                room.players[a].total_painted = 1
        _, tiles, counts, painted = state(room)
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 6})
        collect_until(ws1, "player_moved")
        collect_until(ws2, "player_moved")
        assert sync(ws1) == [] and sync(ws2) == []
        assert room.players[a].position == (5, 6)
        assert state(room)[1:] == (tiles, counts, painted)
    assert "tile_painted" not in [r["type"] for r in read_log(room)]


@pytest.mark.parametrize("target, code", [
    ({"target_x": 6, "target_y": 6}, "unreachable_target"),
    ({"target_x": 5, "target_y": 20}, "invalid_target"),
    ({"target_x": 5, "target_y": 5}, "invalid_target"),
    ({"target_x": 5, "target_y": 4}, "blocked_target"),
])
def test_invalid_move_keeps_everything(client, users, now, monkeypatch, target, code):
    map_data = grid(blocked=[(5, 4)])
    monkeypatch.setattr(websocket, "load_map", lambda map_id: map_data)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        set_position(room, users[0], (5, 5))
        before = snapshot(player), state(room)
        ws1.send_json({"type": "card_use", "hand_index": 0, **target})
        assert collect_until(ws1, "error")[0]["code"] == code
        assert (snapshot(player), state(room)) == before
        assert sync(ws2) == []
    names = set(r["type"] for r in read_log(room))
    assert not {"card_used", "player_moved", "tile_painted"} & names


def test_insufficient_cost_no_move_or_paint(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        set_position(room, users[0], (5, 5))
        with websocket.state_lock:
            player.cost = 1
        before = snapshot(player), state(room)
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 6})
        assert collect_until(ws1, "error")[0]["code"] == "insufficient_cost"
        assert (snapshot(player), state(room)) == before
    assert "tile_painted" not in [r["type"] for r in read_log(room)]


def test_ready_and_finished_rejected(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        before = state(room)
        target = {"target_x": room.players[users[0]].position[0] ^ 1, "target_y": room.players[users[0]].position[1]}
        ws1.send_json({"type": "card_use", "hand_index": 0, **target})
        assert collect_until(ws1, "error")[0]["code"] == "game_not_playing"
        assert state(room) == before

        now[0] = 310
        ws1.send_json({"type": "card_use", "hand_index": 0, **target})
        assert collect_until(ws1, "error")[0]["code"] == "game_not_playing"
        assert state(room) == before


def test_attack_and_legacy_move_do_not_paint(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        set_position(room, users[0], (5, 5))
        before = state(room)
        ws1.send_json({"type": "card_use", "hand_index": 1, "target_x": 5, "target_y": 6})
        collect_until(ws1, "attack_requested")
        ws1.send_json({"type": "card_use", "hand_index": 0})
        collect_until(ws1, "move_requested")
        assert sync(ws1) == []
        assert state(room) == before
        assert room.players[users[0]].cards_used == 2
    names = [r["type"] for r in read_log(room)]
    assert "player_moved" not in names and "tile_painted" not in names


def test_other_room_not_notified(client, users, now):
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
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 6, "target_y": 5})
        collect_until(ws2, "tiles_updated")
        assert sync(ws3) == [] and sync(ws4) == []
        assert all(owner is None for row in other.tile_owners for owner in row)
        assert room.tile_owners[5][6] == users[0]


def test_reconnect_restores_position_and_tiles(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        ws1 = client.websocket_connect(WS_URL).__enter__()
        try:
            room = start_game(ws1, ws2, users, now)
            set_position(room, users[0], (5, 5))
            ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 4, "target_y": 5})
            collect_until(ws2, "tiles_updated")
        finally:
            ws1.__exit__(None, None, None)
        collect_until(ws2, "player_disconnected")
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            message = ws1.receive_json()
            players = {p["user_id"]: p for p in message["players"]}
            assert players[users[0]]["position"] == [4, 5]
            assert players[users[0]]["tile_count"] == 1
            assert message["tile_owners"][5][4] == users[0]
            ws1.send_json({"type": "room_state"})
            assert collect_until(ws1, "room_state")[-1]["tile_owners"] == message["tile_owners"]
    names = [r["type"] for r in read_log(room)]
    assert names.count("tile_painted") == names.count("player_moved") == 1


def test_concurrent_move_and_paint(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a, b = users[0], users[1]
        set_position(room, a, (5, 5))
        set_position(room, b, (5, 7))
        with websocket.state_lock:
            room.players[a].hand = ["move_dash", "move_dash", "move_jump"]
            room.players[b].hand = ["move_dash", "move_dash", "move_jump"]
        barrier = threading.Barrier(4)

        def send(ws, index):
            barrier.wait()
            ws.send_json({"type": "card_use", "hand_index": index, "target_x": 5, "target_y": 6})

        threads = [threading.Thread(target=send, args=(ws, i)) for ws in (ws1, ws2) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seen = {ws1: [], ws2: []}
        for ws in (ws1, ws2):
            while sum(m["type"] in ("hand_updated", "error") for m in seen[ws]) < 2:
                seen[ws] += sync(ws)

        for uid, ws in ((a, ws1), (b, ws2)):
            player = room.players[uid]
            assert player.position == (5, 6)
            assert player.cards_used == 1 and player.cost == 4
            assert [m["code"] for m in seen[ws] if m["type"] == "error"] == ["invalid_target"]
        assert room.tile_counts == owned(room)
        assert sum(room.tile_counts.values()) == 1
        winner = room.tile_owners[6][5]
        assert room.players[winner].total_painted == 1

    records = read_log(room)
    painted = [r for r in records if r["type"] == "tile_painted"]
    assert [r["data"]["new_owner"] for r in painted][-1] == winner
    assert len([r for r in records if r["type"] == "player_moved"]) == 2
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
