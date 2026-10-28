import pytest

from app import game_room, matchmaking
from app.game_room import create_room, get_player_room
from app.matchmaking import cancel_queue, get_waiting_players, join_queue, try_match


@pytest.fixture(autouse=True)
def clear_state():
    game_room.rooms.clear()
    game_room.player_rooms.clear()
    matchmaking.waiting_queue.clear()
    yield
    game_room.rooms.clear()
    game_room.player_rooms.clear()
    matchmaking.waiting_queue.clear()


def test_single_user_keeps_waiting():
    join_queue(1)

    assert try_match() is None
    assert get_waiting_players() == [1]
    assert game_room.rooms == {}


def test_two_users_match():
    join_queue(1)
    join_queue(2)
    room = try_match()

    assert room.player_order == [1, 2]
    assert get_player_room(1) is room
    assert get_player_room(2) is room
    assert get_waiting_players() == []


def test_fifo_order():
    for user_id in [5, 3, 9]:
        join_queue(user_id)
    room = try_match()

    assert room.player_order == [5, 3]
    assert get_waiting_players() == [9]


def test_reject_duplicate_join():
    join_queue(1)

    with pytest.raises(ValueError):
        join_queue(1)
    assert get_waiting_players() == [1]


def test_cancel_and_rejoin():
    join_queue(1)
    join_queue(2)

    assert cancel_queue(1) is True
    assert cancel_queue(1) is False
    assert get_waiting_players() == [2]

    join_queue(1)
    assert get_waiting_players() == [2, 1]
    assert try_match().player_order == [2, 1]


def test_user_in_game_cannot_join():
    join_queue(1)
    join_queue(2)
    try_match()

    with pytest.raises(ValueError):
        join_queue(1)
    assert get_waiting_players() == []


@pytest.mark.parametrize("size", [3, 4])
def test_match_size_setting(monkeypatch, size):
    monkeypatch.setattr(matchmaking, "MATCH_SIZE", size)
    for user_id in range(1, size):
        join_queue(user_id)
    assert try_match() is None

    join_queue(size)
    join_queue(size + 1)
    room = try_match()

    assert room.player_order == list(range(1, size + 1))
    assert len(room.players) == size
    assert get_waiting_players() == [size + 1]


def test_multiple_rooms_in_a_row():
    for user_id in range(1, 7):
        join_queue(user_id)

    rooms = [try_match() for _ in range(3)]

    assert [r.player_order for r in rooms] == [[1, 2], [3, 4], [5, 6]]
    assert len({r.room_id for r in rooms}) == 3
    assert len(game_room.rooms) == 3
    assert try_match() is None


def test_queue_kept_when_room_creation_fails():
    join_queue(1)
    join_queue(2)
    create_room([2, 3])

    with pytest.raises(ValueError):
        try_match()
    assert get_waiting_players() == [1, 2]
    assert get_player_room(1) is None
