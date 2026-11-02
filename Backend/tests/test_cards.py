import random
from collections import Counter

import pytest

from app import game_room
from app.cards import initialize_deck, rotate_card
from app.game_room import create_room, get_player_room, set_player_connected

CARDS = ["a", "b", "c", "d", "e", "f"]


class NoShuffle:
    def shuffle(self, items):
        pass


@pytest.fixture(autouse=True)
def clear_rooms():
    game_room.rooms.clear()
    game_room.player_rooms.clear()
    yield
    game_room.rooms.clear()
    game_room.player_rooms.clear()


@pytest.fixture
def player():
    room = create_room([1, 2])
    return room.players[1]


def state(player):
    return list(player.deck), list(player.hand), list(player.draw_queue)


def test_initialize_deck(player):
    initialize_deck(player, CARDS, NoShuffle())
    assert player.deck == CARDS
    assert player.hand == ["a", "b", "c"]
    assert list(player.draw_queue) == ["d", "e", "f"]
    assert len(player.draw_queue) == 3


def test_initialize_does_not_alias_input(player):
    cards = list(CARDS)
    initialize_deck(player, cards, NoShuffle())
    cards[0] = "z"
    assert player.deck[0] == "a"
    assert player.hand is not player.deck


def test_initial_order_is_shuffled(player):
    initialize_deck(player, CARDS, random.Random(1))
    expected = list(CARDS)
    random.Random(1).shuffle(expected)
    assert player.deck == expected
    assert player.hand + list(player.draw_queue) == expected

    orders = set()
    for seed in range(20):
        p = create_room([10 + seed * 2, 11 + seed * 2]).players[10 + seed * 2]
        initialize_deck(p, CARDS, random.Random(seed))
        orders.add(tuple(p.deck))
    assert len(orders) > 1


def test_rotate_replaces_hand_and_moves_to_back(player):
    initialize_deck(player, CARDS, NoShuffle())
    assert rotate_card(player, 1) == "b"
    assert player.hand == ["a", "d", "c"]
    assert list(player.draw_queue) == ["e", "f", "b"]


@pytest.mark.parametrize("index, hand, queue", [
    (0, ["d", "b", "c"], ["e", "f", "a"]),
    (1, ["a", "d", "c"], ["e", "f", "b"]),
    (2, ["a", "b", "d"], ["e", "f", "c"]),
])
def test_rotate_each_position(player, index, hand, queue):
    initialize_deck(player, CARDS, NoShuffle())
    assert rotate_card(player, index) == CARDS[index]
    assert player.hand == hand
    assert list(player.draw_queue) == queue


def test_repeated_rotation_cycles_all_cards(player):
    initialize_deck(player, CARDS, NoShuffle())
    used = [rotate_card(player, 0) for _ in range(12)]
    assert used == ["a", "d", "e", "f", "a", "d", "e", "f", "a", "d", "e", "f"]

    rng = random.Random(7)
    for _ in range(100):
        rotate_card(player, rng.randrange(3))
        assert len(player.hand) == 3
        assert len(player.draw_queue) == 3
        assert Counter(player.hand + list(player.draw_queue)) == Counter(CARDS)
    assert player.deck == CARDS


@pytest.mark.parametrize("index", [-1, 3, 10, "0", 1.0, None, True])
def test_invalid_index_keeps_state(player, index):
    initialize_deck(player, CARDS, NoShuffle())
    rotate_card(player, 0)
    before = state(player)
    with pytest.raises(ValueError):
        rotate_card(player, index)
    assert state(player) == before


def test_rotate_uninitialized_deck(player):
    with pytest.raises(ValueError):
        rotate_card(player, 0)
    assert state(player) == ([], [], [])


@pytest.mark.parametrize("cards", [
    CARDS[:5],
    CARDS + ["g"],
    [],
    "abcdef",
    None,
    ["a", "b", "c", "d", "e", ""],
    ["a", "b", "c", "d", "e", "   "],
    ["a", "b", "c", "d", "e", 6],
    ["a", "b", "c", "d", "e", None],
])
def test_invalid_deck_rejected(player, cards):
    with pytest.raises(ValueError):
        initialize_deck(player, cards)
    assert state(player) == ([], [], [])


def test_reinitialize_rejected(player):
    initialize_deck(player, CARDS, NoShuffle())
    rotate_card(player, 2)
    before = state(player)
    with pytest.raises(ValueError):
        initialize_deck(player, list("uvwxyz"))
    assert state(player) == before


def test_duplicate_card_ids(player):
    cards = ["paint", "paint", "move", "move", "move", "bomb"]
    initialize_deck(player, cards, NoShuffle())
    assert rotate_card(player, 1) == "paint"
    assert player.hand == ["paint", "move", "move"]
    assert list(player.draw_queue) == ["move", "bomb", "paint"]
    for i in range(30):
        rotate_card(player, i % 3)
        assert Counter(player.hand + list(player.draw_queue)) == Counter(cards)


def test_players_are_independent():
    room = create_room([1, 2, 3, 4])
    players = [room.players[uid] for uid in room.player_order]
    assert len({id(p.hand) for p in players}) == 4
    assert len({id(p.draw_queue) for p in players}) == 4

    for i, p in enumerate(players):
        initialize_deck(p, [f"{i}{c}" for c in CARDS], NoShuffle())
    rotate_card(players[0], 0)

    assert players[0].hand == ["0d", "0b", "0c"]
    for i, p in enumerate(players[1:], 1):
        assert p.hand == [f"{i}a", f"{i}b", f"{i}c"]
        assert list(p.draw_queue) == [f"{i}d", f"{i}e", f"{i}f"]


def test_state_kept_after_reconnect(player):
    initialize_deck(player, CARDS, random.Random(3))
    rotate_card(player, 1)
    rotate_card(player, 0)
    before = state(player)

    set_player_connected(1, False)
    set_player_connected(1, True)

    assert get_player_room(1).players[1] is player
    assert state(player) == before
