import json
import threading
from collections import Counter
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import cards, decks, matchmaking
from app.cards import CARDS, DEFAULT_DECK, DECK_SIZE
from app.database import SessionLocal
from app.game_room import RoomStatus, get_player_room
from app.main import app
from app.matchmaking import cancel_queue, get_waiting_players, join_queue, try_match
from app.models import User, UserDeck
from tests.test_game_engine import client, now, receive_until
from tests.test_websocket import WS_URL, auth, clear_state, make_token, users, wait_until

URL = "/users/me/deck"
BOMBS = ["attack_bomb"] * 6
CUSTOM = ["attack_bomb", "attack_bomb", "move_jump", "move_jump", "attack_slash", "move_dash"]
DASHES = ["move_dash"] * 6


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def headers(user_id, **kwargs):
    return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}


def put(client, user_id, cards):
    return client.put(URL, json={"cards": cards}, headers=headers(user_id))


def get(client, user_id):
    return client.get(URL, headers=headers(user_id))


def stored(user_id):
    with SessionLocal() as db:
        return db.scalar(select(UserDeck.cards).where(UserDeck.user_id == user_id))


def all_cards(player):
    return Counter(player.hand + list(player.draw_queue))


def join(ws, **extra):
    ws.send_json({"type": "queue_join", **extra})
    return ws.receive_json()


def match(client, ws1, ws2, users, **extra):
    auth(ws1, users[0])
    auth(ws2, users[1])
    assert join(ws1, **extra)["type"] == "queue_waiting"
    assert join(ws2)["type"] == "queue_waiting"
    receive_until(ws1, "game_ready")
    receive_until(ws2, "game_ready")
    return get_player_room(users[0])


def test_get_default_deck(client, users):
    r = get(client, users[0])
    assert r.status_code == 200
    assert r.json() == {"cards": DEFAULT_DECK}
    assert stored(users[0]) is None


def test_save_and_reload_deck(client, users):
    r = put(client, users[0], CUSTOM)
    assert r.status_code == 200
    assert r.json() == {"cards": CUSTOM}
    assert get(client, users[0]).json() == {"cards": CUSTOM}
    assert stored(users[0]) == CUSTOM


def test_update_existing_deck(client, users):
    put(client, users[0], CUSTOM)
    assert put(client, users[0], BOMBS).json() == {"cards": BOMBS}
    assert get(client, users[0]).json() == {"cards": BOMBS}
    with SessionLocal() as db:
        assert len(db.scalars(select(UserDeck)).all()) == 1


def test_deck_survives_restart(client, users):
    put(client, users[0], CUSTOM)
    with TestClient(app) as restarted:
        assert get(restarted, users[0]).json() == {"cards": CUSTOM}
    with SessionLocal() as db:
        assert db.get(User, users[0]) is not None


@pytest.mark.parametrize("size", [0, 1, 5, 7, 12])
def test_deck_must_have_six_cards(client, users, size):
    put(client, users[0], CUSTOM)
    r = put(client, users[0], ["move_dash"] * size)
    assert r.status_code == 422
    assert stored(users[0]) == CUSTOM


@pytest.mark.parametrize("card_id", ["ghost", "", "defense_shield", "MOVE_DASH", " move_dash"])
def test_unknown_card_rejected(client, users, card_id):
    r = put(client, users[0], DASHES[:5] + [card_id])
    assert r.status_code == 422
    assert stored(users[0]) is None


@pytest.mark.parametrize("card_id", [1, None, True, 2.5, ["move_dash"], {"id": "move_dash"}])
def test_non_string_card_rejected(client, users, card_id):
    r = put(client, users[0], DASHES[:5] + [card_id])
    assert r.status_code == 422
    assert stored(users[0]) is None


@pytest.mark.parametrize("body", [{}, {"cards": "move_dash"}, {"cards": None}, {"deck": DASHES}, []])
def test_invalid_body_rejected(client, users, body):
    r = client.put(URL, json=body, headers=headers(users[0]))
    assert r.status_code == 422
    assert stored(users[0]) is None


