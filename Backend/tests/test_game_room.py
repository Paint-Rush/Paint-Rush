import pytest

from app import game_room
from app.game_room import (
    MAP_SIZE,
    SPAWN_POINTS,
    Color,
    RoomStatus,
    create_room,
    get_player_room,
    get_room,
    remove_room,
    set_player_connected,
)


@pytest.fixture(autouse=True)
def clear_rooms():
    game_room.rooms.clear()
    game_room.player_rooms.clear()
    yield
    game_room.rooms.clear()
    game_room.player_rooms.clear()


def test_create_room_with_two_players():
    room = create_room([1, 2])

    assert get_room(room.room_id) is room
    assert room.player_order == [1, 2]
    assert set(room.players) == {1, 2}


def test_create_room_with_four_players():
    room = create_room([4, 3, 2, 1])

    assert room.player_order == [4, 3, 2, 1]
    assert [room.players[u].color for u in room.player_order] == list(Color)


def test_reject_more_than_four_players():
    with pytest.raises(ValueError):
        create_room([1, 2, 3, 4, 5])
    with pytest.raises(ValueError):
        create_room([1])
    assert game_room.rooms == {}


def test_reject_duplicate_user():
    with pytest.raises(ValueError):
        create_room([1, 1])

    create_room([1, 2])
    with pytest.raises(ValueError):
        create_room([2, 3])
    assert get_player_room(3) is None


def test_spawn_points_are_unique_corners():
    for _ in range(50):
        game_room.rooms.clear()
        game_room.player_rooms.clear()
        room = create_room([1, 2, 3, 4])
        spawns = [p.spawn for p in room.players.values()]

        assert len(set(spawns)) == 4
        assert set(spawns) <= set(SPAWN_POINTS)


def test_initial_room_state():
    room = create_room([10, 20], map_id="map_a", character_ids={10: "knight"})

    assert room.status == RoomStatus.READY
    assert room.map_id == "map_a"
    assert room.started_at is None
    assert len(room.tile_owners) == MAP_SIZE
    assert all(len(row) == MAP_SIZE for row in room.tile_owners)
    assert all(tile is None for row in room.tile_owners for tile in row)
    assert room.tile_counts == {10: 0, 20: 0}

    assert room.players[10].character_id == "knight"
    assert len({p.color for p in room.players.values()}) == 2
    for player in room.players.values():
        assert player.position == player.spawn
        assert player.connected is True
        assert player.alive is True
        assert player.deck == []
        assert player.cost == 6
        assert player.cost_used == 0
        assert player.cards_used == 0
        assert player.kills == 0
        assert player.deaths == 0
        assert player.total_painted == 0


def test_get_and_remove_room():
    room = create_room([1, 2])

    assert get_player_room(1) is room
    assert remove_room(room.room_id) is room
    assert get_room(room.room_id) is None
    assert get_player_room(1) is None
    assert remove_room(room.room_id) is None

    assert create_room([1, 2]).room_id != room.room_id


def test_disconnect_keeps_player_in_room():
    room = create_room([1, 2])

    set_player_connected(1, False)
    assert 1 in room.players
    assert room.players[1].connected is False
    assert get_player_room(1) is room

    set_player_connected(1, True)
    assert room.players[1].connected is True
    assert set_player_connected(99, False) is None


def test_rooms_are_independent():
    room_a = create_room([1, 2])
    room_b = create_room([3, 4])

    room_a.tile_owners[0][0] = 1
    room_a.tile_counts[1] = 1
    room_a.players[1].cost = 9
    room_a.status = RoomStatus.PLAYING

    assert room_a.room_id != room_b.room_id
    assert room_b.tile_owners[0][0] is None
    assert room_a.tile_owners[1] is not room_a.tile_owners[0]
    assert room_b.tile_counts == {3: 0, 4: 0}
    assert room_b.players[3].cost == 6
    assert room_b.status == RoomStatus.READY
    assert get_player_room(3) is room_b
