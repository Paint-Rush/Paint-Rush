import threading

import pytest

from app import websocket
from app.attacks import check_attack, resolve_attack
from app.cards import CARDS
from app.game_room import RoomStatus, get_player_room
from app.results import calculate_results
from tests.test_action_events import collect_until, read_log, start_game, sync, users
from tests.test_card_use import snapshot
from tests.test_game_engine import client, now
from tests.test_movement import grid, room_at, set_position, state
from tests.test_movement_painting import owned
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def attack(room, card_id, target, map_data=None, user_id=1):
    map_data = map_data or grid()
    area = check_attack(room, user_id, card_id, *target, map_data, 10)
    return area, resolve_attack(room, user_id, card_id, area, map_data, 10)


def own(room, user_id, *points):
    for x, y in points:
        room.tile_owners[y][x] = user_id
        room.tile_counts[user_id] += 1


def test_slash_paints_adjacent_neutral_tile():
    room = room_at()
    area, tiles = attack(room, "attack_slash", (5, 6))
    assert area == [(5, 6)]
    assert tiles == [{"x": 5, "y": 6, "previous_owner": None, "new_owner": 1}]
    assert room.tile_owners[6][5] == 1
    assert room.tile_counts == {1: 1, 2: 0} == owned(room)
    assert room.players[1].total_painted == 1
    assert room.players[1].position == (5, 5)


def test_slash_overwrites_opponent_and_keeps_own_tile():
    room = room_at()
    own(room, 2, (6, 5))
    room.players[2].total_painted = 1
    _, tiles = attack(room, "attack_slash", (6, 5))
    assert tiles == [{"x": 6, "y": 5, "previous_owner": 2, "new_owner": 1}]
    assert room.tile_counts == {1: 1, 2: 0} == owned(room)
    assert room.players[2].total_painted == 1

    before = state(room)
    _, tiles = attack(room, "attack_slash", (6, 5))
    assert tiles == []
    assert state(room) == before


@pytest.mark.parametrize("position, target, code", [
    ((5, 5), (5, 5), "attack_out_of_range"),
    ((5, 5), (5, 7), "attack_out_of_range"),
    ((5, 5), (6, 6), "attack_out_of_range"),
    ((5, 5), (4, 4), "attack_out_of_range"),
    ((0, 5), (-1, 5), "invalid_attack_target"),
    ((19, 5), (20, 5), "invalid_attack_target"),
    ((5, 5), ("5", 6), "invalid_attack_target"),
    ((5, 5), (5, None), "invalid_attack_target"),
])
def test_slash_rejects_out_of_range_and_bad_targets(position, target, code):
    room = room_at(position)
    before = state(room)
    with pytest.raises(ValueError, match=code):
        check_attack(room, 1, "attack_slash", *target, grid(), 10)
    assert state(room) == before


@pytest.mark.parametrize("tile", ["blocked", "glass"])
def test_slash_rejects_blocked_and_glass(tile):
    room = room_at()
    map_data = grid(**{tile: [(5, 6)]})
    with pytest.raises(ValueError, match="unattackable_tile"):
        check_attack(room, 1, "attack_slash", 5, 6, map_data, 10)


def test_bomb_clears_only_opponent_normal_tiles():
    room = room_at(user_ids=(1, 2, 3))
    map_data = grid(blocked=[(4, 4)], glass=[(6, 4)])
    own(room, 2, (5, 4), (4, 5), (4, 4), (6, 4), (8, 8))
    own(room, 3, (6, 6))
    own(room, 1, (5, 5), (6, 5))
    painted = {uid: p.total_painted for uid, p in room.players.items()}
    area, tiles = attack(room, "attack_bomb", (5, 5), map_data)
    assert len(area) == 9
    assert sorted((t["x"], t["y"], t["previous_owner"]) for t in tiles) == [(4, 5, 2), (5, 4, 2), (6, 6, 3)]
    assert all(t["new_owner"] is None for t in tiles)
    for x, y in [(5, 4), (4, 5), (6, 6)]:
        assert room.tile_owners[y][x] is None
    assert room.tile_owners[4][4] == 2 and room.tile_owners[4][6] == 2
    assert room.tile_owners[8][8] == 2
    assert room.tile_owners[5][5] == 1 and room.tile_owners[5][6] == 1
    assert room.tile_counts == {1: 2, 2: 3, 3: 0} == owned(room)
    assert {uid: p.total_painted for uid, p in room.players.items()} == painted
    assert room.players[1].position == (5, 5)