def test_duplicate_cards_allowed(client, users):
    assert put(client, users[0], DASHES).status_code == 200
    assert get(client, users[0]).json() == {"cards": DASHES}


def test_jwt_required(client, users):
    bad = [
        {},
        {"Authorization": "Bearer nope"},
        {"Authorization": make_token(users[0])},
        headers(users[0], expires=timedelta(seconds=-1)),
        headers(users[0], key="wrong-secret-key-wrong-secret-key"),
        headers(9999),
    ]
    for h in bad:
        assert client.get(URL, headers=h).status_code == 401
        assert client.put(URL, json={"cards": CUSTOM}, headers=h).status_code == 401
    with SessionLocal() as db:
        assert db.scalars(select(UserDeck)).all() == []


def test_cannot_change_other_account(client, users):
    put(client, users[1], CUSTOM)
    r = client.put(URL, json={"cards": BOMBS, "user_id": users[1]}, headers=headers(users[0]))
    assert r.status_code == 200
    assert stored(users[0]) == BOMBS
    assert stored(users[1]) == CUSTOM
    for path in (f"/users/{users[1]}/deck", "/users/deck"):
        assert client.put(path, json={"cards": BOMBS}, headers=headers(users[0])).status_code in (404, 405)


def test_decks_independent(client, users):
    put(client, users[0], CUSTOM)
    put(client, users[1], BOMBS)
    assert get(client, users[0]).json() == {"cards": CUSTOM}
    assert get(client, users[1]).json() == {"cards": BOMBS}
    assert get(client, users[2]).json() == {"cards": DEFAULT_DECK}


def test_save_failure_rolls_back(client, users, monkeypatch):
    put(client, users[0], CUSTOM)

    def fail(self):
        raise OperationalError("commit", {}, Exception("db down"))

    monkeypatch.setattr(Session, "commit", fail)
    r = put(client, users[0], BOMBS)
    assert r.status_code == 500
    monkeypatch.undo()
    assert stored(users[0]) == CUSTOM
    assert put(client, users[1], BOMBS).status_code == 200


def test_cards_endpoint(client):
    r = client.get("/cards")
    assert r.status_code == 200
    body = r.json()
    assert body["deck_size"] == DECK_SIZE
    assert body["cards"] == [{"card_id": cid, "type": c.type.value, "cost": c.cost} for cid, c in CARDS.items()]
    assert set(body["cards"][0]) == {"card_id", "type", "cost"}


def test_match_uses_saved_deck(client, users):
    put(client, users[0], CUSTOM)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = match(client, ws1, ws2, users)
        a, b = room.players[users[0]], room.players[users[1]]
        assert Counter(a.deck) == Counter(CUSTOM) == all_cards(a)
        assert all_cards(b) == Counter(DEFAULT_DECK)
        for player in (a, b):
            assert len(player.hand) == 3 and len(player.draw_queue) == 3
        assert matchmaking.decks == {}

        ws1.send_json({"type": "room_state"})
        state = receive_until(ws1, "room_state")
        assert state["my_hand"] == a.hand
        assert "deck" not in json.dumps(state)
        assert state["my_next_card"] == next(iter(a.draw_queue))


def test_match_keeps_character_selection(client, users):
    put(client, users[0], BOMBS)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = match(client, ws1, ws2, users, character_id="character_02")
        assert room.players[users[0]].character_id == "character_02"
        assert room.players[users[1]].character_id == "character_01"
        assert room.players[users[0]].deck == BOMBS
        assert matchmaking.selections == {}


def test_each_match_gets_new_deck_objects():
    join_queue(1, deck=CUSTOM)
    join_queue(2, deck=CUSTOM)
    room = try_match()
    a, b = room.players[1], room.players[2]
    assert a.deck is not b.deck and a.hand is not b.hand
    assert a.deck is not CUSTOM and b.deck is not CUSTOM
    assert a.draw_queue is not b.draw_queue
    a.deck[0] = "changed"
    assert "changed" not in b.deck
    assert CUSTOM == ["attack_bomb", "attack_bomb", "move_jump", "move_jump", "attack_slash", "move_dash"]


