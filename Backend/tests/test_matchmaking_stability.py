import asyncio
import logging

import pytest

from app import cards, game_room, maps, matchmaking, websocket
from app.database import SessionLocal
from app.game_room import create_room, get_player_room
from app.matchmaking import join_queue, match_deadline, try_match
from app.models import User
from tests.test_game_engine import async_wait, client, now
from tests.test_websocket import WS_URL, auth, clear_state


@pytest.fixture(autouse=True)
def clean(now):
    clear_state()
    yield
    clear_state()


def join_at(now, t, *user_ids):
    now[0] = t
    for user_id in user_ids:
        join_queue(user_id)


def failure_logs(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("match creation failed")]


def test_test_map_not_a_candidate():
    assert "test" in maps.list_maps()
    assert matchmaking.MAP_IDS == ("default",)
    assert matchmaking.available_maps() == ["default"]
    assert {matchmaking.choose_map() for _ in range(30)} == {"default"}


def test_default_map_assigned(now):
    join_at(now, 0, 1, 2, 3, 4)
    assert try_match(0).map_id == "default"


def test_allowed_maps_can_be_added(monkeypatch):
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("default", "test"))
    assert matchmaking.available_maps() == ["default", "test"]
    assert {matchmaking.choose_map() for _ in range(100)} == {"default", "test"}


@pytest.mark.parametrize("map_ids", [(), ("missing",), ("BAD ID",)])
def test_no_candidates_falls_back_to_default(monkeypatch, map_ids):
    monkeypatch.setattr(matchmaking, "MAP_IDS", map_ids)
    assert matchmaking.available_maps() == []
    assert matchmaking.choose_map() == "default"


def test_room_failure_drops_blocking_player_once_then_recovers(now, caplog):
    async def main():
        join_at(now, 0, 1, 2)
        create_room([2, 9])
        with websocket.state_lock:
            websocket.schedule_match_timer()
        task = websocket.match_task
        now[0] = 10
        assert await async_wait(task.done)
        await asyncio.sleep(websocket.TIMER_STEP * 4)
        after_failure = (matchmaking.get_waiting_players(), match_deadline(), websocket.match_task)

        now[0] = 12
        with websocket.state_lock:
            join_queue(3)
            websocket.schedule_match_timer()
        now[0] = 21
        await asyncio.sleep(websocket.TIMER_STEP * 4)
        before_deadline = get_player_room(1)
        now[0] = 22
        assert await async_wait(lambda: get_player_room(1) is not None)
        await websocket.stop_timers()
        return after_failure, before_deadline

    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    (waiting, deadline, task), before_deadline = asyncio.run(main())
    assert len(failure_logs(caplog)) == 1
    assert (waiting, deadline, task) == ([1], None, None)
    assert before_deadline is None
    assert get_player_room(1).player_order == [1, 3]
    assert get_player_room(2).player_order == [2, 9]
    assert websocket.match_task is None


def test_deck_failure_drops_candidates_without_repeating(now, caplog, monkeypatch):
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    join_at(now, 0, 1, 2, 3)
    now[0] = 10
    original = cards.initialize_deck
    broken = lambda player, card_ids, rng=None: (_ for _ in ()).throw(ValueError("broken"))
    monkeypatch.setattr(cards, "initialize_deck", broken)
    with websocket.state_lock:
        matched, dropped = websocket.create_matches()
    assert (matched, dropped) == ([], [1, 2])
    assert len(failure_logs(caplog)) == 1
    assert matchmaking.get_waiting_players() == [3]
    assert match_deadline() is None
    assert game_room.rooms == {}
    assert 1 not in matchmaking.selections and 1 not in matchmaking.decks

    monkeypatch.setattr(cards, "initialize_deck", original)
    join_at(now, 11, 1)
    assert try_match(21).player_order == [3, 1]


def test_shutdown_after_failure_leaves_no_task(now):
    async def main():
        join_at(now, 0, 1, 2, 3)
        create_room([1, 9])
        with websocket.state_lock:
            websocket.schedule_match_timer()
        now[0] = 10
        await asyncio.sleep(websocket.TIMER_STEP * 4)
        task = websocket.match_task
        await websocket.stop_timers()
        return task

    task = asyncio.run(main())
    assert task is None or task.done()
    assert websocket.match_task is None


@pytest.fixture
def players(client):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(2)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def test_ws_failed_match_notifies_and_rejoin_works(client, players, now, monkeypatch):
    original = cards.initialize_deck
    calls = []

    def broken_once(player, card_ids, *args, **kwargs):
        calls.append(player.user_id)
        if len(calls) == 1:
            raise ValueError("broken")
        return original(player, card_ids, *args, **kwargs)

    monkeypatch.setattr(cards, "initialize_deck", broken_once)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, players[0])
        auth(ws2, players[1])
        for ws in (ws1, ws2):
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        now[0] = 10
        for ws in (ws1, ws2):
            assert ws.receive_json()["code"] == "match_failed"
            assert ws.receive_json() == {"type": "queue_cancelled"}
        assert matchmaking.get_waiting_players() == []
        assert game_room.rooms == {}

        for ws in (ws1, ws2):
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        now[0] = 20
        assert ws1.receive_json()["type"] == ws2.receive_json()["type"] == "match_found"
    assert get_player_room(players[0]) is get_player_room(players[1])