def test_bomb_on_empty_area_succeeds_without_changes():
    room = room_at()
    before = state(room)
    area, tiles = attack(room, "attack_bomb", (6, 6))
    assert len(area) == 9 and tiles == []
    assert state(room) == before


@pytest.mark.parametrize("position, center, size", [
    ((0, 0), (0, 0), 4),
    ((0, 0), (0, 3), 6),
    ((19, 19), (19, 19), 4),
    ((19, 10), (19, 10), 6),
    ((5, 5), (5, 5), 9),
])
def test_bomb_area_clipped_to_map(position, center, size):
    room = room_at(position)
    area, _ = attack(room, "attack_bomb", center)
    assert len(area) == len(set(area)) == size
    assert all(0 <= x < 20 and 0 <= y < 20 and abs(x - center[0]) <= 1 and abs(y - center[1]) <= 1 for x, y in area)


@pytest.mark.parametrize("target, ok", [
    ((8, 5), True), ((5, 2), True), ((6, 7), True), ((7, 6), True),
    ((9, 5), False), ((8, 6), False), ((7, 7), False), ((20, 5), False),
])
def test_bomb_range(target, ok):
    room = room_at()
    if ok:
        attack(room, "attack_bomb", target)
        return
    before = state(room)
    with pytest.raises(ValueError, match="attack_out_of_range|invalid_attack_target"):
        check_attack(room, 1, "attack_bomb", *target, grid(), 10)
    assert state(room) == before


@pytest.mark.parametrize("t", [5, 310])
def test_attack_rejected_outside_play(t):
    room = room_at(t=t)
    with pytest.raises(ValueError, match="game_not_playing"):
        check_attack(room, 1, "attack_slash", 5, 6, grid(), t)


def test_attack_result_matches_final_tiles():
    room = room_at()
    own(room, 2, (5, 6), (5, 7))
    room.players[2].total_painted = 2
    attack(room, "attack_slash", (5, 6))
    attack(room, "attack_bomb", (5, 7))
    room.status = RoomStatus.FINISHED
    rows = {r["user_id"]: r for r in calculate_results(room)["results"]}
    assert rows[1]["painted_tiles"] == 1 and rows[2]["painted_tiles"] == 0
    assert rows[1]["total_painted"] == 1 and rows[2]["total_painted"] == 2
    assert rows[1]["kills"] == rows[1]["deaths"] == 0


def ws_state(room, user_id):
    return snapshot(room.players[user_id]), state(room)