def test_join_queue_rejects_invalid_deck():
    for deck in (["ghost"] * 6, DASHES[:5], [1] * 6, "move_dash"):
        with pytest.raises(ValueError, match="invalid_deck"):
            join_queue(1, deck=deck)
    assert get_waiting_players() == []
    assert matchmaking.selections == {} and matchmaking.decks == {}


def test_queued_deck_copy_not_affected():
    deck = list(CUSTOM)
    join_queue(1, deck=deck)
    deck[0] = "ghost"
    assert matchmaking.decks[1] == CUSTOM


def test_cancel_and_disconnect_clear_deck(client, users):
    put(client, users[0], CUSTOM)
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        join(ws)
        assert matchmaking.decks == {users[0]: CUSTOM}
        ws.send_json({"type": "queue_cancel"})
        assert ws.receive_json() == {"type": "queue_cancelled"}
        assert matchmaking.decks == {} and matchmaking.selections == {}
        join(ws)
        assert matchmaking.decks == {users[0]: CUSTOM}
    assert wait_until(lambda: matchmaking.decks == {})
    assert get_waiting_players() == []


def test_match_failure_keeps_deck_selection(monkeypatch):
    join_queue(1, "character_02", CUSTOM)
    join_queue(2, deck=BOMBS)

    def broken(player, card_ids, rng=None):
        raise ValueError("broken")

    monkeypatch.setattr(cards, "initialize_deck", broken)
    with pytest.raises(ValueError):
        try_match()
    assert get_waiting_players() == [1, 2]
    assert matchmaking.decks == {1: CUSTOM, 2: BOMBS}
    assert matchmaking.selections == {1: "character_02", 2: "character_01"}
    assert get_player_room(1) is None
    monkeypatch.undo()
    room = try_match()
    assert Counter(room.players[1].deck) == Counter(CUSTOM)
    assert Counter(room.players[2].deck) == Counter(BOMBS)
    assert cancel_queue(1) is False


def test_deck_load_failure_isolated(client, users, monkeypatch):
    real = decks.load_deck

    def flaky(user_id):
        if user_id == users[0]:
            raise OperationalError("select", {}, Exception("db down"))
        return real(user_id)

    monkeypatch.setattr(decks, "load_deck", flaky)
    with (
        client.websocket_connect(WS_URL) as ws1,
        client.websocket_connect(WS_URL) as ws2,
        client.websocket_connect(WS_URL) as ws3,
    ):
        auth(ws1, users[0])
        auth(ws2, users[1])
        auth(ws3, users[2])
        reply = join(ws1)
        assert reply["code"] == "deck_unavailable"
        assert set(reply) == {"type", "code", "message"}
        assert get_waiting_players() == [] and matchmaking.decks == {}
        join(ws2)
        join(ws3)
        receive_until(ws2, "game_ready")
        room = get_player_room(users[1])
        assert room is get_player_room(users[2])
        assert get_player_room(users[0]) is None


def test_invalid_stored_deck_rejected_on_join(client, users):
    with SessionLocal() as db:
        db.add(UserDeck(user_id=users[0], cards=["ghost"] * 6))
        db.commit()
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        assert join(ws)["code"] == "invalid_deck"
        assert get_waiting_players() == [] and matchmaking.decks == {}


