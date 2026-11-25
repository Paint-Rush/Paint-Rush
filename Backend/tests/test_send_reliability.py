import asyncio
import copy

import pytest

from app import game_room, matchmaking, websocket
from app.cards import DEFAULT_DECK, card_cost
from app.game_room import get_player_room
from tests.test_card_use import playing_room
from tests.test_game_engine import now
from tests.test_websocket import clear_state

pytestmark = pytest.mark.usefixtures("default_map")

ALL = ("*",)
TARGET = {"type": "card_use", "hand_index": 0, "target_x": 6, "target_y": 5}
MOVE_EVENTS = ["card_used", "move_requested", "player_moved", "tiles_updated"]
ATTACK_EVENTS = ["card_used", "attack_requested", "attack_resolved", "tiles_updated", "player_damaged", "player_knocked_out"]


class Socket:
    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.messages = []

    async def send_json(self, message):
        if "*" in self.fail_on or message["type"] in self.fail_on:
            raise RuntimeError("connection lost")
        self.messages.append(message)

    def types(self):
        return [message["type"] for message in self.messages]


@pytest.fixture(autouse=True)
def clean():
    clear_state()
    yield
    clear_state()


def game(now, card_id, fail_on=()):
    now[0] = 10
    room = playing_room(10, (1, 2))
    other = playing_room(10, (3, 4))
    player = room.players[1]
    player.hand[0] = card_id
    player.cost = 10
    player.position = (5, 5)
    target = room.players[2]
    target.position = (6, 5) if card_id == "attack_slash" else (9, 9)
    target.hp = 1
    sockets = {1: Socket(fail_on), 2: Socket(), 3: Socket(), 4: Socket()}
    websocket.connections.update(sockets)
    return room, other, sockets


def replay(messages, owners):
    owners = copy.deepcopy(owners)
    for message in messages:
        if message["type"] == "tiles_updated":
            for tile in message["tiles"]:
                assert owners[tile["y"]][tile["x"]] == tile["previous_owner"]
                owners[tile["y"]][tile["x"]] = tile["new_owner"]
    return owners


def run_card(now, card_id, message, fail_on):
    room, other, sockets = game(now, card_id, fail_on)
    before = copy.deepcopy(room.tile_owners)
    other_before = copy.deepcopy(other.tile_owners)
    queue = list(room.players[1].draw_queue)
    asyncio.run(websocket.card_use(1, sockets[1], message))
    player = room.players[1]
    assert player.cards_used == 1
    assert player.cost_used == card_cost(card_id)
    assert player.cost == 10 - card_cost(card_id)
    assert player.hand[0] == queue[0]
    assert list(player.draw_queue) == queue[1:] + [card_id]
    assert other.tile_owners == other_before
    assert sockets[3].messages == [] and sockets[4].messages == []
    assert replay(sockets[2].messages, before) == room.tile_owners
    return room, sockets


@pytest.mark.parametrize("fail_on", [(), ("hand_updated",), ("cost_updated",), ALL])
def test_move_reaches_other_players_when_actor_send_fails(now, fail_on):
    room, sockets = run_card(now, "move_dash", TARGET, fail_on)
    received = sockets[2].messages
    assert sockets[2].types() == MOVE_EVENTS
    moved = received[2]
    assert (moved["x"], moved["y"]) == room.players[1].position == (6, 5)
    assert received[3]["tiles"] == [{"x": 6, "y": 5, "previous_owner": None, "new_owner": 1}]
    actor = sockets[1].types()
    expected = ["card_used", "hand_updated", "cost_updated", "move_requested", "player_moved", "tiles_updated"]
    if fail_on == ALL:
        assert actor == []
    else:
        assert actor == [t for t in expected if t not in fail_on]


@pytest.mark.parametrize("fail_on", [(), ("hand_updated",), ("cost_updated",), ALL])
def test_attack_reaches_other_players_when_actor_send_fails(now, fail_on):
    room, sockets = run_card(now, "attack_slash", TARGET, fail_on)
    received = sockets[2].messages
    assert sockets[2].types() == ATTACK_EVENTS
    assert received[2]["affected_tiles"] == [[6, 5]]
    assert received[4]["hp"] == room.players[2].hp == 0
    assert received[5]["user_id"] == 2 and room.players[2].knocked_out_at == 10
    assert room.players[1].kills == 1 and room.players[2].deaths == 1
    actor = sockets[1].types()
    expected = ["card_used", "hand_updated", "cost_updated", *ATTACK_EVENTS[1:]]
    if fail_on == ALL:
        assert actor == []
    else:
        assert actor == [t for t in expected if t not in fail_on]