def test_slash_over_websocket_sends_events_and_rotates(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a, b = users[0], users[1]
        player = room.players[a]
        set_position(room, a, (5, 5))
        with websocket.state_lock:
            room.tile_owners[6][5] = b
            room.tile_counts[b] += 1
        next_card = next(iter(player.draw_queue))
        queue = list(player.draw_queue)

        ws1.send_json({"type": "card_use", "hand_index": 1, "target_x": 5, "target_y": 6})
        tile = {"x": 5, "y": 6, "previous_owner": b, "new_owner": a}
        resolved = {
            "type": "attack_resolved", "room_id": str(room.room_id), "user_id": a, "card_id": "attack_slash",
            "target_x": 5, "target_y": 6, "affected_tiles": [[5, 6]],
        }
        painted = {"type": "tiles_updated", "room_id": str(room.room_id), "tiles": [tile]}
        assert collect_until(ws1, "tiles_updated") == [
            {"type": "card_used", "user_id": a, "card_id": "attack_slash", "hand_index": 1},
            {"type": "hand_updated", "hand": player.hand, "cost": 3},
            {"type": "cost_updated", "cost": 3, "max_cost": 10},
            {"type": "attack_requested", "user_id": a, "card_id": "attack_slash"},
            resolved,
            painted,
        ]
        assert collect_until(ws2, "tiles_updated") == [
            {"type": "card_used", "user_id": a, "card_id": "attack_slash", "hand_index": 1},
            {"type": "attack_requested", "user_id": a, "card_id": "attack_slash"},
            resolved,
            painted,
        ]
        assert player.hand[1] == next_card
        assert list(player.draw_queue) == queue[1:] + ["attack_slash"]
        assert player.cost == 3 and player.cards_used == 1 and player.cost_used == CARDS["attack_slash"].cost
        assert player.position == (5, 5)
        for ws, uid in ((ws1, a), (ws2, b)):
            current = sync(ws)
            assert current == []
            ws.send_json({"type": "room_state"})
            snapshot_state = collect_until(ws, "room_state")[-1]
            assert snapshot_state["tile_owners"][6][5] == a
            counts = {p["user_id"]: p["tile_count"] for p in snapshot_state["players"]}
            assert counts == room.tile_counts == owned(room) == {a: 1, b: 0}

    names = [r["type"] for r in read_log(room)]
    i = names.index("card_used")
    assert names[i:i + 5] == ["card_used", "cost_changed", "attack_requested", "tile_painted", "attack_resolved"]


def test_bomb_over_websocket_sends_null_owner(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a, b = users[0], users[1]
        set_position(room, a, (5, 5))
        with websocket.state_lock:
            room.players[a].hand = ["attack_bomb", "move_dash", "move_jump"]
            room.players[a].cost = 10
            for x, y in [(7, 7), (8, 8)]:
                room.tile_owners[y][x] = b
                room.tile_counts[b] += 1
            room.tile_owners[6][7] = a
            room.tile_counts[a] += 1

        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 7, "target_y": 6})
        own_messages = collect_until(ws1, "tiles_updated")
        other = collect_until(ws2, "tiles_updated")
        for messages in (own_messages, other):
            resolved = next(m for m in messages if m["type"] == "attack_resolved")
            assert resolved["card_id"] == "attack_bomb" and [resolved["target_x"], resolved["target_y"]] == [7, 6]
            assert sorted(map(tuple, resolved["affected_tiles"])) == sorted((x, y) for x in (6, 7, 8) for y in (5, 6, 7))
            assert messages[-1]["tiles"] == [{"x": 7, "y": 7, "previous_owner": b, "new_owner": None}]
        assert room.tile_owners[8][8] == b and room.tile_owners[6][7] == a
        assert room.tile_counts == owned(room) == {a: 1, b: 1}
        assert room.players[a].cost == 5 and room.players[a].cost_used == 5
        assert sync(ws1) == [] and sync(ws2) == []


def test_bomb_without_changes_sends_no_tiles_updated(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        set_position(room, users[0], (5, 5))
        with websocket.state_lock:
            room.players[users[0]].hand = ["attack_bomb", "move_dash", "move_jump"]
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 5})
        assert collect_until(ws1, "attack_resolved")[-1]["affected_tiles"]
        assert collect_until(ws2, "attack_resolved")
        assert sync(ws1) == [] and sync(ws2) == []
        assert room.players[users[0]].cards_used == 1


@pytest.mark.parametrize("change, message, code", [
    ({"cost": 2}, {"hand_index": 1, "target_x": 5, "target_y": 6}, "insufficient_cost"),
    ({}, {"hand_index": 1}, "target_required"),
    ({}, {"hand_index": 1, "target_x": 5}, "invalid_attack_target"),
    ({}, {"hand_index": 1, "target_x": 6, "target_y": 6}, "attack_out_of_range"),
    ({}, {"hand_index": 1, "target_x": 5, "target_y": 7}, "attack_out_of_range"),
    ({}, {"hand_index": 1, "target_x": 5, "target_y": 20}, "invalid_attack_target"),
    ({"bomb": True}, {"hand_index": 1, "target_x": 9, "target_y": 5}, "attack_out_of_range"),
    ({}, {"hand_index": 7, "target_x": 5, "target_y": 6}, "invalid_hand_index"),
])
def test_failed_attack_keeps_cost_hand_and_tiles(client, users, now, change, message, code):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a, b = users[0], users[1]
        set_position(room, a, (5, 5))
        with websocket.state_lock:
            if "cost" in change:
                room.players[a].cost = change["cost"]
            if change.get("bomb"):
                room.players[a].hand[1] = "attack_bomb"
            room.tile_owners[6][5] = b
            room.tile_counts[b] += 1
            before = ws_state(room, a)
        ws1.send_json({"type": "card_use", **message})
        reply = collect_until(ws1, "error")
        assert [m["type"] for m in reply] == ["error"]
        assert reply[0] == {"type": "error", "code": code, "message": websocket.CARD_ERRORS[code]}
        assert ws_state(room, a) == before
        assert sync(ws1) == [] and sync(ws2) == []
    names = [r["type"] for r in read_log(room)]
    assert not {"card_used", "attack_requested", "attack_resolved", "tile_painted", "tile_cleared"} & set(names)


