import copy
import random

import pytest

from app import game_engine, game_room
from app.game_engine import advance, start
from app.game_room import MAP_SIZE, RoomStatus, create_room
from app.maps import create_default_map, set_tile
from app.painting import paint_tile


@pytest.fixture(autouse=True)
def clear_rooms():
    game_room.rooms.clear()
    game_room.player_rooms.clear()
    yield
    game_room.rooms.clear()
    game_room.player_rooms.clear()


@pytest.fixture
def map_data():
    data = create_default_map()
    set_tile(data, 1, 0, "GLASS")
    set_tile(data, 2, 0, "BLOCKED")
    return data


def make_room(user_ids=(1, 2), t=10):
    room = create_room(list(user_ids))
    start(room, 0)
    advance(room, t)
    return room


def snapshot(room):
    return (
        copy.deepcopy(room.tile_owners),
        dict(room.tile_counts),
        {uid: p.total_painted for uid, p in room.players.items()},
    )


def owned(room, user_id):
    return sum(row.count(user_id) for row in room.tile_owners)


def test_paint_empty_tile(map_data):
    room = make_room()
    assert paint_tile(room, 1, 5, 3, map_data, 10) is True
    assert room.tile_owners[3][5] == 1
    assert room.tile_counts == {1: 1, 2: 0}
    assert room.players[1].total_painted == 1
    assert room.players[2].total_painted == 0


def test_overwrite_opponent_tile(map_data):
    room = make_room()
    paint_tile(room, 1, 4, 4, map_data, 10)
    assert paint_tile(room, 2, 4, 4, map_data, 10) is True
    assert room.tile_owners[4][4] == 2
    assert room.tile_counts == {1: 0, 2: 1}
    assert room.players[1].total_painted == 1
    assert room.players[2].total_painted == 1


def test_repaint_own_tile(map_data):
    room = make_room()
    paint_tile(room, 1, 4, 4, map_data, 10)
    before = snapshot(room)
    assert paint_tile(room, 1, 4, 4, map_data, 10) is False
    assert snapshot(room) == before


@pytest.mark.parametrize("x, y", [(1, 0), (2, 0)])
def test_unpaintable_tiles(map_data, x, y):
    room = make_room()
    before = snapshot(room)
    assert paint_tile(room, 1, x, y, map_data, 10) is False
    assert room.tile_owners[y][x] is None
    assert snapshot(room) == before


@pytest.mark.parametrize("user_id", [3, 0, -1, None, "1", True, 1.0])
def test_invalid_user(map_data, user_id):
    room = make_room()
    before = snapshot(room)
    with pytest.raises(ValueError):
        paint_tile(room, user_id, 0, 0, map_data, 10)
    assert snapshot(room) == before


@pytest.mark.parametrize("x, y", [
    (-1, 0), (0, -1), (MAP_SIZE, 0), (0, MAP_SIZE), (100, 100),
    (True, 0), (0, False), (1.0, 0), ("1", 0), (None, 0), (0, [1]),
])
def test_invalid_coordinates(map_data, x, y):
    room = make_room()
    before = snapshot(room)
    with pytest.raises(ValueError):
        paint_tile(room, 1, x, y, map_data, 10)
    assert snapshot(room) == before


def test_invalid_map_data(map_data):
    room = make_room()
    before = snapshot(room)
    broken = copy.deepcopy(map_data)
    broken["tiles"][0][0] = "LAVA"
    other = create_default_map("other")
    small = {**create_default_map(), "width": 10, "height": 10, "tiles": [["NORMAL"] * 10 for _ in range(10)]}
    for data in (broken, other, small, None):
        with pytest.raises(ValueError):
            paint_tile(room, 1, 0, 0, data, 10)
    room.tile_owners.pop()
    with pytest.raises(ValueError):
        paint_tile(room, 1, 0, 0, map_data, 10)
    room.tile_owners.append([None] * MAP_SIZE)
    assert snapshot(room) == before


@pytest.mark.parametrize("t, now", [(5, 5), (310, 310), (309, 310), (309, 400)])
def test_rejected_when_not_playing(map_data, t, now):
    room = make_room(t=t)
    before = snapshot(room)
    with pytest.raises(ValueError):
        paint_tile(room, 1, 0, 0, map_data, now)
    assert snapshot(room) == before


def test_rejected_without_timer(map_data):
    room = create_room([1, 2])
    with pytest.raises(ValueError):
        paint_tile(room, 1, 0, 0, map_data, 10)


def test_paint_while_playing(map_data):
    room = make_room(t=100)
    assert room.status is RoomStatus.PLAYING
    assert paint_tile(room, 1, 0, 0, map_data, 100)


def test_paint_while_deathmatch(map_data):
    room = make_room(t=260)
    assert room.status is RoomStatus.DEATHMATCH
    assert paint_tile(room, 2, 19, 19, map_data, 260)
    assert room.tile_counts[2] == 1


def test_default_clock_used(map_data, monkeypatch):
    room = make_room()
    monkeypatch.setattr(game_engine, "clock", lambda: 10.0)
    assert paint_tile(room, 1, 0, 0, map_data)
    monkeypatch.setattr(game_engine, "clock", lambda: 400.0)
    with pytest.raises(ValueError):
        paint_tile(room, 1, 1, 1, map_data)


def test_two_player_room(map_data):
    room = make_room()
    for x in range(5):
        paint_tile(room, 1, x, 5, map_data, 10)
    for x in range(3, 8):
        paint_tile(room, 2, x, 5, map_data, 10)
    assert room.tile_owners[5][:9] == [1, 1, 1, 2, 2, 2, 2, 2, None]
    assert room.tile_counts == {1: 3, 2: 5}
    assert room.players[1].total_painted == 5
    assert room.players[2].total_painted == 5


def test_four_player_room(map_data):
    room = make_room([1, 2, 3, 4])
    for uid in (1, 2, 3, 4):
        assert paint_tile(room, uid, 7, 7, map_data, 10)
        paint_tile(room, uid, uid + 10, 10, map_data, 10)
    assert room.tile_owners[7][7] == 4
    assert room.tile_counts == {1: 1, 2: 1, 3: 1, 4: 2}
    assert all(room.players[uid].total_painted == 2 for uid in (1, 2, 3, 4))
    assert room.players[1].color is not room.players[4].color


def test_rooms_are_independent(map_data):
    a = make_room([1, 2])
    b = make_room([3, 4])
    paint_tile(a, 1, 0, 0, map_data, 10)
    assert b.tile_owners[0][0] is None
    assert b.tile_counts == {3: 0, 4: 0}
    with pytest.raises(ValueError):
        paint_tile(b, 1, 0, 0, map_data, 10)
    assert a.tile_owners is not b.tile_owners


def test_repeated_overwrites_consistent(map_data):
    room = make_room([1, 2, 3, 4])
    rng = random.Random(0)
    painted = {uid: 0 for uid in room.player_order}
    for _ in range(2000):
        uid = rng.choice(room.player_order)
        if paint_tile(room, uid, rng.randrange(6), rng.randrange(6), map_data, 10):
            painted[uid] += 1
    for uid in room.player_order:
        assert room.tile_counts[uid] == owned(room, uid)
        assert room.players[uid].total_painted == painted[uid]
        assert room.players[uid].total_painted >= room.tile_counts[uid]
    assert sum(room.tile_counts.values()) == 36 - 2
