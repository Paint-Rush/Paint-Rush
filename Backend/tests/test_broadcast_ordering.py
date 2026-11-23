import asyncio
import copy

import pytest

from app import websocket
from app.game_room import RoomStatus, create_room
from tests.test_card_use import playing_room
from tests.test_game_engine import async_wait, now
from tests.test_websocket import clear_state

pytestmark = pytest.mark.usefixtures("default_map")

TIMEOUT = 2
PLAYERS = (1, 2, 3, 4)


class Socket:
    def __init__(self, hold=None):
        self.messages = []
        self.hold = hold
        self.held = asyncio.Event()
        self.release = asyncio.Event()

    async def send_json(self, message):
        if self.hold and self.hold(message) and not self.release.is_set():
            self.held.set()
            await self.release.wait()
        self.messages.append(message)

    def types(self):
        return [message["type"] for message in self.messages]

    def owners(self, start):
        owners = copy.deepcopy(start)
        for message in self.messages:
            if message["type"] == "room_state":
                owners = copy.deepcopy(message["tile_owners"])
            elif message["type"] == "tiles_updated":
                for tile in message["tiles"]:
                    owners[tile["y"]][tile["x"]] = tile["new_owner"]
        return owners


class BrokenSocket(Socket):
    async def send_json(self, message):
        raise RuntimeError("connection lost")


@pytest.fixture(autouse=True)
def clean():
    clear_state()
    websocket.outboxes.clear()
    websocket.flushing.clear()
    yield
    clear_state()
    websocket.outboxes.clear()
    websocket.flushing.clear()


def first_from(user_id, event):
    return lambda message: message["type"] == event and message.get("user_id") == user_id


def setup_room(now, ids=PLAYERS, hold=None, held_by=3):
    now[0] = 10
    room = playing_room(10, ids)
    for player in room.players.values():
        player.cost = 10
    positions = {ids[0]: (5, 5), ids[1]: (7, 5), ids[2]: (0, 19), ids[3]: (19, 19)}
    for uid, position in positions.items():
        room.players[uid].position = position
    sockets = {uid: Socket(hold if uid == held_by else None) for uid in ids}
    websocket.connections.update(sockets)
    return room, sockets, copy.deepcopy(room.tile_owners)


def give(room, user_id, card_id):
    room.players[user_id].hand[0] = card_id


def card(x, y):
    return {"type": "card_use", "hand_index": 0, "target_x": x, "target_y": y}


async def interleave(blocked_socket, first, *rest):
    task = asyncio.create_task(first)
    await asyncio.wait_for(blocked_socket.held.wait(), TIMEOUT)
    for action in rest:
        await asyncio.wait_for(action, TIMEOUT)
    blocked_socket.release.set()
    await asyncio.wait_for(task, TIMEOUT)
    for _ in range(3):
        await asyncio.sleep(0)


def assert_clients_match_server(room, sockets, start):
    for uid, socket in sockets.items():
        assert socket.owners(start) == room.tile_owners, uid


