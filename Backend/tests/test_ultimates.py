import re
from pathlib import Path

import pytest

from app import ultimates, websocket
from app.characters import CHARACTERS
from app.game_engine import check_ultimate as check_ready
from app.game_room import RoomStatus
from app.ultimates import check_ultimate, resolve_ultimate
from tests.test_action_events import collect_until, read_log, start_game, sync, users
from tests.test_attacks import own
from tests.test_characters import playing_room, snapshot
from tests.test_game_engine import client, now
from tests.test_movement import grid
from tests.test_movement_painting import owned
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, wait_until

pytestmark = pytest.mark.usefixtures("default_map")


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def square(x, y):
    return sorted((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))


def cast(room, user_id=1, target=(None, None), map_data=None):
    map_data = map_data or grid()
    character = check_ready(room, user_id, 10)
    center, area = check_ultimate(room, user_id, character.ultimate_id, *target, map_data)
    return center, area, resolve_ultimate(room, user_id, character.ultimate_id, area, map_data, 10)


def burst_room():
    room = playing_room(character_ids={1: "character_01"})
    room.players[1].position = (5, 5)
    return room


def steal_room():
    room = playing_room(character_ids={1: "character_02"})
    room.players[1].position = (5, 5)
    return room


def test_color_burst_paints_neutral_and_enemy_normal_tiles():
    room = burst_room()
    own(room, 2, (4, 4), (6, 6))
    own(room, 1, (5, 4))
    map_data = grid(blocked=[(6, 4)], glass=[(4, 6)])
    positions = {uid: p.position for uid, p in room.players.items()}
    hp = {uid: p.hp for uid, p in room.players.items()}
    center, area, tiles = cast(room, target=(9, 9), map_data=map_data)
    assert center == (5, 5) and sorted(area) == square(5, 5)
    changed = {(t["x"], t["y"]): t["previous_owner"] for t in tiles}
    assert changed == {(4, 4): 2, (6, 6): 2, (4, 5): None, (6, 5): None, (5, 5): None, (5, 6): None}
    assert all(t["new_owner"] == 1 for t in tiles)
    assert room.tile_owners[4][6] is None and room.tile_owners[6][4] is None
    assert room.tile_counts == owned(room) == {1: 7, 2: 0}
    assert room.players[1].total_painted == 6
    assert {uid: p.position for uid, p in room.players.items()} == positions
    assert {uid: p.hp for uid, p in room.players.items()} == hp


def test_color_burst_clips_at_map_edge():
    room = burst_room()
    room.players[1].position = (0, 0)
    center, area, tiles = cast(room)
    assert center == (0, 0) and sorted(area) == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert len(tiles) == 4


def test_color_steal_takes_only_enemy_normal_tiles():
    room = steal_room()
    own(room, 2, (7, 6), (8, 7), (6, 7))
    own(room, 1, (7, 7))
    map_data = grid(glass=[(8, 7)])
    room.players[2].position = (7, 6)
    center, area, tiles = cast(room, target=(7, 6), map_data=map_data)
    assert center == (7, 6) and sorted(area) == square(7, 6)
    assert tiles == [
        {"x": 7, "y": 6, "previous_owner": 2, "new_owner": 1},
        {"x": 6, "y": 7, "previous_owner": 2, "new_owner": 1},
    ]
    assert room.tile_owners[7][8] == 2 and room.tile_owners[8][8] is None
    assert room.tile_counts == owned(room) == {1: 3, 2: 1}
    assert room.players[1].position == (5, 5) and room.players[2].position == (7, 6)
    assert room.players[2].hp == 3


def test_color_steal_defaults_to_own_position():
    room = steal_room()
    own(room, 2, (6, 6))
    center, area, tiles = cast(room)
    assert center == (5, 5) and sorted(area) == square(5, 5)
    assert tiles == [{"x": 6, "y": 6, "previous_owner": 2, "new_owner": 1}]


@pytest.mark.parametrize("target, code", [
    ((8, 6), "ultimate_out_of_range"),
    ((20, 5), "invalid_ultimate_target"),
    ((-1, 5), "invalid_ultimate_target"),
    ((5, None), "invalid_ultimate_target"),
    (("5", 5), "invalid_ultimate_target"),
    ((True, 5), "invalid_ultimate_target"),
])
def test_color_steal_rejects_bad_target(target, code):
    room = steal_room()
    own(room, 2, (8, 6))
    with pytest.raises(ValueError, match=code):
        check_ultimate(room, 1, "ultimate_02", *target, grid())
    assert room.players[1].ultimate_used is False
    assert room.tile_owners[6][8] == 2


