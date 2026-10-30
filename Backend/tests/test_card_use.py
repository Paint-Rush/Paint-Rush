import json
import threading
from collections import Counter

import pytest

from app import cards, game_room, matchmaking, websocket
from app.cards import CARDS, DEFAULT_DECK, CardType, card_cost, initialize_deck
from app.game_engine import advance, start, use_card
from app.game_room import MAX_COST, RoomStatus, create_room, get_player_room
from tests.test_game_engine import client, now, receive_until, users
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, wait_until


class NoShuffle:
    def shuffle(self, items):
        pass


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def playing_room(t=10, user_ids=(1, 2)):
    room = create_room(list(user_ids))
    start(room, 0)
    for player in room.players.values():
        initialize_deck(player, DEFAULT_DECK, NoShuffle())
    advance(room, t)
    return room


def snapshot(player):
    return list(player.hand), list(player.draw_queue), player.cost, player.cards_used, player.cost_used


def test_card_data():
    assert len(DEFAULT_DECK) == 6
    for card_id, card in CARDS.items():
        assert isinstance(card_id, str)
        assert isinstance(card.type, CardType)
        assert 1 <= card.cost <= MAX_COST
        assert card_cost(card_id) == card.cost
    assert card_cost("ghost") is None


@pytest.mark.parametrize("cost", [0, 11, -1, 2.5])
def test_card_cost_out_of_range(monkeypatch, cost):
    monkeypatch.setitem(CARDS, "broken", cards.Card(CardType.MOVE, cost))
    assert card_cost("broken") is None


def test_match_initializes_decks():
    matchmaking.join_queue(1)
    matchmaking.join_queue(2)
    room = matchmaking.try_match()
    a, b = room.players[1], room.players[2]
    for player in (a, b):
        assert len(player.hand) == 3
        assert len(player.draw_queue) == 3
        assert Counter(player.hand + list(player.draw_queue)) == Counter(DEFAULT_DECK)
    assert a.hand is not b.hand
    assert a.draw_queue is not b.draw_queue


def test_deck_init_failure_leaves_no_room(monkeypatch):
    monkeypatch.setattr(cards, "DEFAULT_DECK", ["only_one"])
    matchmaking.join_queue(1)
    matchmaking.join_queue(2)
    with pytest.raises(ValueError):
        matchmaking.try_match()
    assert game_room.rooms == {}
    assert game_room.player_rooms == {}
    assert matchmaking.get_waiting_players() == [1, 2]


def test_use_card_success():
    room = playing_room()
    player = room.players[1]
    assert player.hand == ["move_dash", "move_jump", "attack_slash"]

    assert use_card(room, 1, 0, 10) == "move_dash"
    assert player.cost == 4
    assert player.hand == ["attack_bomb", "move_jump", "attack_slash"]
    assert list(player.draw_queue) == ["defense_shield", "defense_wall", "move_dash"]
    assert player.cards_used == 1
    assert player.cost_used == 2

    assert use_card(room, 1, 2, 10) == "attack_slash"
    assert player.cost == 1
    assert player.cards_used == 2
    assert player.cost_used == 5
    assert snapshot(room.players[2]) == (
        ["move_dash", "move_jump", "attack_slash"], ["attack_bomb", "defense_shield", "defense_wall"], 6, 0, 0,
    )


def test_cost_refreshed_before_use():
    room = playing_room()
    player = room.players[1]
    use_card(room, 1, 0, 10)
    with pytest.raises(ValueError, match="insufficient_cost"):
        use_card(room, 1, 0, 10.85)
    assert use_card(room, 1, 0, 10.86) == "attack_bomb"
    assert player.cost == 0


def test_insufficient_cost_keeps_state():
    room = playing_room()
    player = room.players[1]
    player.cost = 1
    before = snapshot(player)
    with pytest.raises(ValueError, match="insufficient_cost"):
        use_card(room, 1, 0, 10)
    assert snapshot(player) == before


@pytest.mark.parametrize("index", [-1, 3, "0", 1.0, None, True, [0]])
def test_invalid_hand_index(index):
    room = playing_room()
    before = snapshot(room.players[1])
    with pytest.raises(ValueError, match="invalid_hand_index"):
        use_card(room, 1, index, 10)
    assert snapshot(room.players[1]) == before


def test_unknown_card():
    room = playing_room()
    player = room.players[1]
    player.hand[1] = "ghost"
    before = snapshot(player)
    with pytest.raises(ValueError, match="unknown_card"):
        use_card(room, 1, 1, 10)
    assert snapshot(player) == before


def test_deck_not_initialized():
    room = create_room([1, 2])
    start(room, 0)
    advance(room, 10)
    with pytest.raises(ValueError, match="deck_not_initialized"):
        use_card(room, 1, 0, 10)
    assert room.players[1].cost == 6


@pytest.mark.parametrize("t, status", [(5, RoomStatus.READY), (310, RoomStatus.FINISHED)])
def test_rejected_when_not_playing(t, status):
    room = playing_room(t)
    assert room.status is status
    before = snapshot(room.players[1])
    with pytest.raises(ValueError, match="game_not_playing"):
        use_card(room, 1, 0, t)
    assert snapshot(room.players[1]) == before


