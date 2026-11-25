import asyncio
import copy

import pytest

from app import websocket
from app.game_room import RoomStatus, create_room, remove_room
from tests.test_card_use import playing_room
from tests.test_game_engine import async_wait, now
from tests.test_websocket import clear_state

pytestmark = pytest.mark.usefixtures("default_map")

TIMEOUT = 1
PLAYERS = (1, 2, 3, 4)
MOVE_EVENTS = ["card_used", "move_requested", "player_moved", "tiles_updated"]


class Socket:
    def __init__(self, hold=None):
        self.messages = []
        self.hold = hold
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.closed = None

    async def send_json(self, message):
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self.hold and self.hold(message) and not self.release.is_set():
                self.held.set()
                await self.release.wait()
            self.messages.append(message)
        finally:
            self.active -= 1

    async def close(self, code=1000, reason=""):
        self.closed = code

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


def everything(message):
    return True


def kind(event):
    return lambda message: message["type"] == event


def reset():
    clear_state()
    asyncio.run(websocket.stop_timers())
    websocket.flushing.clear()


@pytest.fixture(autouse=True)
def clean():
    reset()
    yield
    reset()


def setup_room(now, hold=None, held_by=3):
    now[0] = 10
    room = playing_room(10, PLAYERS)
    for player in room.players.values():
        player.cost = 10
    positions = {1: (5, 5), 2: (7, 5), 3: (0, 19), 4: (19, 19)}
    for uid, position in positions.items():
        room.players[uid].position = position
        room.players[uid].hand[0] = "move_dash"
    sockets = {uid: Socket(hold if uid == held_by else None) for uid in PLAYERS}
    websocket.connections.update(sockets)
    return room, sockets, copy.deepcopy(room.tile_owners)


def card(x, y):
    return {"type": "card_use", "hand_index": 0, "target_x": x, "target_y": y}


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_stuck_socket_does_not_block_other_players(now):
    room, sockets, start = setup_room(now, hold=everything)

    async def main():
        await asyncio.wait_for(websocket.card_use(1, sockets[1], card(6, 5)), TIMEOUT)
        await asyncio.wait_for(sockets[3].held.wait(), TIMEOUT)
        await asyncio.wait_for(websocket.card_use(2, sockets[2], card(8, 5)), TIMEOUT)
        delivered = {uid: sockets[uid].types() for uid in (1, 2, 4)}
        sockets[3].release.set()
        await settle()
        return delivered

    delivered = asyncio.run(main())
    assert [room.players[uid].cards_used for uid in (1, 2)] == [1, 1]
    assert delivered[4] == MOVE_EVENTS + MOVE_EVENTS
    assert [m["user_id"] for m in sockets[4].messages if m["type"] == "card_used"] == [1, 2]
    for uid in PLAYERS:
        assert sockets[uid].owners(start) == room.tile_owners, uid


def test_stuck_socket_does_not_delay_room_timer(now, monkeypatch):
    monkeypatch.setattr(websocket, "store_result", lambda room: None)
    monkeypatch.setattr(websocket, "load_settlements", lambda room: {})
    sockets = {1: Socket(), 2: Socket(everything)}
    websocket.connections.update(sockets)

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = 50
        assert await async_wait(lambda: room.status is RoomStatus.PLAYING)
        await asyncio.wait_for(sockets[2].held.wait(), TIMEOUT)
        now[0] = 310
        finished = await async_wait(lambda: not websocket.timers)
        delivered = sockets[1].types()
        sockets[2].release.set()
        assert await async_wait(lambda: "game_result" in sockets[2].types())
        return room, finished, delivered

    room, finished, delivered = asyncio.run(main())
    assert finished and room.status is RoomStatus.FINISHED
    assert delivered.index("game_started") < delivered.index("game_finished") < delivered.index("game_result")
    assert sockets[2].types() == sockets[1].types()