@pytest.mark.parametrize("tile", ["BLOCKED", "GLASS"])
def test_failed_slash_on_special_tile_over_websocket(client, users, now, monkeypatch, tile):
    map_data = grid(**{tile.lower(): [(5, 6)]})
    monkeypatch.setattr(websocket, "load_map", lambda map_id: map_data)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        set_position(room, users[0], (5, 5))
        before = ws_state(room, users[0])
        ws1.send_json({"type": "card_use", "hand_index": 1, "target_x": 5, "target_y": 6})
        assert collect_until(ws1, "error")[-1]["code"] == "unattackable_tile"
        assert ws_state(room, users[0]) == before


def test_attack_rejected_in_ready(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        assert room.status is RoomStatus.READY
        x, y = room.players[users[0]].position
        before = ws_state(room, users[0])
        for index in range(3):
            ws1.send_json({"type": "card_use", "hand_index": index, "target_x": x, "target_y": y})
            assert collect_until(ws1, "error")[-1]["code"] == "game_not_playing"
        assert ws_state(room, users[0]) == before


def test_attack_rejected_after_finish(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        set_position(room, users[0], (5, 5))
        now[0] = 310
        assert collect_until(ws1, "game_finished")
        before = ws_state(room, users[0])
        ws1.send_json({"type": "card_use", "hand_index": 1, "target_x": 5, "target_y": 6})
        assert collect_until(ws1, "error")[-1]["code"] == "game_not_playing"
        assert ws_state(room, users[0]) == before


def test_concurrent_attacks_and_move_keep_state_consistent(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        a, b = users[0], users[1]
        set_position(room, a, (5, 5))
        set_position(room, b, (5, 7))
        with websocket.state_lock:
            room.players[a].hand = ["attack_slash", "move_dash", "attack_slash"]
            room.players[b].hand = ["attack_bomb", "attack_slash", "move_dash"]
            room.players[a].cost = room.players[b].cost = 10
            room.tile_owners[6][4] = b
            room.tile_counts[b] += 1
        requests = [
            (ws1, {"hand_index": 0, "target_x": 5, "target_y": 6}),
            (ws1, {"hand_index": 2, "target_x": 5, "target_y": 4}),
            (ws2, {"hand_index": 0, "target_x": 5, "target_y": 6}),
            (ws2, {"hand_index": 1, "target_x": 4, "target_y": 7}),
        ]
        barrier = threading.Barrier(len(requests))

        def send(ws, message):
            barrier.wait()
            ws.send_json({"type": "card_use", **message})

        threads = [threading.Thread(target=send, args=request) for request in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        for ws in (ws1, ws2):
            seen = []
            while sum(m["type"] in ("hand_updated", "error") for m in seen) < 2:
                seen += sync(ws)
            assert not [m for m in seen if m["type"] == "error"]

        assert room.tile_counts == owned(room)
        assert room.tile_owners[4][5] == a and room.tile_owners[7][4] == b and room.tile_owners[6][4] == b
        assert room.tile_owners[6][5] in (a, None)
        for uid, spent in ((a, 6), (b, 8)):
            player = room.players[uid]
            assert player.cards_used == 2 and player.cost_used == spent and player.cost == 10 - spent
            assert len(player.hand) == 3 and len(list(player.draw_queue)) == 3
        assert room.players[a].position == (5, 5) and room.players[b].position == (5, 7)

    records = read_log(room)
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
    assert [r["type"] for r in records].count("attack_resolved") == 4
