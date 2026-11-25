import asyncio
import logging

import pytest
from sqlalchemy import func, select

from app import game_engine, websocket
from app.cards import DEFAULT_DECK
from app.database import SessionLocal
from app.game_room import RoomStatus, create_room
from app.models import Match, MatchPlayerResult, MatchReward, RankHistory, User, UserLevel, UserWallet
from tests.test_action_events import read_log
from tests.test_game_engine import async_wait, client, now
from tests.test_websocket import clear_state

PHASE_TIMES = {RoomStatus.READY: 5, RoomStatus.PLAYING: 50, RoomStatus.DEATHMATCH: 260}
ONCE = ("game_started", "deathmatch_started", "game_finished", "game_result")


def aborted(room):
    return {"type": "game_aborted", "room_id": str(room.room_id), "reason": "server_error"}


class Socket:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)

    def count(self, event):
        return sum(message["type"] == event for message in self.messages)

    def types(self):
        return [message["type"] for message in self.messages]


class Flaky:
    def __init__(self, original, rooms=None):
        self.original = original
        self.rooms = rooms
        self.pending = 0
        self.always = False
        self.alternate = False
        self.failed = 0
        self.calls = 0
        self.after_failure = 0

    def __call__(self, room, *args):
        if self.rooms is None or room.room_id in self.rooms:
            self.calls += 1
            if self.always or self.pending or (self.alternate and self.calls % 2):
                self.pending = max(0, self.pending - 1)
                self.failed += 1
                raise RuntimeError("boom")
            if self.failed:
                self.after_failure += 1
        return self.original(room, *args)


@pytest.fixture(autouse=True)
def clean():
    clear_state()
    yield
    clear_state()


def connect(user_ids):
    sockets = {user_id: Socket() for user_id in user_ids}
    websocket.connections.update(sockets)
    return sockets


def timer_logs(caplog, room):
    return [r for r in caplog.records if r.name == websocket.logger.name and str(room.room_id) in r.getMessage()]


def assert_finished_once(room, sockets):
    assert room.status is RoomStatus.FINISHED and room.result is not None
    for socket in sockets.values():
        for event in ONCE:
            assert socket.count(event) == 1, event


def test_normal_timer_runs_to_finish(now):
    sockets = connect([1, 2])

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        for t in (10, 250, 310):
            now[0] = t
            await asyncio.sleep(websocket.TIMER_STEP * 3)
        assert await async_wait(lambda: not websocket.timers)
        return room

    assert_finished_once(asyncio.run(main()), sockets)


@pytest.mark.parametrize("phase", [RoomStatus.READY, RoomStatus.PLAYING, RoomStatus.DEATHMATCH])
def test_transient_advance_failure_recovers(now, monkeypatch, caplog, phase):
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    flaky = Flaky(game_engine.advance)
    monkeypatch.setattr(game_engine, "advance", flaky)
    sockets = connect([1, 2])

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = PHASE_TIMES[phase]
        assert await async_wait(lambda: room.status is phase)
        flaky.pending = 2
        assert await async_wait(lambda: flaky.after_failure > 0)
        assert room.status is phase
        assert list(websocket.timers) == [room.room_id]
        assert websocket.start_timer(room) is None and len(websocket.timers) == 1
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return room

    room = asyncio.run(main())
    assert flaky.failed == 2
    assert_finished_once(room, sockets)
    assert [r.getMessage() for r in timer_logs(caplog, room)] == [f"game timer failed: {room.room_id}"]


def test_failure_at_finish_recovers_result(now, monkeypatch):
    advance = Flaky(game_engine.advance)
    results = Flaky(websocket.calculate_results)
    monkeypatch.setattr(game_engine, "advance", advance)
    monkeypatch.setattr(websocket, "calculate_results", results)
    saved = []

    async def fake_save(room):
        saved.append(room.result)
        return True

    monkeypatch.setattr(websocket, "save_result", fake_save)
    sockets = connect([1, 2])

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = 299
        assert await async_wait(lambda: room.status is RoomStatus.DEATHMATCH)
        advance.pending = 1
        results.pending = 2
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return room

    room = asyncio.run(main())
    assert advance.failed == 1 and results.failed == 2
    assert results.calls - results.failed == 1
    assert saved == [room.result]
    assert_finished_once(room, sockets)


