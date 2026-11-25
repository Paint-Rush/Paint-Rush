import json
from itertools import combinations

import pytest

from app import attacks, maps, matchmaking, ultimates
from app.algorithms.bfs import find_path, is_possible
from app.cards import DEFAULT_DECK, initialize_deck
from app.game_engine import GAME_SECONDS, READY_SECONDS, RESPAWN_SECONDS, advance, start
from app.game_room import (
    MAP_SIZE,
    SPAWN_POINTS,
    RoomStatus,
    create_room,
    get_player_room,
    remove_room,
    set_player_connected,
)
from app.maps import TILE_TYPES, load_map, validate_map
from app.matchmaking import join_queue, try_match
from app.movement import check_move, move_player, paint_path
from app.painting import paint_tile
from app.results import calculate_results
from tests.test_card_use import NoShuffle
from tests.test_game_engine import client, now
from tests.test_match_sizes import close_sockets, join_all, open_sockets, players
from tests.test_websocket import WS_URL, auth, clear_state, wait_until

PLAY_MAPS = ("default", "crossroads", "glass_garden")
CENTER = [(9, 9), (10, 9), (9, 10), (10, 10)]


@pytest.fixture(autouse=True)
def clean():
    clear_state()
    yield
    clear_state()


def playing(map_id, user_ids=(1, 2), t=READY_SECONDS):
    room = create_room(list(user_ids), map_id=map_id)
    start(room, 0)
    for player in room.players.values():
        initialize_deck(player, DEFAULT_DECK, NoShuffle())
    advance(room, t)
    return room


def tiles_of(map_data, kind):
    return [
        (x, y)
        for y, row in enumerate(map_data["tiles"])
        for x, tile in enumerate(row)
        if tile == kind
    ]


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_play_map_loads(map_id):
    map_data = load_map(map_id)
    assert map_data["map_id"] == map_id
    assert isinstance(map_data["name"], str) and map_data["name"]
    assert map_data["width"] == map_data["height"] == MAP_SIZE == 20
    assert len(map_data["tiles"]) == 20 and all(len(row) == 20 for row in map_data["tiles"])


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_play_map_json_and_tiles(map_id):
    raw = json.loads(maps.map_path(map_id).read_text(encoding="utf-8"))
    assert set(raw) == {"map_id", "name", "width", "height", "tiles"}
    validate_map(raw)
    assert {tile for row in raw["tiles"] for tile in row} <= set(TILE_TYPES)


def test_new_maps_use_their_terrain():
    crossroads = load_map("crossroads")
    garden = load_map("glass_garden")
    assert tiles_of(crossroads, "BLOCKED") and not tiles_of(crossroads, "GLASS")
    assert tiles_of(garden, "GLASS")
    assert len(tiles_of(garden, "BLOCKED")) < len(tiles_of(garden, "GLASS"))
    assert all(
        crossroads["tiles"][y][x] == "NORMAL"
        for y in range(8, 12) for x in range(20)
    ) and all(
        crossroads["tiles"][y][x] == "NORMAL"
        for y in range(20) for x in range(9, 11)
    )


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_map_is_symmetric_for_all_spawns(map_id):
    tiles = load_map(map_id)["tiles"]
    assert tiles == tiles[::-1]
    assert tiles == [row[::-1] for row in tiles]


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_spawns_walkable(map_id):
    map_data = load_map(map_id)
    assert len(SPAWN_POINTS) == 4
    for x, y in SPAWN_POINTS:
        assert is_possible(map_data, x, y)


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_paths_between_all_spawns(map_id):
    map_data = load_map(map_id)
    for a, b in combinations(SPAWN_POINTS, 2):
        path = find_path(map_data, a, b)
        assert path is not None and path[0] == a and path[-1] == b
        assert all(is_possible(map_data, x, y) for x, y in path)
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            assert abs(ax - bx) + abs(ay - by) == 1


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_center_reachable_from_all_spawns(map_id):
    map_data = load_map(map_id)
    center = [point for point in CENTER if is_possible(map_data, *point)]
    assert center
    lengths = set()
    for spawn in SPAWN_POINTS:
        for point in center:
            assert find_path(map_data, spawn, point) is not None
        lengths.add(min(len(find_path(map_data, spawn, point)) for point in center))
    assert len(lengths) == 1