def test_send_timeout_drops_only_slow_connection(now, monkeypatch):
    monkeypatch.setattr(websocket, "SEND_TIMEOUT", 0.05)
    room, sockets, start = setup_room(now, hold=everything)

    async def main():
        await websocket.card_use(1, sockets[1], card(6, 5))
        assert await async_wait(lambda: 3 not in websocket.connections)
        await websocket.card_use(2, sockets[2], card(8, 5))
        await settle()
        state = (sockets[3] in websocket.stalled, websocket.pending.get(sockets[3]), sockets[3] in websocket.senders)
        websocket.release(sockets[3])
        return state

    stalled, pending, sending = asyncio.run(main())
    assert stalled and not pending and not sending
    assert sockets[3].closed == 1013 and sockets[3].messages == []
    assert room.players[3].connected is False
    assert [room.players[uid].cards_used for uid in (1, 2)] == [1, 1]
    assert room.tile_owners[5][6] == 1 and room.tile_owners[5][8] == 2
    for uid in (1, 2, 4):
        assert sockets[uid].types().count("player_disconnected") == 1
        assert sockets[uid].owners(start) == room.tile_owners
    assert sockets[3] not in websocket.stalled


def test_pending_limit_drops_connection_that_cannot_keep_up(now, monkeypatch):
    monkeypatch.setattr(websocket, "MAX_PENDING", 4)
    room, sockets, start = setup_room(now, hold=everything)

    async def main():
        await websocket.card_use(1, sockets[1], card(6, 5))
        await websocket.card_use(2, sockets[2], card(8, 5))
        size = len(websocket.pending.get(sockets[3], ()))
        stalled = sockets[3] in websocket.stalled
        sockets[3].release.set()
        assert await async_wait(lambda: 3 not in websocket.connections)
        return size, stalled

    size, stalled = asyncio.run(main())
    assert stalled and size == 0
    assert sockets[3].types() == ["card_used"] and sockets[3].closed == 1013
    assert room.players[3].connected is False
    assert [room.players[uid].cards_used for uid in (1, 2)] == [1, 1]
    assert sockets[4].types().count("card_used") == 2


def test_same_socket_never_sends_concurrently(now):
    room, sockets, start = setup_room(now, hold=kind("card_used"), held_by=1)

    async def main():
        task = asyncio.create_task(websocket.card_use(1, sockets[1], card(6, 5)))
        await asyncio.wait_for(sockets[1].held.wait(), TIMEOUT)
        await asyncio.wait_for(websocket.card_use(1, sockets[1], {"type": "card_use", "hand_index": 9}), TIMEOUT)
        await asyncio.wait_for(websocket.room_state_request(1, sockets[1]), TIMEOUT)
        await asyncio.wait_for(websocket.card_use(2, sockets[2], card(8, 5)), TIMEOUT)
        sockets[1].release.set()
        await asyncio.wait_for(task, TIMEOUT)
        await settle()

    asyncio.run(main())
    assert sockets[1].peak == 1
    assert sockets[1].types() == [
        "card_used", "hand_updated", "cost_updated", "move_requested", "player_moved", "tiles_updated",
        "error", "room_state", *MOVE_EVENTS,
    ]
    assert sockets[1].owners(start) == room.tile_owners


def test_replaced_connection_does_not_wait_for_or_inherit_old_queue(now):
    room, sockets, start = setup_room(now, hold=everything)
    old = sockets[3]
    new = Socket()

    async def main():
        await websocket.card_use(1, sockets[1], card(6, 5))
        await asyncio.wait_for(old.held.wait(), TIMEOUT)
        await asyncio.wait_for(websocket.connect(3, new), TIMEOUT)
        restored = new.types()
        await websocket.card_use(2, sockets[2], card(8, 5))
        old.release.set()
        await settle()
        return restored

    restored = asyncio.run(main())
    assert restored == ["auth_success", "room_state"]
    assert new.types() == ["auth_success", "room_state", *MOVE_EVENTS]
    assert new.owners(start) == room.tile_owners
    assert old.types() == ["card_used", "session_replaced"] and old.closed == websocket.SESSION_REPLACED
    assert websocket.connections[3] is new and room.players[3].connected is True
    assert "player_reconnected" not in sockets[1].types()