def test_other_room_unaffected_by_failing_room(now, monkeypatch):
    flaky = Flaky(game_engine.advance)
    monkeypatch.setattr(game_engine, "advance", flaky)
    sockets = connect([1, 2, 3, 4])

    async def main():
        a, b = create_room([1, 2]), create_room([3, 4])
        flaky.rooms = {a.room_id}
        websocket.start_timer(a)
        websocket.start_timer(b)
        now[0] = 50
        assert await async_wait(lambda: a.status is RoomStatus.PLAYING and b.status is RoomStatus.PLAYING)
        flaky.pending = 3
        assert await async_wait(lambda: flaky.after_failure > 0)
        b_during = (b.status, b.last_tick, b.ready_at)
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return a, b, b_during

    a, b, b_during = asyncio.run(main())
    assert b_during == (RoomStatus.PLAYING, 260, 0)
    assert_finished_once(a, {k: sockets[k] for k in (1, 2)})
    assert_finished_once(b, {k: sockets[k] for k in (3, 4)})
    a_rooms = {m.get("room_id") for k in (1, 2) for m in sockets[k].messages} - {None}
    b_rooms = {m.get("room_id") for k in (3, 4) for m in sockets[k].messages} - {None}
    assert a_rooms <= {str(a.room_id)} and b_rooms <= {str(b.room_id)}