def test_failed_card_use_still_replies_with_error(now):
    room, other, sockets = game(now, "move_dash")
    room.players[1].cost = 0
    asyncio.run(websocket.card_use(1, sockets[1], TARGET))
    assert sockets[1].types() == ["error"]
    assert sockets[2].messages == []
    assert room.players[1].cards_used == 0


def test_card_reply_goes_to_requesting_socket_not_replacement(now):
    room, other, sockets = game(now, "move_dash")
    old = sockets[1]
    new = Socket()
    websocket.connections[1] = new
    asyncio.run(websocket.card_use(1, old, TARGET))
    assert old.types() == ["hand_updated", "cost_updated"]
    assert new.types() == MOVE_EVENTS
    assert sockets[2].types() == MOVE_EVENTS


def join(user_id, socket):
    websocket.connections[user_id] = socket
    return websocket.queue_join(user_id, socket, {"type": "queue_join", "character_id": "character_01"})


def run_matching(now, monkeypatch, size, last_fail_on):
    monkeypatch.setattr(websocket.decks, "load_deck", lambda user_id: list(DEFAULT_DECK))
    sockets = {user_id: Socket() for user_id in range(1, size)}
    sockets[size] = Socket(last_fail_on)
    now[0] = 0

    async def main():
        for user_id in range(1, size + 1):
            await join(user_id, sockets[user_id])
        state = (dict(game_room.rooms), dict(websocket.timers), matchmaking.get_waiting_players())
        await websocket.stop_timers()
        return state

    rooms, timers, waiting = asyncio.run(main())
    assert len(rooms) == 1
    room = next(iter(rooms.values()))
    assert list(timers) == [room.room_id]
    assert waiting == []
    assert room.player_order == list(range(1, size + 1))
    assert all(get_player_room(user_id) is room for user_id in room.player_order)
    for user_id in range(1, size):
        types = sockets[user_id].types()
        assert types == ["queue_waiting", "match_found", "game_ready"]
        found = sockets[user_id].messages[1]
        assert found["room_id"] == str(room.room_id)
        assert [p["user_id"] for p in found["players"]] == room.player_order
    return room, sockets


@pytest.mark.usefixtures("immediate_matchmaking")
@pytest.mark.parametrize("fail_on", [ALL, ("queue_waiting",)])
def test_two_player_match_announced_when_last_reply_fails(now, monkeypatch, fail_on):
    room, sockets = run_matching(now, monkeypatch, 2, fail_on)
    assert sockets[2].types() == ([] if fail_on == ALL else ["match_found", "game_ready"])


@pytest.mark.parametrize("fail_on", [ALL, ("queue_waiting",)])
def test_four_player_match_announced_when_last_reply_fails(now, monkeypatch, fail_on):
    room, sockets = run_matching(now, monkeypatch, 4, fail_on)
    assert sockets[4].types() == ([] if fail_on == ALL else ["match_found", "game_ready"])
    assert len(room.players) == 4


@pytest.mark.parametrize("size", [2, 4])
def test_matching_messages_unchanged_without_failure(now, monkeypatch, size):
    if size == 2:
        monkeypatch.setattr(matchmaking, "MATCH_SIZE", 2)
    room, sockets = run_matching(now, monkeypatch, size, ())
    assert sockets[size].types() == ["queue_waiting", "match_found", "game_ready"]
    assert sockets[size].messages[0]["waiting"] == size


def test_queue_error_reply_unchanged(now, monkeypatch):
    monkeypatch.setattr(websocket.decks, "load_deck", lambda user_id: list(DEFAULT_DECK))
    socket = Socket()

    async def main():
        await join(1, socket)
        await join(1, socket)

    asyncio.run(main())
    assert socket.types() == ["queue_waiting", "error"]
    assert socket.messages[1]["code"] == "already_in_queue"
    assert matchmaking.get_waiting_players() == [1]
    assert game_room.rooms == {} and websocket.timers == {}