def test_rejected_after_time_up_before_timer_tick():
    room = playing_room(309)
    assert room.status is RoomStatus.DEATHMATCH
    with pytest.raises(ValueError, match="game_not_playing"):
        use_card(room, 1, 0, 310)


def test_deathmatch_use():
    room = playing_room(255)
    assert room.status is RoomStatus.DEATHMATCH
    assert use_card(room, 1, 0, 255) == "move_dash"
    assert room.players[1].cost == MAX_COST - 2


def test_dead_player_rejected():
    room = playing_room()
    room.players[1].alive = False
    before = snapshot(room.players[1])
    with pytest.raises(ValueError, match="player_dead"):
        use_card(room, 1, 0, 10)
    assert snapshot(room.players[1]) == before


def test_concurrent_use_no_double_spend():
    room = playing_room()
    player = room.players[1]
    player.cost = 3
    results = []
    barrier = threading.Barrier(8)

    def run():
        barrier.wait()
        with websocket.state_lock:
            try:
                results.append(use_card(room, 1, 1, 10))
            except ValueError as e:
                results.append(str(e))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("move_jump") == 1
    assert results.count("insufficient_cost") == 7
    assert player.cost == 0
    assert player.cards_used == 1
    assert player.cost_used == 3
    assert Counter(player.hand + list(player.draw_queue)) == Counter(DEFAULT_DECK)


def match(client, users):
    ws1 = client.websocket_connect(WS_URL).__enter__()
    ws2 = client.websocket_connect(WS_URL).__enter__()
    auth(ws1, users[0])
    auth(ws2, users[1])
    join_and_match(ws1, ws2)
    return ws1, ws2, get_player_room(users[0])


def test_card_use_over_websocket(client, users, now):
    ws1, ws2, room = match(client, users)
    try:
        ws1.send_json({"type": "card_use", "hand_index": 0})
        assert receive_until(ws1, "error")["code"] == "game_not_playing"

        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        player = room.players[users[0]]
        with websocket.state_lock:
            card_id = player.hand[0]
            room.players[users[1]].hand[0] = "secret_card"

        ws1.send_json({"type": "card_use", "hand_index": 0})
        used = {"type": "card_used", "user_id": users[0], "card_id": card_id, "hand_index": 0}
        assert receive_until(ws1, "card_used") == used
        hand = ws1.receive_json()
        assert hand == {"type": "hand_updated", "hand": player.hand, "cost": player.cost}
        assert ws1.receive_json() == {"type": "cost_updated", "cost": player.cost, "max_cost": MAX_COST}
        assert player.cost == 6 - CARDS[card_id].cost
        assert player.cards_used == 1

        other = receive_until(ws2, "card_used")
        assert other == used
        ws2.send_json({"type": "room_state"})
        state = receive_until(ws2, "room_state")
        assert state["my_hand"] == room.players[users[1]].hand

        ws1.send_json({"type": "room_state"})
        text = json.dumps(receive_until(ws1, "room_state"))
        assert "secret_card" not in text
        assert "draw_queue" not in text
        assert "deck" not in text

        for message, code in [
            ({"type": "card_use", "hand_index": 5}, "invalid_hand_index"),
            ({"type": "card_use"}, "invalid_hand_index"),
            ({"type": "card_use", "hand_index": "1"}, "invalid_hand_index"),
        ]:
            before = (list(player.hand), list(player.draw_queue), player.cost)
            ws1.send_json(message)
            reply = receive_until(ws1, "error")
            assert reply["code"] == code
            assert set(reply) == {"type", "code", "message"}
            assert (list(player.hand), list(player.draw_queue), player.cost) == before

        with websocket.state_lock:
            player.cost = 0
        ws1.send_json({"type": "card_use", "hand_index": 0})
        assert receive_until(ws1, "error")["code"] == "insufficient_cost"
        assert room.players[users[1]].cost_used == 0
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)


def test_card_use_without_room(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        ws.send_json({"type": "card_use", "hand_index": 0})
        assert ws.receive_json()["code"] == "not_in_room"


def test_reconnect_keeps_hand_and_cost(client, users, now):
    ws1, ws2, room = match(client, users)
    try:
        player = room.players[users[0]]
        deck = list(player.deck)
        ws1.send_json({"type": "room_state"})
        state = receive_until(ws1, "room_state")
        assert state["my_hand"] == player.hand
        assert state["my_next_card"] == next(iter(player.draw_queue))

        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        ws1.send_json({"type": "card_use", "hand_index": 1})
        receive_until(ws1, "hand_updated")
        expected = (list(player.hand), list(player.draw_queue), player.cost)
        ws1.__exit__(None, None, None)
        assert wait_until(lambda: not room.players[users[0]].connected)

        ws1 = client.websocket_connect(WS_URL).__enter__()
        auth(ws1, users[0])
        state = receive_until(ws1, "room_state")
        assert state["my_hand"] == expected[0]
        assert state["my_next_card"] == expected[1][0]
        assert state["my_cost"] == expected[2]
        assert (list(player.hand), list(player.draw_queue), player.cost) == expected
        assert player.deck == deck
        assert len(websocket.timers) == 1
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)