def test_only_play_maps_are_candidates():
    assert matchmaking.MAP_IDS == PLAY_MAPS
    assert matchmaking.available_maps() == list(PLAY_MAPS)


def test_test_map_excluded():
    assert "test" in maps.list_maps()
    load_map("test")
    assert "test" not in matchmaking.MAP_IDS
    assert "test" not in matchmaking.available_maps()
    assert "test" not in {matchmaking.choose_map() for _ in range(300)}


def test_random_choice_covers_all_play_maps():
    assert {matchmaking.choose_map() for _ in range(300)} == set(PLAY_MAPS)


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_try_match_assigns_chosen_map(now, monkeypatch, map_id):
    monkeypatch.setattr(matchmaking.random, "choice", lambda options: map_id if map_id in options else None)
    for user_id in (1, 2, 3, 4):
        join_queue(user_id)
    assert try_match().map_id == map_id


def test_missing_map_handling(monkeypatch):
    with pytest.raises(FileNotFoundError):
        load_map("nowhere")
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("nowhere", "crossroads", "BAD ID"))
    assert matchmaking.available_maps() == ["crossroads"]
    assert {matchmaking.choose_map() for _ in range(30)} == {"crossroads"}
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("nowhere",))
    assert matchmaking.choose_map() == "default"


@pytest.mark.parametrize("map_id", PLAY_MAPS)
@pytest.mark.parametrize("size", [2, 4])
def test_match_sizes_on_each_map(now, monkeypatch, map_id, size):
    monkeypatch.setattr(matchmaking, "MAP_IDS", (map_id,))
    monkeypatch.setattr(matchmaking, "MATCH_SIZE", size)
    user_ids = list(range(1, size + 1))
    now[0] = 0
    for user_id in user_ids:
        join_queue(user_id)
    room = try_match()
    assert room.map_id == map_id
    assert room.player_order == user_ids
    assert len({p.spawn for p in room.players.values()}) == size
    assert {p.spawn for p in room.players.values()} <= set(SPAWN_POINTS)
    map_data = load_map(map_id)
    assert all(is_possible(map_data, *p.position) for p in room.players.values())


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_map_id_fixed_through_match_and_reconnect(map_id):
    room = playing(map_id, (1, 2, 3, 4))
    set_player_connected(1, False)
    set_player_connected(1, True)
    assert get_player_room(1) is room and room.map_id == map_id
    for t in (READY_SECONDS + 100, READY_SECONDS + GAME_SECONDS):
        advance(room, t)
        assert room.map_id == map_id
    assert room.status is RoomStatus.FINISHED


def test_rematch_can_assign_new_map(now, monkeypatch):
    picks = iter(["crossroads", "glass_garden"])
    monkeypatch.setattr(matchmaking.random, "choice", lambda options: next(picks))
    for user_id in (1, 2, 3, 4):
        join_queue(user_id)
    first = try_match()
    assert first.map_id == "crossroads"
    remove_room(first.room_id)
    for user_id in (1, 2, 3, 4):
        join_queue(user_id)
    second = try_match()
    assert second.map_id == "glass_garden" and first.map_id == "crossroads"


