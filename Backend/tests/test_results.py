import asyncio
import copy
import uuid

import pytest

from app import websocket
from app.game_engine import advance, start
from app.game_room import RoomStatus, create_room, get_player_room
from app.results import calculate_results
from tests.test_game_engine import client, now, receive_until, users
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, wait_until


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def finished_room(user_ids=(1, 2)):
    room = create_room(list(user_ids))
    start(room, 0)
    advance(room, 310)
    return room


def set_stats(room, user_id, tiles=0, painted=0, kills=0, cards=0, cost=0, deaths=0):
    player = room.players[user_id]
    room.tile_counts[user_id] = tiles
    player.total_painted = painted
    player.kills = kills
    player.cards_used = cards
    player.cost_used = cost
    player.deaths = deaths


def ranking(room):
    return [r["user_id"] for r in calculate_results(room)["results"]]


@pytest.mark.parametrize("stats_1, stats_2, winner", [
    ({"tiles": 5}, {"tiles": 9, "painted": 0}, 2),
    ({"tiles": 5, "painted": 3}, {"tiles": 5, "painted": 8}, 2),
    ({"tiles": 5, "painted": 8, "kills": 1}, {"tiles": 5, "painted": 8, "kills": 2}, 2),
    ({"tiles": 5, "painted": 8, "kills": 2, "cards": 9}, {"tiles": 5, "painted": 8, "kills": 2, "cards": 4}, 2),
    ({"cards": 4, "cost": 20}, {"cards": 4, "cost": 12}, 2),
    ({"tiles": 3, "painted": 3, "kills": 1, "cards": 2, "cost": 5},
     {"tiles": 3, "painted": 3, "kills": 1, "cards": 2, "cost": 5}, 1),
])
def test_tie_breakers(stats_1, stats_2, winner):
    room = finished_room()
    set_stats(room, 1, **stats_1)
    set_stats(room, 2, **stats_2)
    result = calculate_results(room)
    assert result["winner_id"] == winner
    assert [r["is_winner"] for r in result["results"]] == [True, False]
    assert [r["rank"] for r in result["results"]] == [1, 2]


def test_earlier_criteria_take_priority():
    room = finished_room()
    set_stats(room, 1, tiles=10, painted=10, kills=0, cards=50, cost=200)
    set_stats(room, 2, tiles=9, painted=99, kills=9, cards=1, cost=1)
    assert ranking(room) == [1, 2]


def test_queue_order_tie_uses_player_order():
    room = finished_room([7, 3])
    assert ranking(room) == [7, 3]


def test_deaths_do_not_affect_rank():
    room = finished_room()
    set_stats(room, 1, deaths=50)
    assert ranking(room) == [1, 2]


def test_two_player_result_fields():
    room = finished_room()
    set_stats(room, 1, tiles=4, painted=6, kills=1, cards=3, cost=8, deaths=2)
    set_stats(room, 2, tiles=7, painted=7, kills=0, cards=5, cost=12, deaths=1)
    assert calculate_results(room) == {
        "room_id": str(room.room_id),
        "winner_id": 2,
        "results": [
            {"user_id": 2, "rank": 1, "is_winner": True, "painted_tiles": 7, "total_painted": 7,
             "kills": 0, "deaths": 1, "cards_used": 5, "cost_used": 12},
            {"user_id": 1, "rank": 2, "is_winner": False, "painted_tiles": 4, "total_painted": 6,
             "kills": 1, "deaths": 2, "cards_used": 3, "cost_used": 8},
        ],
    }


def test_three_players():
    room = finished_room([1, 2, 3])
    set_stats(room, 1, tiles=2)
    set_stats(room, 2, tiles=5)
    set_stats(room, 3, tiles=2, painted=4)
    result = calculate_results(room)
    assert [r["user_id"] for r in result["results"]] == [2, 3, 1]
    assert [r["rank"] for r in result["results"]] == [1, 2, 3]
    assert sum(r["is_winner"] for r in result["results"]) == 1


def test_four_players():
    room = finished_room([4, 3, 2, 1])
    set_stats(room, 1, tiles=3, painted=3, kills=1, cards=2, cost=5)
    set_stats(room, 2, tiles=3, painted=3, kills=1, cards=2, cost=4)
    set_stats(room, 3, tiles=3, painted=3, kills=1, cards=2, cost=5)
    set_stats(room, 4, tiles=1, painted=9)
    result = calculate_results(room)
    assert [r["user_id"] for r in result["results"]] == [2, 3, 1, 4]
    assert result["winner_id"] == 2
    assert sum(r["is_winner"] for r in result["results"]) == 1


def test_disconnected_player_included():
    room = finished_room()
    room.players[1].connected = False
    set_stats(room, 1, tiles=5)
    result = calculate_results(room)
    assert result["winner_id"] == 1
    assert len(result["results"]) == 2