def test_aborted_match_messages_never_follow_new_match(now):
    now[0] = 10
    room = playing_room(10, (1, 2))
    room.players[1].hand[0] = "move_dash"
    room.players[1].position = (5, 5)
    sockets = {1: Socket(), 2: Socket(kind("card_used")), 3: Socket()}
    websocket.connections.update(sockets)

    async def main():
        await websocket.card_use(1, sockets[1], card(6, 5))
        await asyncio.wait_for(sockets[2].held.wait(), TIMEOUT)
        assert await websocket.abort_game(room)
        with websocket.state_lock:
            nxt = create_room([2, 3])
            websocket.enqueue(nxt, websocket.match_found(nxt))
        await websocket.flush_room(nxt)
        sockets[2].release.set()
        await settle()
        return nxt

    nxt = asyncio.run(main())
    types = sockets[2].types()
    assert types == [*MOVE_EVENTS, "game_aborted", "match_found"]
    assert sockets[2].messages[-1]["room_id"] == str(nxt.room_id)
    assert sockets[2].peak == 1
    assert sockets[1].types().count("game_aborted") == 1
    assert sockets[3].types() == ["match_found"]
    assert room.room_id not in websocket.outboxes


def test_old_room_events_skip_players_in_new_room_but_keep_final_messages(now):
    now[0] = 10
    old = create_room([1, 2])
    sockets = {1: Socket(), 2: Socket(), 3: Socket()}
    websocket.connections.update(sockets)
    room_id = str(old.room_id)
    stale = [
        {"type": "tiles_updated", "room_id": room_id, "tiles": []},
        {"type": "player_moved", "user_id": 2, "x": 1, "y": 1, "path": [[1, 1]]},
        {"type": "game_result", "match_id": room_id, "winner_id": 1, "results": []},
    ]
    settlement = {"type": "rank_settlement", "room_id": room_id}

    async def main():
        with websocket.state_lock:
            remove_room(old.room_id)
            create_room([1, 3])
            for message in stale:
                websocket.enqueue(old, message)
            websocket.enqueue(old, settlement, 1)
        await websocket.flush_room(old)

    asyncio.run(main())
    assert sockets[1].types() == ["game_result", "rank_settlement"]
    assert sockets[2].types() == ["tiles_updated", "player_moved", "game_result"]
    assert sockets[3].messages == []
    assert old.room_id not in websocket.outboxes


def test_finished_result_and_settlement_delivered_once_with_slow_socket(now, monkeypatch):
    saves = []
    monkeypatch.setattr(websocket, "store_result", saves.append)

    def settlements(room):
        return {uid: [
            {"type": "rank_settlement", "room_id": str(room.room_id), "user_id": uid},
            {"type": "reward_settlement", "room_id": str(room.room_id), "user_id": uid},
        ] for uid in (1, 2)}

    monkeypatch.setattr(websocket, "load_settlements", settlements)
    sockets = {1: Socket(), 2: Socket(kind("game_finished"))}
    websocket.connections.update(sockets)

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = 50
        assert await async_wait(lambda: room.status is RoomStatus.PLAYING)
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        assert room.result_saved
        assert await websocket.save_result(room)
        sockets[2].release.set()
        assert await async_wait(lambda: "reward_settlement" in sockets[2].types())
        for uid in (1, 2):
            await websocket.room_leave(uid, sockets[uid])
        await settle()
        return room

    room = asyncio.run(main())
    for socket in sockets.values():
        types = socket.types()
        for event in ("game_finished", "game_result", "rank_settlement", "reward_settlement", "room_left"):
            assert types.count(event) == 1, event
        assert types.index("game_finished") < types.index("game_result") < types.index("rank_settlement")
        assert types.index("rank_settlement") < types.index("reward_settlement") < types.index("room_left")
        assert types[-1] == "room_left"
    assert websocket.get_room(room.room_id) is None
    assert room.room_id not in websocket.outboxes
    assert websocket.pending == {} and websocket.senders == {}


def test_stop_timers_cancels_stuck_senders(now):
    room, sockets, start = setup_room(now, hold=everything)

    async def main():
        await websocket.card_use(1, sockets[1], card(6, 5))
        task = websocket.senders[sockets[3]]
        await websocket.stop_timers()
        return task

    task = asyncio.run(main())
    assert task.cancelled()
    assert websocket.senders == {} and websocket.pending == {} and websocket.stalled == set()
    assert sockets[3].messages == []


def test_release_cancels_sender_and_discards_queue(now):
    room, sockets, start = setup_room(now, hold=everything)

    async def main():
        await websocket.card_use(1, sockets[1], card(6, 5))
        task = websocket.senders[sockets[3]]
        websocket.release(sockets[3])
        await settle()
        return task

    task = asyncio.run(main())
    assert task.cancelled()
    assert sockets[3] not in websocket.senders and sockets[3] not in websocket.pending
