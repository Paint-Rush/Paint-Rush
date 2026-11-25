import logging

import pytest

from app import cards, game_room, maps, matchmaking, websocket
from app.database import SessionLocal
from app.game_room import create_room, get_player_room
from app.matchmaking import join_queue, try_match
from app.models import User
from tests.test_game_engine import client, now
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
    assert matchmaking.MAP_IDS == ("default", "crossroads", "glass_garden")
    assert matchmaking.available_maps() == ["default", "crossroads", "glass_garden"]
    assert {matchmaking.choose_map() for _ in range(300)} == {"default", "crossroads", "glass_garden"}


def test_default_map_assigned(now, monkeypatch):
    monkeypatch.setattr(matchmaking, "MAP_IDS", ("default",))
    join_at(now, 0, 1, 2, 3, 4)
    assert try_match().map_id == "default"


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
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    join_at(now, 0, 1, 2, 3, 4)
    create_room([2, 9])
    with websocket.state_lock:
        assert websocket.create_matches() == ([], [2])
    assert len(failure_logs(caplog)) == 1
    assert matchmaking.get_waiting_players() == [1, 3, 4]
    assert get_player_room(1) is None

    join_at(now, 12, 5)
    assert try_match().player_order == [1, 3, 4, 5]
    assert get_player_room(2).player_order == [2, 9]
    assert len(failure_logs(caplog)) == 1


def test_deck_failure_drops_candidates_without_repeating(now, caplog, monkeypatch):
    caplog.set_level(logging.ERROR, logger=websocket.logger.name)
    join_at(now, 0, 1, 2, 3, 4, 5)
    original = cards.initialize_deck
    broken = lambda player, card_ids, rng=None: (_ for _ in ()).throw(ValueError("broken"))
    monkeypatch.setattr(cards, "initialize_deck", broken)
    with websocket.state_lock:
        matched, dropped = websocket.create_matches()
    assert (matched, dropped) == ([], [1, 2, 3, 4])
    assert len(failure_logs(caplog)) == 1
    assert matchmaking.get_waiting_players() == [5]
    assert game_room.rooms == {}
    assert 1 not in matchmaking.selections and 1 not in matchmaking.decks

    monkeypatch.setattr(cards, "initialize_deck", original)
    join_at(now, 11, 1, 2, 3)
    assert try_match().player_order == [5, 1, 2, 3]


@pytest.fixture
def players(client):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(4)]
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
    sockets = [client.websocket_connect(WS_URL) for _ in players]
    try:
        for ws, uid in zip(sockets, players):
            ws.__enter__()
            auth(ws, uid)
        for ws in sockets:
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        for ws in sockets:
            assert ws.receive_json()["code"] == "match_failed"
            assert ws.receive_json() == {"type": "queue_cancelled"}
        assert matchmaking.get_waiting_players() == []
        assert game_room.rooms == {}

        for ws in sockets:
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        assert all(ws.receive_json()["type"] == "match_found" for ws in sockets)
    finally:
        for ws in sockets:
            ws.__exit__(None, None, None)
    assert len({id(get_player_room(uid)) for uid in players}) == 1
    assert get_player_room(players[0]).player_order == players