def test_reconnect_keeps_deck_state(client, users, now):
    put(client, users[0], CUSTOM)
    with client.websocket_connect(WS_URL) as ws2:
        ws1 = client.websocket_connect(WS_URL).__enter__()
        try:
            room = match(client, ws1, ws2, users)
            now[0] = 10
            assert wait_until(lambda: room.status is RoomStatus.PLAYING)
            player = room.players[users[0]]
            ws1.send_json({"type": "card_use", "hand_index": 0})
            receive_until(ws1, "hand_updated")
            expected = list(player.deck), list(player.hand), list(player.draw_queue), player.cost
        finally:
            ws1.__exit__(None, None, None)
        assert wait_until(lambda: not room.players[users[0]].connected)
        put(client, users[0], BOMBS)
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            state = receive_until(ws1, "room_state")
            assert state["my_hand"] == expected[1]
            assert state["my_next_card"] == expected[2][0]
            assert (list(player.deck), list(player.hand), list(player.draw_queue), player.cost) == expected


def test_saved_deck_change_during_queue_and_game(client, users, now):
    put(client, users[0], CUSTOM)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        assert put(client, users[0], BOMBS).status_code == 200
        assert matchmaking.decks == {users[0]: CUSTOM}
        join(ws2)
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        player = room.players[users[0]]
        assert Counter(player.deck) == Counter(CUSTOM)
        before = list(player.deck), list(player.hand), list(player.draw_queue)
        assert put(client, users[0], DASHES).status_code == 200
        assert (list(player.deck), list(player.hand), list(player.draw_queue)) == before


def test_next_match_uses_updated_deck(client, users, now):
    put(client, users[0], CUSTOM)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        old = match(client, ws1, ws2, users)
        assert Counter(old.players[users[0]].deck) == Counter(CUSTOM)
        put(client, users[0], BOMBS)
        now[0] = 310
        receive_until(ws1, "game_result")
        receive_until(ws2, "game_result")
        for ws in (ws1, ws2):
            ws.send_json({"type": "room_leave"})
            assert receive_until(ws, "room_left")["room_id"] == str(old.room_id)
        now[0] = 0
        assert join(ws1)["type"] == "queue_waiting"
        assert join(ws2)["type"] == "queue_waiting"
        receive_until(ws1, "game_ready")
        new = get_player_room(users[0])
        assert new is not old
        assert new.players[users[0]].deck == BOMBS
        assert Counter(new.players[users[1]].deck) == Counter(DEFAULT_DECK)
        assert Counter(old.players[users[0]].deck) == Counter(CUSTOM)


def test_concurrent_matching_keeps_decks_separate(client, users, monkeypatch):
    saved = {users[0]: CUSTOM, users[1]: BOMBS, users[2]: DASHES, users[3]: ["move_jump"] * 6}
    for uid, deck in saved.items():
        put(client, uid, deck)
    sockets = [client.websocket_connect(WS_URL).__enter__() for _ in range(4)]
    try:
        for ws, uid in zip(sockets, saved):
            auth(ws, uid)
        barrier = threading.Barrier(4)

        def run(ws):
            barrier.wait()
            ws.send_json({"type": "queue_join"})

        threads = [threading.Thread(target=run, args=(ws,)) for ws in sockets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for ws in sockets:
            receive_until(ws, "game_ready")
        rooms = {get_player_room(uid).room_id for uid in saved}
        assert len(rooms) == 2
        for uid, deck in saved.items():
            player = get_player_room(uid).players[uid]
            assert Counter(player.deck) == Counter(deck) == all_cards(player)
        assert matchmaking.decks == {} and get_waiting_players() == []
    finally:
        for ws in sockets:
            ws.__exit__(None, None, None)


def test_custom_deck_card_use_move_and_paint(client, users, now):
    put(client, users[0], DASHES)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = match(client, ws1, ws2, users)
        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        player = room.players[users[0]]
        x, y = player.position
        target = (x + 1 if x == 0 else x - 1, y)
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": target[0], "target_y": target[1]})
        assert receive_until(ws1, "card_used")["card_id"] == "move_dash"
        tiles = receive_until(ws1, "tiles_updated")["tiles"]
        assert tiles == [{"x": target[0], "y": target[1], "previous_owner": None, "new_owner": users[0]}]
        assert player.position == target
        assert player.cost == 4 and player.cards_used == 1
        assert room.tile_counts[users[0]] == 1
        assert all_cards(player) == Counter(DASHES)