@pytest.mark.parametrize("t, status", [(5, RoomStatus.READY), (100, RoomStatus.PLAYING), (260, RoomStatus.DEATHMATCH)])
def test_rejected_before_finished(t, status):
    room = create_room([1, 2])
    start(room, 0)
    advance(room, t)
    assert room.status is status
    with pytest.raises(ValueError):
        calculate_results(room)


def test_stats_unchanged_and_repeatable():
    room = finished_room([1, 2, 3])
    set_stats(room, 1, tiles=2, painted=5, kills=1, cards=3, cost=7)
    set_stats(room, 3, tiles=2, painted=5, kills=1, cards=3, cost=7)
    def snapshot():
        players = {uid: {k: v for k, v in vars(p).items() if k != "draw_queue"} for uid, p in room.players.items()}
        return copy.deepcopy(players), dict(room.tile_counts), list(room.player_order), room.status

    before = snapshot()
    first = calculate_results(room)
    second = calculate_results(room)
    assert first == second
    assert first is not second
    assert snapshot() == before


@pytest.mark.parametrize("corrupt", [
    lambda room: room.player_order.clear(),
    lambda room: room.player_order.append(1),
    lambda room: room.player_order.append(99),
    lambda room: room.player_order.pop(),
    lambda room: room.tile_counts.pop(1),
    lambda room: setattr(room.players[1], "user_id", 99),
    lambda room: room.players.clear(),
])
def test_invalid_room_data(corrupt):
    room = finished_room()
    corrupt(room)
    with pytest.raises(ValueError):
        calculate_results(room)


def test_rooms_independent():
    a = finished_room([1, 2])
    b = finished_room([3, 4])
    set_stats(a, 2, tiles=1)
    set_stats(b, 3, tiles=1)
    ra, rb = calculate_results(a), calculate_results(b)
    assert (ra["winner_id"], rb["winner_id"]) == (2, 3)
    assert ra["room_id"] != rb["room_id"]


def test_result_sent_after_finish_and_restored(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            auth(ws2, users[1])
            join_and_match(ws1, ws2)
            room = get_player_room(users[0])
            room_id = str(room.room_id)
            now[0] = 100
            assert wait_until(lambda: room.status is RoomStatus.PLAYING)
            with websocket.state_lock:
                room.tile_counts[users[1]] = 3
                room.players[users[1]].total_painted = 3

            now[0] = 310
            messages = {}
            for ws in (ws1, ws2):
                assert receive_until(ws, "game_finished") == {"type": "game_finished", "room_id": room_id}
                messages[ws] = ws.receive_json()
            assert messages[ws1] == messages[ws2]
            result = messages[ws1]
            assert result["type"] == "game_result"
            assert result["room_id"] == room_id
            assert result["winner_id"] == users[1]
            assert [r["user_id"] for r in result["results"]] == [users[1], users[0]]
            assert wait_until(lambda: not websocket.timers)
            stored = room.result

        ws2.receive_json()
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            state = ws1.receive_json()
            assert state["type"] == "room_state"
            assert state["status"] == "FINISHED"
            assert ws1.receive_json() == result
            assert room.result is stored
            assert get_player_room(users[0]) is room
            ws1.send_json({"type": "queue_join"})
            assert ws1.receive_json()["code"] == "already_in_game"
        assert len({r.room_id for r in [room, get_player_room(users[1])]}) == 1


def test_result_not_duplicated(client, users, now, monkeypatch):
    calls = []
    original = websocket.calculate_results

    def counting(room):
        calls.append(room.room_id)
        return original(room)

    monkeypatch.setattr(websocket, "calculate_results", counting)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        now[0] = 310
        receive_until(ws1, "game_result")
        assert wait_until(lambda: not websocket.timers)
        now[0] = 400
        with websocket.state_lock:
            assert advance(room, 400) == []
        ws1.send_json({"type": "room_state"})
        assert ws1.receive_json()["type"] == "room_state"
    assert calls == [room.room_id]


def test_result_failure_does_not_stop_other_rooms(now, monkeypatch):
    original = websocket.calculate_results

    def broken(room):
        if room.player_order[0] == 1:
            raise ValueError("boom")
        return original(room)

    monkeypatch.setattr(websocket, "calculate_results", broken)

    async def main():
        a, b = create_room([1, 2]), create_room([3, 4])
        websocket.start_timer(a)
        websocket.start_timer(b)
        now[0] = 310
        for _ in range(200):
            if not websocket.timers:
                break
            await asyncio.sleep(0.01)
        return a, b

    a, b = asyncio.run(main())
    assert a.status is b.status is RoomStatus.FINISHED
    assert a.result is None
    assert b.result["winner_id"] == 3
    assert uuid.UUID(b.result["room_id"]) == b.room_id