def test_ultimate_rejects_missing_map():
    room = burst_room()
    with pytest.raises(ValueError, match="map_unavailable"):
        check_ultimate(room, 1, "ultimate_01", None, None, None)
    with pytest.raises(ValueError, match="map_unavailable"):
        check_ultimate(room, 1, "ultimate_01", None, None, grid() | {"map_id": "other"})


def test_ultimate_rejected_when_knocked_out():
    room = burst_room()
    room.players[1].knocked_out_at = 5
    room.players[1].alive = False
    with pytest.raises(ValueError, match="player_knocked_out"):
        check_ready(room, 1, 10)
    assert room.players[1].ultimate_used is False


def start_ultimate(ws1, ws2, users, now, character_id):
    room = start_game(ws1, ws2, users, now)
    with websocket.state_lock:
        room.players[users[0]].character_id = character_id
        room.players[users[0]].position = (5, 5)
    return room


@pytest.mark.usefixtures("immediate_matchmaking")
def test_color_burst_over_websocket(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_ultimate(ws1, ws2, users, now, "character_01")
        a, b = users[0], users[1]
        with websocket.state_lock:
            own(room, b, (4, 4))
        before = snapshot(room, a)[:5]
        ws1.send_json({"type": "ultimate_use"})
        for ws in (ws1, ws2):
            activated, painted = collect_until(ws, "tiles_updated")
            assert activated["type"] == "ultimate_activated"
            assert activated["room_id"] == str(room.room_id) and activated["user_id"] == a
            assert activated["character_id"] == "character_01" and activated["ultimate_id"] == "ultimate_01"
            assert [activated["target_x"], activated["target_y"]] == [5, 5]
            assert sorted(map(tuple, activated["affected_tiles"])) == square(5, 5)
            assert sorted(map(tuple, activated["changed_tiles"])) == square(5, 5)
            assert painted["room_id"] == str(room.room_id) and len(painted["tiles"]) == 9
            assert {"x": 4, "y": 4, "previous_owner": b, "new_owner": a} in painted["tiles"]
        assert room.players[a].ultimate_used is True
        assert snapshot(room, a)[:5] == before
        assert room.tile_counts == owned(room) == {a: 9, b: 0}
        assert sync(ws2) == []

        ws1.send_json({"type": "room_state"})
        state = collect_until(ws1, "room_state")[-1]
        me = next(p for p in state["players"] if p["user_id"] == a)
        assert state["my_ultimate_used"] is True and me["tile_count"] == 9 and me["position"] == [5, 5]

        ws1.send_json({"type": "ultimate_use"})
        assert collect_until(ws1, "error")[-1]["code"] == "ultimate_already_used"
        assert sync(ws2) == []
    log = read_log(room)
    assert [r["data"] for r in log if r["type"] == "ultimate_activated"] == [
        {"user_id": a, "character_id": "character_01", "ultimate_id": "ultimate_01"},
    ]
    assert sum(r["type"] == "tile_painted" for r in log) == 9


@pytest.mark.usefixtures("immediate_matchmaking")
def test_color_steal_over_websocket_bad_target_keeps_ultimate(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_ultimate(ws1, ws2, users, now, "character_02")
        a, b = users[0], users[1]
        with websocket.state_lock:
            own(room, b, (7, 6), (6, 6))
        for message, code in [
            ({"target_x": 9, "target_y": 6}, "ultimate_out_of_range"),
            ({"target_x": 99, "target_y": 6}, "invalid_ultimate_target"),
            ({"target_x": 7}, "invalid_ultimate_target"),
        ]:
            ws1.send_json({"type": "ultimate_use", **message})
            assert collect_until(ws1, "error")[-1]["code"] == code
            assert room.players[a].ultimate_used is False
        assert sync(ws2) == []

        ws1.send_json({"type": "ultimate_use", "target_x": 7, "target_y": 6})
        for ws in (ws1, ws2):
            activated, painted = collect_until(ws, "tiles_updated")
            assert activated["ultimate_id"] == "ultimate_02"
            assert [activated["target_x"], activated["target_y"]] == [7, 6]
            assert sorted(map(tuple, activated["changed_tiles"])) == [(6, 6), (7, 6)]
            assert sorted((t["x"], t["y"], t["previous_owner"], t["new_owner"]) for t in painted["tiles"]) == [
                (6, 6, b, a), (7, 6, b, a),
            ]
        assert room.tile_counts == owned(room) == {a: 2, b: 0}
        assert room.players[a].ultimate_used is True


@pytest.mark.usefixtures("immediate_matchmaking")
def test_color_steal_without_change_sends_no_tiles_updated(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_ultimate(ws1, ws2, users, now, "character_02")
        ws1.send_json({"type": "ultimate_use", "target_x": 6, "target_y": 6})
        activated = ws1.receive_json()
        assert activated["type"] == "ultimate_activated" and activated["changed_tiles"] == []
        assert sync(ws1) == []
        assert room.players[users[0]].ultimate_used is True


@pytest.mark.usefixtures("immediate_matchmaking")
def test_ultimate_rejected_in_ready_finished_and_knocked_out(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = websocket.get_player_room(users[0])
        ws1.send_json({"type": "ultimate_use"})
        assert collect_until(ws1, "error")[-1]["code"] == "game_not_playing"

        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        with websocket.state_lock:
            room.players[users[0]].knocked_out_at = 10
            room.players[users[0]].alive = False
        ws1.send_json({"type": "ultimate_use"})
        assert collect_until(ws1, "error")[-1]["code"] == "player_knocked_out"

        now[0] = 310
        assert wait_until(lambda: room.status is RoomStatus.FINISHED)
        ws2.send_json({"type": "ultimate_use"})
        assert collect_until(ws2, "error")[-1]["code"] == "game_not_playing"
        assert not any(p.ultimate_used for p in room.players.values())
        assert all(count == 0 for count in room.tile_counts.values())


@pytest.mark.usefixtures("immediate_matchmaking")
def test_reconnect_keeps_ultimate_used_after_effect(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            room = start_ultimate(ws1, ws2, users, now, "character_01")
            ws1.send_json({"type": "ultimate_use"})
            collect_until(ws1, "tiles_updated")
            collect_until(ws2, "tiles_updated")
        assert wait_until(lambda: not room.players[users[0]].connected)

        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            state = collect_until(ws1, "room_state")[-1]
            assert state["my_ultimate_used"] is True
            assert sum(row.count(users[0]) for row in state["tile_owners"]) == 9
            ws1.send_json({"type": "ultimate_use"})
            assert collect_until(ws1, "error")[-1]["code"] == "ultimate_already_used"
        assert room.tile_counts[users[0]] == 9


def legacy_check(position, ultimate_id, target_x, target_y, map_data):
    if ultimate_id not in ("ultimate_01", "ultimate_02"):
        raise ValueError("unknown_ultimate")
    if ultimate_id == "ultimate_01" or (target_x is None and target_y is None):
        target = position
    else:
        if type(target_x) is not int or type(target_y) is not int:
            raise ValueError("invalid_ultimate_target")
        if not (0 <= target_x < map_data["width"] and 0 <= target_y < map_data["height"]):
            raise ValueError("invalid_ultimate_target")
        if abs(position[0] - target_x) + abs(position[1] - target_y) > 3:
            raise ValueError("ultimate_out_of_range")
        target = (target_x, target_y)
    x, y = target
    area = [
        (x + dx, y + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
        if 0 <= x + dx < map_data["width"] and 0 <= y + dy < map_data["height"]
    ]
    return target, area


def legacy_changes(owners, area, map_data, user_id, ultimate_id):
    return [
        (x, y) for x, y in area
        if map_data["tiles"][y][x] == "NORMAL" and owners[y][x] != user_id
        and not (ultimate_id == "ultimate_02" and owners[y][x] is None)
    ]


def test_ultimate_registry_lists_existing_ultimates():
    assert list(ultimates.ULTIMATES) == ["ultimate_01", "ultimate_02"]
    assert ultimates.ULTIMATES["ultimate_01"] == ultimates.Ultimate(
        "ultimate_01", targeted=False, max_range=0, radius=1, enemy_only=False,
    )
    assert ultimates.ULTIMATES["ultimate_02"] == ultimates.Ultimate(
        "ultimate_02", targeted=True, max_range=3, radius=1, enemy_only=True,
    )
    assert all(key == u.ultimate_id for key, u in ultimates.ULTIMATES.items())
    assert {c.ultimate_id for c in CHARACTERS.values()} <= set(ultimates.ULTIMATES)


@pytest.mark.parametrize("ultimate_id", ["ultimate_03", "", None, 1, ["ultimate_01"]])
def test_unregistered_ultimate_rejected(ultimate_id):
    room = burst_room()
    assert ultimates.get_ultimate(ultimate_id) is None
    with pytest.raises(ValueError, match="unknown_ultimate"):
        check_ultimate(room, 1, ultimate_id, None, None, grid())
    with pytest.raises(ValueError, match="unknown_ultimate"):
        resolve_ultimate(room, 1, ultimate_id, [(5, 5)], grid(), 10)
    assert room.tile_owners[5][5] is None and room.players[1].ultimate_used is False


def test_registry_drives_target_rules(monkeypatch):
    room = burst_room()
    wide = ultimates.Ultimate("ultimate_test", targeted=True, max_range=5, radius=2, enemy_only=False)
    monkeypatch.setitem(ultimates.ULTIMATES, wide.ultimate_id, wide)
    center, area = check_ultimate(room, 1, "ultimate_test", 10, 5, grid())
    assert center == (10, 5)
    assert sorted(area) == sorted((10 + dx, 5 + dy) for dx in range(-2, 3) for dy in range(-2, 3))
    with pytest.raises(ValueError, match="ultimate_out_of_range"):
        check_ultimate(room, 1, "ultimate_test", 11, 5, grid())
    assert check_ultimate(room, 1, "ultimate_01", 10, 5, grid())[0] == (5, 5)


@pytest.mark.parametrize("ultimate_id", ["ultimate_01", "ultimate_02"])
def test_registry_matches_legacy_rules(ultimate_id):
    map_data = grid(blocked=[(2, 2), (7, 3)], glass=[(3, 3), (6, 8)])
    room = playing_room(character_ids={1: "character_01"})
    owners = [[(x * 7 + y * 3) % 3 or None for x in range(20)] for y in range(20)]
    for position in [(0, 0), (5, 5), (19, 0), (3, 3), (10, 19)]:
        for target in [(None, None), (5, 5), (position[0] + 3, position[1]), (position[0] + 2, position[1] + 2), (25, 1), (1, None)]:
            room.players[1].position = position
            try:
                expected = legacy_check(position, ultimate_id, *target, map_data)
            except ValueError as e:
                with pytest.raises(ValueError, match=str(e)):
                    check_ultimate(room, 1, ultimate_id, *target, map_data)
                continue
            center, area = check_ultimate(room, 1, ultimate_id, *target, map_data)
            assert (center, sorted(area)) == (expected[0], sorted(expected[1]))
            room.tile_owners = [row[:] for row in owners]
            room.tile_counts = {1: 0, 2: 0}
            for y, row in enumerate(owners):
                for x, owner in enumerate(row):
                    if owner is not None:
                        room.tile_counts[owner] += 1
            changed = resolve_ultimate(room, 1, ultimate_id, area, map_data, 10)
            assert [(t["x"], t["y"]) for t in changed] == legacy_changes(owners, area, map_data, 1, ultimate_id)
            assert all(t["previous_owner"] == owners[t["y"]][t["x"]] and t["new_owner"] == 1 for t in changed)


def test_frontend_registry_matches_backend():
    source = (Path(__file__).resolve().parents[2] / "Frontend" / "src" / "game" / "roomView.ts").read_text(encoding="utf-8")
    constants = dict(re.findall(r"export const (\w+) = '([^']+)';", source))
    numbers = {name: int(value) for name, value in re.findall(r"export const (\w+) = (\d+);", source)}
    entries = re.findall(
        r"\[(\w+)\]: \{ targeted: (true|false), maxRange: (\w+), radius: (\d+), enemyOnly: (true|false) \}", source,
    )
    frontend = {
        constants[name]: ultimates.Ultimate(
            constants[name], targeted == "true",
            numbers[max_range] if max_range in numbers else int(max_range),
            int(radius), enemy_only == "true",
        )
        for name, targeted, max_range, radius, enemy_only in entries
    }
    assert frontend == ultimates.ULTIMATES