def test_same_tile_overwrite_order_matches_server(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    give(room, 1, "move_dash")
    give(room, 2, "attack_slash")

    async def main():
        await interleave(
            sockets[3],
            websocket.card_use(1, sockets[1], card(6, 5)),
            websocket.card_use(2, sockets[2], card(6, 5)),
        )

    asyncio.run(main())
    assert room.tile_owners[5][6] == 2
    assert_clients_match_server(room, sockets, start)
    used = [m["user_id"] for m in sockets[3].messages if m["type"] == "card_used"]
    assert used == [1, 2]


def test_different_tiles_all_applied(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    give(room, 1, "move_dash")
    give(room, 2, "move_dash")

    async def main():
        await interleave(
            sockets[3],
            websocket.card_use(1, sockets[1], card(6, 5)),
            websocket.card_use(2, sockets[2], card(8, 5)),
        )

    asyncio.run(main())
    assert room.tile_owners[5][6] == 1 and room.tile_owners[5][8] == 2
    assert_clients_match_server(room, sockets, start)


def test_room_state_snapshot_not_overwritten_by_older_event(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    give(room, 1, "move_dash")
    give(room, 2, "attack_slash")

    async def main():
        await interleave(
            sockets[3],
            websocket.card_use(1, sockets[1], card(6, 5)),
            websocket.card_use(2, sockets[2], card(6, 5)),
            websocket.room_state_request(3, sockets[3]),
        )

    asyncio.run(main())
    assert room.tile_owners[5][6] == 2
    types = sockets[3].types()
    assert types[-1] == "room_state"
    assert sockets[3].messages[-1]["tile_owners"] == room.tile_owners
    assert_clients_match_server(room, sockets, start)


def test_ultimate_then_attack_order(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "ultimate_activated"))
    room.players[1].character_id = "character_01"
    give(room, 2, "attack_slash")
    room.players[2].position = (7, 5)

    async def main():
        await interleave(
            sockets[3],
            websocket.ultimate_use(1, sockets[1], {"type": "ultimate_use"}),
            websocket.card_use(2, sockets[2], card(6, 5)),
        )

    asyncio.run(main())
    assert room.tile_owners[5][6] == 2 and room.tile_owners[5][5] == 1
    assert_clients_match_server(room, sockets, start)
    types = sockets[3].types()
    assert types.index("ultimate_activated") < types.index("card_used")


def test_move_and_attack_event_order_per_action(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    give(room, 1, "move_dash")
    give(room, 2, "attack_slash")

    async def main():
        await interleave(
            sockets[3],
            websocket.card_use(1, sockets[1], card(6, 5)),
            websocket.card_use(2, sockets[2], card(6, 5)),
        )

    asyncio.run(main())
    assert sockets[3].types() == [
        "card_used", "move_requested", "player_moved", "tiles_updated",
        "card_used", "attack_requested", "attack_resolved", "tiles_updated", "player_damaged",
    ]
    assert sockets[1].types() == [
        "card_used", "hand_updated", "cost_updated", "move_requested", "player_moved", "tiles_updated",
        "card_used", "attack_requested", "attack_resolved", "tiles_updated", "player_damaged",
    ]
    assert room.players[1].hp == 2


def test_four_players_act_while_flush_is_held(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"), held_by=4)
    give(room, 1, "move_dash")
    give(room, 2, "attack_slash")
    give(room, 3, "move_dash")
    room.players[3].position = (6, 4)
    give(room, 4, "move_dash")
    room.players[4].position = (6, 7)

    async def main():
        await interleave(
            sockets[4],
            websocket.card_use(1, sockets[1], card(6, 5)),
            websocket.card_use(2, sockets[2], card(6, 5)),
            websocket.card_use(3, sockets[3], card(6, 3)),
            websocket.card_use(4, sockets[4], card(6, 6)),
        )

    asyncio.run(main())
    assert [room.players[uid].cards_used for uid in PLAYERS] == [1, 1, 1, 1]
    assert_clients_match_server(room, sockets, start)
    for socket in sockets.values():
        assert [m["user_id"] for m in socket.messages if m["type"] == "card_used"] == [1, 2, 3, 4]
    assert not websocket.flushing


def test_other_room_not_blocked_by_held_room(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    other = playing_room(10, (5, 6))
    other.players[5].position = (5, 5)
    other.players[5].hand[0] = "move_dash"
    other_sockets = {5: Socket(), 6: Socket()}
    websocket.connections.update(other_sockets)
    give(room, 1, "move_dash")

    async def main():
        task = asyncio.create_task(websocket.card_use(1, sockets[1], card(6, 5)))
        await asyncio.wait_for(sockets[3].held.wait(), TIMEOUT)
        await asyncio.wait_for(websocket.card_use(5, other_sockets[5], card(6, 5)), TIMEOUT)
        delivered = other_sockets[6].types()
        sockets[3].release.set()
        await asyncio.wait_for(task, TIMEOUT)
        return delivered

    delivered = asyncio.run(main())
    assert delivered == ["card_used", "move_requested", "player_moved", "tiles_updated"]
    assert all(str(room.room_id) != m.get("room_id") for s in other_sockets.values() for m in s.messages)
    assert all(str(other.room_id) != m.get("room_id") for s in sockets.values() for m in s.messages)


def test_broken_socket_does_not_break_ordering(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    websocket.connections[4] = BrokenSocket()
    del sockets[4]
    give(room, 1, "move_dash")
    give(room, 2, "attack_slash")

    async def main():
        await interleave(
            sockets[3],
            websocket.card_use(1, sockets[1], card(6, 5)),
            websocket.card_use(2, sockets[2], card(6, 5)),
        )

    asyncio.run(main())
    assert [room.players[uid].cards_used for uid in (1, 2)] == [1, 1]
    assert_clients_match_server(room, sockets, start)
    assert room.room_id not in websocket.flushing


def test_reconnect_during_held_flush_gets_consistent_state(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    give(room, 1, "move_dash")
    give(room, 2, "attack_slash")
    with websocket.state_lock:
        websocket.set_player_connected(4, False)
    del websocket.connections[4]
    fresh = Socket()

    async def main():
        await interleave(
            sockets[3],
            websocket.card_use(1, sockets[1], card(6, 5)),
            websocket.connect(4, fresh),
            websocket.card_use(2, sockets[2], card(6, 5)),
        )

    asyncio.run(main())
    assert fresh.types()[0] == "auth_success" and fresh.types().count("room_state") == 1
    snapshot = fresh.messages[fresh.types().index("room_state")]
    assert snapshot["tile_owners"][5][6] in (1, 2)
    assert fresh.owners(start) == room.tile_owners
    assert sockets[3].types().count("player_reconnected") == 1
    assert_clients_match_server(room, {k: v for k, v in sockets.items() if k != 4}, start)


def test_replaced_connection_keeps_order_and_private_replies(now):
    room, sockets, start = setup_room(now, hold=first_from(1, "card_used"))
    give(room, 1, "move_dash")
    give(room, 2, "attack_slash")
    old = sockets[1]
    new = Socket()
    websocket.connections[1] = new

    async def main():
        await interleave(
            sockets[3],
            websocket.card_use(1, old, card(6, 5)),
            websocket.card_use(2, sockets[2], card(6, 5)),
        )

    asyncio.run(main())
    assert old.types() == ["hand_updated", "cost_updated"]
    assert new.owners(start) == room.tile_owners
    assert "hand_updated" not in new.types()
    assert_clients_match_server(room, {2: sockets[2], 3: sockets[3], 4: sockets[4]}, start)


def test_timer_events_follow_state_order_and_outbox_cleared(now):
    sockets = {uid: Socket() for uid in (1, 2)}
    websocket.connections.update(sockets)

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = 50
        assert await async_wait(lambda: room.status is RoomStatus.PLAYING)
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return room

    room = asyncio.run(main())
    types = sockets[1].types()
    assert types.index("game_started") < types.index("deathmatch_started") < types.index("game_finished") < types.index("game_result")
    assert types == sockets[2].types()
    assert room.room_id not in websocket.flushing
    assert list(websocket.outboxes.get(room.room_id, [])) == []


def test_aborted_room_outbox_removed(now, monkeypatch):
    sockets = {uid: Socket() for uid in (1, 2)}
    websocket.connections.update(sockets)

    def broken(room):
        raise ValueError("boom")

    monkeypatch.setattr(websocket, "calculate_results", broken)

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return room

    room = asyncio.run(main())
    assert room.status is RoomStatus.ABORTED
    assert sockets[1].types()[-1] == "game_aborted"
    assert room.room_id not in websocket.outboxes and room.room_id not in websocket.flushing


def test_finished_room_outbox_removed_after_all_leave(now, monkeypatch):
    sockets = {uid: Socket() for uid in (1, 2)}
    websocket.connections.update(sockets)
    monkeypatch.setattr(websocket, "store_result", lambda room: None)
    monkeypatch.setattr(websocket, "load_settlements", lambda room: {})

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        assert room.room_id in websocket.outboxes
        for uid in (1, 2):
            await websocket.room_leave(uid, sockets[uid])
        return room

    room = asyncio.run(main())
    assert all(socket.types()[-1] == "room_left" for socket in sockets.values())
    assert websocket.get_room(room.room_id) is None
    assert room.room_id not in websocket.outboxes and room.room_id not in websocket.flushing


def test_stop_timers_clears_outboxes(now):
    room = playing_room(10, (1, 2))
    with websocket.state_lock:
        websocket.enqueue(room, {"type": "game_tick"})
    asyncio.run(websocket.stop_timers())
    assert websocket.outboxes == {}