def test_repeated_failure_stops_timer_with_bounded_retries_and_logs(now, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    flaky = Flaky(game_engine.advance)
    monkeypatch.setattr(game_engine, "advance", flaky)
    sockets = connect([1, 2, 3, 4])

    async def main():
        a, b = create_room([1, 2]), create_room([3, 4])
        flaky.rooms = {a.room_id}
        flaky.always = True
        loop = asyncio.get_running_loop()
        started = loop.time()
        websocket.start_timer(a)
        websocket.start_timer(b)
        assert await async_wait(lambda: a.room_id not in websocket.timers)
        elapsed = loop.time() - started
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return a, b, elapsed

    a, b, elapsed = asyncio.run(main())
    assert flaky.calls == websocket.TIMER_MAX_FAILURES
    expected = websocket.TIMER_STEP * sum(2**n for n in range(websocket.TIMER_MAX_FAILURES))
    assert elapsed >= expected * 0.9
    assert a.status is RoomStatus.ABORTED and a.result is None
    assert all(socket.messages == [aborted(a)] for socket in (sockets[1], sockets[2]))
    assert [r.getMessage() for r in timer_logs(caplog, a)] == [
        f"game timer failed: {a.room_id}",
        f"game timer stopped after {websocket.TIMER_MAX_FAILURES} failures: {a.room_id}",
    ]
    assert_finished_once(b, {k: sockets[k] for k in (3, 4)})


def test_alternating_failures_keep_running_with_capped_logs(now, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    flaky = Flaky(game_engine.advance)
    monkeypatch.setattr(game_engine, "advance", flaky)
    sockets = connect([1, 2])

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        now[0] = 50
        assert await async_wait(lambda: room.status is RoomStatus.PLAYING)
        flaky.calls = 0
        flaky.alternate = True
        assert await async_wait(lambda: flaky.failed >= websocket.TIMER_MAX_FAILURES * 2)
        assert list(websocket.timers) == [room.room_id]
        flaky.alternate = False
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        return room

    room = asyncio.run(main())
    assert len(timer_logs(caplog, room)) == websocket.TIMER_MAX_FAILURES
    assert_finished_once(room, sockets)


def test_stop_timers_cancels_during_retry(now, monkeypatch):
    flaky = Flaky(game_engine.advance)
    flaky.always = True
    monkeypatch.setattr(game_engine, "advance", flaky)

    async def main():
        room = create_room([1, 2])
        websocket.start_timer(room)
        assert await async_wait(lambda: flaky.failed >= 2)
        task = websocket.timers[room.room_id]
        await websocket.stop_timers()
        return task

    task = asyncio.run(main())
    assert task.cancelled()
    assert websocket.timers == {}


def test_recovered_match_saved_and_rewarded_once(client, now, monkeypatch):
    with SessionLocal() as db:
        rows = [User(username=f"timer{i}", password_hash="unused") for i in range(4)]
        db.add_all(rows)
        db.commit()
        ids = [row.id for row in rows]
    advance = Flaky(game_engine.advance)
    results = Flaky(websocket.calculate_results)
    monkeypatch.setattr(game_engine, "advance", advance)
    monkeypatch.setattr(websocket, "calculate_results", results)
    sockets = connect(ids)

    async def main():
        room = create_room(ids)
        websocket.start_timer(room)
        now[0] = 50
        assert await async_wait(lambda: room.status is RoomStatus.PLAYING)
        advance.pending = 2
        assert await async_wait(lambda: advance.after_failure > 0)
        results.pending = 1
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        assert await websocket.save_result(room) is True
        return room

    room = asyncio.run(main())
    assert room.result_saved
    with SessionLocal() as db:
        count = lambda model: db.scalar(select(func.count()).select_from(model))
        assert count(Match) == 1
        assert count(MatchPlayerResult) == 4
        assert count(RankHistory) == 4
        assert count(MatchReward) == 4
        assert count(UserWallet) == 4
        assert count(UserLevel) == 4
    assert_finished_once(room, sockets)
    assert all(socket.count("rank_settlement") == 1 for socket in sockets.values())
    assert all(socket.count("reward_settlement") == 1 for socket in sockets.values())


class BrokenSocket(Socket):
    async def send_json(self, message):
        raise RuntimeError("connection lost")


def abort_by_failures(now, monkeypatch, user_ids, other_ids=None, before=None):
    flaky = Flaky(game_engine.advance)
    monkeypatch.setattr(game_engine, "advance", flaky)

    async def main():
        room = create_room(list(user_ids))
        other = create_room(list(other_ids)) if other_ids else None
        flaky.rooms = {room.room_id}
        websocket.start_timer(room)
        if other:
            websocket.start_timer(other)
        now[0] = 50
        assert await async_wait(lambda: room.status is RoomStatus.PLAYING)
        if before:
            await before(room)
        flaky.always = True
        assert await async_wait(lambda: room.room_id not in websocket.timers)
        if other:
            now[0] = 310
            assert await async_wait(lambda: not websocket.timers)
        await websocket.stop_timers()
        return room, other

    return flaky, main


def test_repeated_failure_aborts_room_and_notifies_once(now, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    sockets = connect([1, 2, 3, 4])
    flaky, main = abort_by_failures(now, monkeypatch, [1, 2], [3, 4])
    room, other = asyncio.run(main())
    assert flaky.failed == websocket.TIMER_MAX_FAILURES
    assert room.status is RoomStatus.ABORTED and room.result is None
    assert websocket.get_room(room.room_id) is None
    assert all(websocket.get_player_room(uid) is None for uid in (1, 2))
    assert websocket.timers == {}
    for uid in (1, 2):
        assert sockets[uid].messages[-1] == aborted(room)
        assert sockets[uid].count("game_aborted") == 1
        assert sockets[uid].count("game_result") == 0
    assert_finished_once(other, {k: sockets[k] for k in (3, 4)})
    assert all(sockets[k].count("game_aborted") == 0 for k in (3, 4))
    assert asyncio.run(websocket.abort_game(room)) is False
    assert sockets[1].count("game_aborted") == 1


def test_abort_skips_room_with_result_or_already_removed(now):
    room = create_room([1, 2])
    room.result = {"room_id": str(room.room_id), "winner_id": 1, "results": []}
    sockets = connect([1, 2])
    assert asyncio.run(websocket.abort_game(room)) is False
    assert room.status is RoomStatus.READY and websocket.get_room(room.room_id) is room
    gone = create_room([3, 4])
    websocket.remove_room(gone.room_id)
    assert asyncio.run(websocket.abort_game(gone)) is False
    assert gone.status is RoomStatus.READY
    assert all(socket.messages == [] for socket in sockets.values())


def test_aborted_room_rejects_cards_and_ultimate(now, monkeypatch):
    sockets = connect([1, 2])
    flaky, main = abort_by_failures(now, monkeypatch, [1, 2])
    room, _ = asyncio.run(main())
    player = room.players[1]
    before = (list(player.hand), player.cost, player.cards_used, player.ultimate_used)
    socket = Socket()
    asyncio.run(websocket.card_use(1, socket, {"type": "card_use", "hand_index": 0, "target_x": 1, "target_y": 0}))
    asyncio.run(websocket.ultimate_use(1, socket, {"type": "ultimate_use"}))
    assert [m["code"] for m in socket.messages] == ["not_in_room", "not_in_room"]
    assert (list(player.hand), player.cost, player.cards_used, player.ultimate_used) == before
    assert not game_engine.is_active(room, 50)
    with pytest.raises(ValueError, match="game_not_playing"):
        game_engine.check_card(room, 1, 0, 50)
    with pytest.raises(ValueError, match="game_not_playing"):
        game_engine.check_ultimate(room, 1, 50)
    assert flaky.original(room, 400) == [] and room.status is RoomStatus.ABORTED


def test_abort_notice_reaches_others_when_one_send_fails(now, monkeypatch):
    sockets = connect([1, 2, 3, 4])
    websocket.connections[1] = BrokenSocket()
    _, main = abort_by_failures(now, monkeypatch, [1, 2, 3, 4])
    room, _ = asyncio.run(main())
    assert all(sockets[uid].messages[-1] == aborted(room) for uid in (2, 3, 4))
    assert all(sockets[uid].count("game_aborted") == 1 for uid in (2, 3, 4))


def test_disconnected_player_reconnects_to_lobby_after_abort(now, monkeypatch):
    sockets = connect([1])

    async def drop_player(room):
        with websocket.state_lock:
            websocket.set_player_connected(2, False)

    _, main = abort_by_failures(now, monkeypatch, [1, 2], before=drop_player)
    room, _ = asyncio.run(main())
    assert sockets[1].count("game_aborted") == 1
    fresh = Socket()
    asyncio.run(websocket.connect(2, fresh))
    assert fresh.messages == [{"type": "auth_success", "user_id": 2}]
    asyncio.run(websocket.room_state_request(2, fresh))
    assert fresh.messages[-1]["code"] == "not_in_room"
    leave = Socket()
    asyncio.run(websocket.room_leave(2, leave))
    assert leave.messages[-1]["code"] == "not_in_room"


def test_players_can_queue_again_after_abort(now, monkeypatch):
    sockets = connect([1, 2])
    _, main = abort_by_failures(now, monkeypatch, [1, 2])
    room, _ = asyncio.run(main())
    monkeypatch.setattr(websocket.decks, "load_deck", lambda user_id: list(DEFAULT_DECK))
    monkeypatch.setattr(game_engine, "advance", Flaky(game_engine.advance).original)
    monkeypatch.setattr(websocket.matchmaking, "MATCH_SIZE", 2)

    async def rejoin():
        for uid in (1, 2):
            await websocket.queue_join(uid, sockets[uid], {"type": "queue_join", "character_id": "character_01"})
        new = websocket.get_player_room(1)
        state = (new, list(websocket.timers))
        await websocket.stop_timers()
        return state

    new, timers = asyncio.run(rejoin())
    assert new is not None and new is not room and new.room_id != room.room_id
    assert websocket.get_player_room(2) is new
    assert timers == [new.room_id]
    assert sockets[1].types()[-3:] == ["queue_waiting", "match_found", "game_ready"]


def test_aborted_match_saves_nothing_and_pays_nothing(client, now, monkeypatch):
    with SessionLocal() as db:
        rows = [User(username=f"abort{i}", password_hash="unused") for i in range(4)]
        db.add_all(rows)
        db.commit()
        ids = [row.id for row in rows]
    connect(ids)
    _, main = abort_by_failures(now, monkeypatch, ids)
    room, _ = asyncio.run(main())
    assert room.status is RoomStatus.ABORTED and not room.result_saved
    with SessionLocal() as db:
        for model in (Match, MatchPlayerResult, RankHistory, MatchReward, UserWallet, UserLevel):
            assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__


class BrokenResults:
    def __init__(self, original, errors):
        self.original = original
        self.errors = list(errors)
        self.rooms = set()
        self.calls = 0

    def __call__(self, room):
        if room.room_id in self.rooms:
            self.calls += 1
            if self.errors:
                raise self.errors.pop(0)("boom")
        return self.original(room)


def finish_with_result_error(now, monkeypatch, user_ids, errors=(ValueError,), other_ids=None, start_at=299):
    results = BrokenResults(websocket.calculate_results, errors)
    monkeypatch.setattr(websocket, "calculate_results", results)
    saved = []
    original_save = websocket.save_result

    async def recording_save(room):
        saved.append(room.room_id)
        return await original_save(room)

    monkeypatch.setattr(websocket, "save_result", recording_save)

    async def main():
        room = create_room(list(user_ids))
        other = create_room(list(other_ids)) if other_ids else None
        results.rooms = {room.room_id}
        websocket.start_timer(room)
        if other:
            websocket.start_timer(other)
        now[0] = start_at
        assert await async_wait(lambda: room.status is not RoomStatus.READY)
        now[0] = 310
        assert await async_wait(lambda: not websocket.timers)
        await websocket.stop_timers()
        return room, other

    return results, saved, main


def test_result_value_error_aborts_instead_of_finishing(now, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    sockets = connect([1, 2])
    results, saved, main = finish_with_result_error(now, monkeypatch, [1, 2])
    room, _ = asyncio.run(main())
    assert results.calls == 1
    assert room.status is RoomStatus.ABORTED and room.result is None
    assert websocket.get_room(room.room_id) is None
    assert websocket.get_player_room(1) is None and websocket.get_player_room(2) is None
    assert websocket.timers == {} and saved == []
    for uid in (1, 2):
        assert sockets[uid].messages[-1] == aborted(room)
        assert sockets[uid].count("game_aborted") == 1
        assert sockets[uid].count("game_finished") == 0 and sockets[uid].count("game_result") == 0
    assert [r.getMessage() for r in timer_logs(caplog, room)] == [f"game result failed: {room.room_id}"]
    names = [r["type"] for r in read_log(room)]
    assert "game_aborted" in names and "game_finished" not in names and "game_result" not in names
    assert asyncio.run(websocket.abort_game(room)) is False
    with websocket.state_lock:
        assert websocket.abort_room(room) is None
    assert sockets[1].count("game_aborted") == 1


def test_result_value_error_keeps_other_events_in_order(now, monkeypatch):
    sockets = connect([1, 2])
    _, _, main = finish_with_result_error(now, monkeypatch, [1, 2], start_at=50)
    room, _ = asyncio.run(main())
    types = sockets[1].types()
    assert types.count("deathmatch_started") == 1 and types.count("game_aborted") == 1
    assert types.index("deathmatch_started") < types.index("game_aborted") == len(types) - 1
    assert "game_finished" not in types


def test_pending_finish_from_transient_error_not_sent_after_value_error(now, monkeypatch):
    sockets = connect([1, 2])
    results, saved, main = finish_with_result_error(now, monkeypatch, [1, 2], errors=(RuntimeError, ValueError))
    room, _ = asyncio.run(main())
    assert results.calls == 2
    assert room.status is RoomStatus.ABORTED and saved == []
    for uid in (1, 2):
        assert sockets[uid].count("game_finished") == 0
        assert sockets[uid].count("game_aborted") == 1


def test_transient_result_error_still_finishes_normally(now, monkeypatch):
    sockets = connect([1, 2])
    results, saved, main = finish_with_result_error(now, monkeypatch, [1, 2], errors=(RuntimeError,))
    monkeypatch.setattr(websocket, "store_result", lambda room: None)
    room, _ = asyncio.run(main())
    assert results.calls == 2
    assert saved == [room.room_id]
    assert_finished_once(room, sockets)
    assert all(socket.count("game_aborted") == 0 for socket in sockets.values())


def test_result_value_error_isolated_from_other_room(now, monkeypatch):
    sockets = connect([1, 2, 3, 4])
    websocket.connections[1] = BrokenSocket()
    monkeypatch.setattr(websocket, "store_result", lambda room: None)
    _, saved, main = finish_with_result_error(now, monkeypatch, [1, 2], other_ids=[3, 4])
    room, other = asyncio.run(main())
    assert sockets[2].messages[-1] == aborted(room)
    assert room.status is RoomStatus.ABORTED
    assert saved == [other.room_id]
    assert_finished_once(other, {k: sockets[k] for k in (3, 4)})
    assert all(sockets[k].count("game_aborted") == 0 for k in (3, 4))


def test_result_value_error_reconnect_and_requeue(now, monkeypatch):
    sockets = connect([1, 2])
    _, _, main = finish_with_result_error(now, monkeypatch, [1, 2])
    room, _ = asyncio.run(main())
    fresh = Socket()
    asyncio.run(websocket.connect(2, fresh))
    assert fresh.messages == [{"type": "auth_success", "user_id": 2}]
    monkeypatch.setattr(websocket.decks, "load_deck", lambda user_id: list(DEFAULT_DECK))
    monkeypatch.setattr(websocket.matchmaking, "MATCH_SIZE", 2)

    async def rejoin():
        await websocket.queue_join(1, sockets[1], {"type": "queue_join", "character_id": "character_01"})
        await websocket.queue_join(2, fresh, {"type": "queue_join", "character_id": "character_01"})
        new = websocket.get_player_room(1)
        await websocket.stop_timers()
        return new

    new = asyncio.run(rejoin())
    assert new is not None and new.room_id != room.room_id
    assert websocket.get_player_room(2) is new
    assert fresh.types()[-2:] == ["match_found", "game_ready"]


def test_result_value_error_saves_and_pays_nothing(client, now, monkeypatch):
    with SessionLocal() as db:
        rows = [User(username=f"noresult{i}", password_hash="unused") for i in range(4)]
        db.add_all(rows)
        db.commit()
        ids = [row.id for row in rows]
    connect(ids)
    _, saved, main = finish_with_result_error(now, monkeypatch, ids)
    room, _ = asyncio.run(main())
    assert room.status is RoomStatus.ABORTED and saved == [] and not room.result_saved
    with SessionLocal() as db:
        for model in (Match, MatchPlayerResult, RankHistory, MatchReward, UserWallet, UserLevel):
            assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