@pytest.mark.parametrize("map_id", ["crossroads", "glass_garden"])
def test_ws_reconnect_restores_map(client, players, now, monkeypatch, map_id):
    monkeypatch.setattr(matchmaking, "MAP_IDS", (map_id,))
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
    assert found["map_id"] == map_id
    assert state["type"] == "room_state"
    assert state["map_id"] == room.map_id == map_id
    assert state["map"] == {"width": 20, "height": 20, "tiles": load_map(map_id)["tiles"]}


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_movement_valid_on_each_map(map_id):
    map_data = load_map(map_id)
    room = playing(map_id)
    player = room.players[1]
    start_point = player.position
    x, y = start_point
    target = next(
        point for point in [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
        if is_possible(map_data, *point)
    )
    path = move_player(room, 1, *target, map_data, 3, READY_SECONDS)
    assert path == [start_point, target] and player.position == target
    painted = paint_path(room, 1, path, map_data, READY_SECONDS)
    assert len(painted) == (map_data["tiles"][target[1]][target[0]] == "NORMAL")


def test_blocked_movement_on_crossroads():
    map_data = load_map("crossroads")
    room = playing("crossroads")
    room.players[1].position = (1, 3)
    assert map_data["tiles"][3][2] == "BLOCKED"
    with pytest.raises(ValueError, match="blocked_target"):
        check_move(room, 1, 2, 3, map_data, 3, READY_SECONDS)
    with pytest.raises(ValueError, match="unreachable_target"):
        check_move(room, 1, 5, 3, map_data, 4, READY_SECONDS)
    path = check_move(room, 1, 5, 3, map_data, 8, READY_SECONDS)
    assert len(path) - 1 == 8
    assert all(map_data["tiles"][y][x] != "BLOCKED" for x, y in path)
    for x, y in tiles_of(map_data, "BLOCKED"):
        assert not is_possible(map_data, x, y)


def test_glass_walkable_not_paintable_on_glass_garden():
    map_data = load_map("glass_garden")
    room = playing("glass_garden")
    assert map_data["tiles"][1][1] == "GLASS" and map_data["tiles"][1][0] == "NORMAL"
    room.players[1].position = (0, 0)
    path = move_player(room, 1, 1, 1, map_data, 3, READY_SECONDS)
    assert room.players[1].position == (1, 1) and len(path) == 3
    painted = paint_path(room, 1, path, map_data, READY_SECONDS)
    assert [(t["x"], t["y"]) for t in painted] == [path[1]]
    assert room.tile_owners[1][1] is None
    room.players[1].position = (2, 2)
    path = move_player(room, 1, 2, 3, map_data, 1, READY_SECONDS)
    assert path == [(2, 2), (2, 3)]
    assert [(t["x"], t["y"]) for t in paint_path(room, 1, path, map_data, READY_SECONDS)] == [(2, 3)]
    painted = [owner for row in room.tile_owners for owner in row if owner == 1]
    for x, y in tiles_of(map_data, "GLASS"):
        assert paint_tile(room, 1, x, y, map_data, READY_SECONDS) is False
        assert room.tile_owners[y][x] is None
    assert room.tile_counts[1] == len(painted) == room.players[1].total_painted


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_slash_targets_on_each_map(map_id):
    map_data = load_map(map_id)
    room = playing(map_id)
    for x in range(20):
        for y in range(20):
            if not is_possible(map_data, x, y):
                continue
            room.players[1].position = (x, y)
            for nx, ny in [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]:
                if not (0 <= nx < 20 and 0 <= ny < 20):
                    continue
                if map_data["tiles"][ny][nx] == "NORMAL":
                    assert attacks.check_attack(room, 1, attacks.SLASH, nx, ny, map_data, READY_SECONDS) == [(nx, ny)]
                else:
                    with pytest.raises(ValueError, match="unattackable_tile"):
                        attacks.check_attack(room, 1, attacks.SLASH, nx, ny, map_data, READY_SECONDS)


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_bomb_clears_only_enemy_normal_tiles(map_id):
    map_data = load_map(map_id)
    room = playing(map_id)
    for y in range(20):
        for x in range(20):
            room.tile_owners[y][x] = 2 if map_data["tiles"][y][x] == "NORMAL" else None
    room.tile_counts[2] = len(tiles_of(map_data, "NORMAL"))
    room.players[1].position = (9, 8)
    area = attacks.check_attack(room, 1, attacks.BOMB, 9, 10, map_data, READY_SECONDS)
    assert sorted(area) == sorted((x, y) for x in range(8, 11) for y in range(9, 12))
    cleared = attacks.resolve_attack(room, 1, attacks.BOMB, area, map_data, READY_SECONDS)
    normal_in_area = [(x, y) for x, y in area if map_data["tiles"][y][x] == "NORMAL"]
    assert sorted((t["x"], t["y"]) for t in cleared) == sorted(normal_in_area)
    assert room.tile_counts[2] == len(tiles_of(map_data, "NORMAL")) - len(normal_in_area)
    assert sum(owner == 2 for row in room.tile_owners for owner in row) == room.tile_counts[2]
    corner = attacks.bomb_area(map_data, 0, 0)
    assert sorted(corner) == [(0, 0), (0, 1), (1, 0), (1, 1)]


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_ultimates_change_only_normal_tiles(map_id):
    map_data = load_map(map_id)
    room = playing(map_id)
    for center in [(x, y) for y in range(20) for x in range(20) if is_possible(map_data, x, y)]:
        room.players[1].position = center
        target, area = ultimates.check_ultimate(room, 1, ultimates.COLOR_BURST, None, None, map_data)
        assert target == center
        changed = ultimates.resolve_ultimate(room, 1, ultimates.COLOR_BURST, area, map_data, READY_SECONDS)
        assert all(map_data["tiles"][t["y"]][t["x"]] == "NORMAL" for t in changed)
    assert room.tile_counts[1] == len(tiles_of(map_data, "NORMAL"))
    for x, y in tiles_of(map_data, "GLASS") + tiles_of(map_data, "BLOCKED"):
        assert room.tile_owners[y][x] is None
    room.players[2].position = (9, 8)
    _, area = ultimates.check_ultimate(room, 2, ultimates.COLOR_STEAL, 9, 10, map_data)
    stolen = ultimates.resolve_ultimate(room, 2, ultimates.COLOR_STEAL, area, map_data, READY_SECONDS)
    assert sorted((t["x"], t["y"]) for t in stolen) == sorted(
        (x, y) for x, y in area if map_data["tiles"][y][x] == "NORMAL"
    )
    assert room.tile_counts[1] + room.tile_counts[2] == len(tiles_of(map_data, "NORMAL"))


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_knockout_respawns_at_spawn(map_id):
    map_data = load_map(map_id)
    room = playing(map_id)
    victim = room.players[2]
    spawn = victim.spawn
    victim.position = (9, 9) if is_possible(map_data, 9, 9) else (8, 9)
    for _ in range(3):
        attacks.hit_players(room, 1, [victim.position], READY_SECONDS)
    assert not victim.alive and victim.knocked_out_at == READY_SECONDS
    advance(room, READY_SECONDS + RESPAWN_SECONDS)
    assert victim.alive and victim.position == spawn and is_possible(map_data, *spawn)


@pytest.mark.parametrize("map_id", PLAY_MAPS)
def test_tile_count_and_result_on_each_map(map_id):
    map_data = load_map(map_id)
    room = playing(map_id)
    normal = tiles_of(map_data, "NORMAL")
    for x, y in normal[:30]:
        paint_tile(room, 1, x, y, map_data, READY_SECONDS)
    for x, y in normal[30:40]:
        paint_tile(room, 2, x, y, map_data, READY_SECONDS)
    assert room.tile_counts == {1: 30, 2: 10}
    advance(room, READY_SECONDS + GAME_SECONDS)
    assert room.status is RoomStatus.FINISHED
    result = calculate_results(room)
    assert result["winner_id"] == 1
    assert [(r["user_id"], r["painted_tiles"]) for r in result["results"]] == [(1, 30), (2, 10)]
