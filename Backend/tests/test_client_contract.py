import json
import os
from collections import Counter
from pathlib import Path

import pytest

from app import websocket
from app.cards import CARDS, DEFAULT_DECK
from app.characters import CHARACTERS
from app.game_room import MAP_SIZE, MAX_COST, SPAWN_POINTS, RoomStatus, get_player_room
from tests.test_action_events import collect_until, sync, users
from tests.test_game_engine import client, now, receive_until
from tests.test_user_deck import headers, join
from tests.test_websocket import WS_URL, auth, clear_state, wait_until

CLIENT_HANDLED = {
    "auth_success", "queue_waiting", "queue_cancelled", "match_found", "room_state", "game_tick",
    "cost_updated", "hand_updated", "game_finished", "game_result", "room_left", "session_replaced", "error",
    "card_used", "move_requested", "attack_requested", "game_ready", "game_started", "deathmatch_started",
    "player_disconnected", "player_reconnected", "ultimate_activated", "attack_resolved",
}
CLIENT_UNHANDLED = {"player_moved", "tiles_updated"}
PHASES = {"READY", "PLAYING", "DEATHMATCH", "FINISHED"}


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def is_int(value, low=0, high=2**53 - 1):
    return type(value) is int and low <= value <= high


def check_room_state(message, self_id):
    assert set(message) >= {
        "room_id", "status", "map_id", "map", "players", "my_cost", "my_hand", "my_next_card",
        "my_ultimate_used", "tile_owners", "started_at", "ready_remaining_seconds", "remaining_seconds", "max_cost",
    }
    assert message["status"] in PHASES
    tiles = message["map"]["tiles"]
    assert message["map"]["width"] == message["map"]["height"] == MAP_SIZE == len(tiles)
    assert all(len(row) == MAP_SIZE and set(row) <= {"NORMAL", "GLASS", "BLOCKED"} for row in tiles)
    assert message["max_cost"] == MAX_COST and is_int(message["my_cost"], 0, MAX_COST)
    assert len(message["my_hand"]) == 3 and all(card in CARDS for card in message["my_hand"])
    assert message["my_next_card"] is None or message["my_next_card"] in CARDS
    assert type(message["my_ultimate_used"]) is bool
    assert is_int(message["remaining_seconds"], 0, 300) and is_int(message["ready_remaining_seconds"], 0, 10)
    players = message["players"]
    assert 2 <= len(players) <= 4
    ids = [p["user_id"] for p in players]
    assert self_id in ids and len(set(ids)) == len(ids)
    assert len({p["color"] for p in players}) == len(players)
    assert len({tuple(p["spawn"]) for p in players}) == len(players)
    owners = message["tile_owners"]
    assert len(owners) == MAP_SIZE and all(len(row) == MAP_SIZE for row in owners)
    assert all(owner is None or owner in ids for row in owners for owner in row)
    for p in players:
        assert set(p) == {
            "user_id", "color", "character_id", "character_name", "ultimate_id", "ultimate_name",
            "spawn", "position", "alive", "connected", "tile_count",
        }
        assert p["color"] in {"RED", "BLUE", "GREEN", "YELLOW"} and p["character_id"] in CHARACTERS
        assert tuple(p["spawn"]) in SPAWN_POINTS
        x, y = p["position"]
        assert is_int(x, 0, MAP_SIZE - 1) and is_int(y, 0, MAP_SIZE - 1)
        assert tiles[y][x] != "BLOCKED"
        assert type(p["alive"]) is bool and type(p["connected"]) is bool
        assert p["tile_count"] == sum(row.count(p["user_id"]) for row in owners)


def check_message(message, self_id, room_ids):
    kind = message["type"]
    assert kind in CLIENT_HANDLED | CLIENT_UNHANDLED, kind
    if kind == "room_state":
        check_room_state(message, self_id)
    elif kind == "error":
        assert set(message) == {"type", "code", "message"}
    elif kind == "game_tick":
        assert message["status"] in PHASES - {"FINISHED"}
        assert is_int(message["remaining_seconds"], 0, 10 if message["status"] == "READY" else 300)
    elif kind in ("cost_updated", "hand_updated"):
        assert is_int(message["cost"], 0, MAX_COST)
        if kind == "hand_updated":
            assert len(message["hand"]) == 3
        else:
            assert message["max_cost"] == MAX_COST
    elif kind == "player_moved":
        assert [message["x"], message["y"]] == message["path"][-1] and len(message["path"]) >= 2
    elif kind == "attack_resolved":
        assert message["room_id"] in room_ids and message["card_id"] in CARDS
        assert set(message) == {"type", "room_id", "user_id", "card_id", "target_x", "target_y", "affected_tiles"}
    elif kind == "tiles_updated":
        assert message["room_id"] in room_ids and message["tiles"]
        assert all(set(t) == {"x", "y", "previous_owner", "new_owner"} for t in message["tiles"])
    elif kind == "game_result":
        assert message["room_id"] in room_ids and is_int(message["winner_id"], 1)
        ranks = sorted(r["rank"] for r in message["results"])
        assert ranks == list(range(1, len(ranks) + 1))
        for r in message["results"]:
            assert set(r) == {"user_id", "rank", "is_winner", "painted_tiles", "total_painted", "kills", "deaths", "cards_used", "cost_used"}
            assert r["is_winner"] == (r["user_id"] == message["winner_id"]) == (r["rank"] == 1)


def test_full_session_matches_client_contract(client, users, now):
    captured = {"self_id": users[0], "sessions": []}
    messages = []
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        messages.append(auth(ws1, users[0]))
        auth(ws2, users[1])
        ws1.send_json({"type": "room_state"})
        messages.append(ws1.receive_json())
        assert messages[-1]["code"] == "not_in_room"
        ws1.send_json({"type": "queue_join", "character_id": "character_01"})
        messages.append(ws1.receive_json())
        join(ws2, character_id="character_02")
        messages += collect_until(ws1, "game_ready")
        messages += sync(ws1)
        ws1.send_json({"type": "room_state"})
        messages.append(receive_until(ws1, "room_state"))
        room = get_player_room(users[0])
        a = room.players[users[0]]

        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        with websocket.state_lock:
            a.hand = ["attack_slash", "move_dash", "move_jump"]
            a.position = (5, 5)
        messages += sync(ws1)
        ws1.send_json({"type": "room_state"})
        messages.append(receive_until(ws1, "room_state"))

        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 4})
        messages += collect_until(ws1, "tiles_updated")
        ws1.send_json({"type": "card_use", "hand_index": 1, "target_x": 5, "target_y": 6})
        messages += collect_until(ws1, "tiles_updated")
        ws1.send_json({"type": "room_state"})
        state = receive_until(ws1, "room_state")
        messages.append(state)
        me = next(p for p in state["players"] if p["user_id"] == users[0])
        assert me["position"] == [5, 6] and state["tile_owners"][6][5] == users[0] and me["tile_count"] == 2
        assert state["tile_owners"][4][5] == users[0]
        assert state["my_hand"] == a.hand and state["my_cost"] == a.cost == 6 - 3 - 2

        ws1.send_json({"type": "ultimate_use"})
        messages += collect_until(ws1, "ultimate_activated")
        ws1.send_json({"type": "room_state"})
        messages.append(receive_until(ws1, "room_state"))
        assert messages[-1]["my_ultimate_used"] is True

        ws1.send_json({"type": "card_use", "hand_index": 9})
        messages.append(receive_until(ws1, "error"))
        ws1.send_json({"type": "card_use", "hand_index": 0, "target_x": 99, "target_y": 0})
        messages.append(receive_until(ws1, "error"))

        now[0] = 310
        messages += collect_until(ws1, "game_result")
        ws2_result = receive_until(ws2, "game_result")
        ws1.send_json({"type": "room_state"})
        final = receive_until(ws1, "room_state")
        messages.append(final)
        assert final["status"] == "FINISHED" and final["remaining_seconds"] == 0
        result = next(m for m in messages if m["type"] == "game_result")
        assert result == ws2_result
        by_user = {r["user_id"]: r for r in result["results"]}
        for p in final["players"]:
            assert by_user[p["user_id"]]["painted_tiles"] == p["tile_count"]
        assert by_user[users[0]]["cards_used"] == 2 and by_user[users[0]]["cost_used"] == 5
        captured["sessions"].append(messages)

        ws1.__exit__(None, None, None)
        assert wait_until(lambda: not room.players[users[0]].connected)
        with client.websocket_connect(WS_URL) as again:
            reconnect = [auth(again, users[0]), again.receive_json(), again.receive_json()]
            assert [m["type"] for m in reconnect] == ["auth_success", "room_state", "game_result"]
            assert reconnect[1]["tile_owners"] == final["tile_owners"]
            assert reconnect[2] == result
            again.send_json({"type": "room_leave"})
            reconnect.append(receive_until(again, "room_left"))
            captured["sessions"].append(reconnect)

    room_ids = {str(room.room_id)}
    for session in captured["sessions"]:
        for message in session:
            check_message(message, users[0], room_ids)
    sent = {m["type"] for session in captured["sessions"] for m in session}
    assert sent & CLIENT_UNHANDLED == CLIENT_UNHANDLED
    dump = os.environ.get("PAINT_RUSH_CONTRACT_DUMP")
    if dump:
        Path(dump).write_text(json.dumps(captured, ensure_ascii=False), encoding="utf-8")


def test_hand_index_only_card_use_like_client(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1, character_id="character_01")
        join(ws2, character_id="character_01")
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        a = room.players[users[0]]
        with websocket.state_lock:
            a.hand = ["move_dash", "attack_slash", "move_jump"]
        position = a.position
        ws1.send_json({"type": "card_use", "hand_index": 0})
        received = collect_until(ws1, "move_requested")
        ws1.send_json({"type": "room_state"})
        received += collect_until(ws1, "room_state")
        assert not {m["type"] for m in received} & CLIENT_UNHANDLED
        assert a.position == position and a.cards_used == 1


def test_client_supplied_state_ignored(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1, character_id="character_01", deck=["attack_bomb"] * 6, position=[9, 9], cost=10)
        join(ws2)
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        a = room.players[users[0]]
        assert a.position == a.spawn and a.cost == 6
        assert Counter(a.deck) == Counter(DEFAULT_DECK)
        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        with websocket.state_lock:
            a.hand[0] = "move_dash"
        card_id = a.hand[0]
        ws1.send_json({
            "type": "card_use", "hand_index": 0, "card_id": "attack_bomb", "cost": 0,
            "position": [9, 9], "tile_owners": [[users[0]] * 20] * 20,
        })
        receive_until(ws1, "hand_updated")
        ws1.send_json({"type": "room_state"})
        state = receive_until(ws1, "room_state")
        me = next(p for p in state["players"] if p["user_id"] == users[0])
        assert me["position"] == list(a.spawn)
        assert state["my_cost"] == 6 - CARDS[card_id].cost
        assert sum(owner == users[0] for row in state["tile_owners"] for owner in row) == 0


def test_room_state_hides_private_data(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1)
        join(ws2)
        receive_until(ws1, "game_ready")
        room = get_player_room(users[0])
        b = room.players[users[1]]
        with websocket.state_lock:
            b.hand[0] = "secret_opponent_card"
        ws1.send_json({"type": "room_state"})
        text = json.dumps(receive_until(ws1, "room_state"))
        for leaked in ("secret_opponent_card", "draw_queue", "deck", "password", "cost_mark"):
            assert leaked not in text
        for key in ("cost", "hand", "cards_used", "cost_used"):
            assert f'"{key}"' not in text


def test_rest_contract_for_client(client, users):
    r = client.post("/api/v1/auth/register", json={"username": "Contract1", "password": "password1234"})
    assert r.status_code == 201 and set(r.json()) == {"id", "username"}
    assert client.post("/api/v1/auth/register", json={"username": "contract1", "password": "password1234"}).status_code == 409
    assert client.post("/api/v1/auth/register", json={"username": "x", "password": "password1234"}).status_code == 422
    token = client.post("/api/v1/auth/login", json={"username": "contract1", "password": "password1234"})
    assert token.status_code == 200 and set(token.json()) == {"access_token", "token_type"}
    assert token.json()["token_type"] == "bearer"
    assert client.post("/api/v1/auth/login", json={"username": "contract1", "password": "wrongpass1"}).status_code == 401

    characters = client.get("/characters").json()["characters"]
    assert all(set(c) == {"character_id", "name", "ultimate_id", "ultimate_name"} for c in characters)
    cards = client.get("/cards").json()
    assert cards["deck_size"] == 6 and all(set(c) == {"card_id", "type", "cost"} for c in cards["cards"])

    h = headers(users[0])
    assert set(client.get("/users/me/deck", headers=h).json()) == {"cards"}
    assert client.put("/users/me/deck", json={"cards": ["move_dash"] * 6}, headers=h).status_code == 200
    profile = client.get("/users/me/profile", headers=h).json()
    assert set(profile) == {"user_id", "username", "display_name", "preferred_character_id"}
    assert client.patch("/users/me/profile", json={"display_name": "Tester"}, headers=h).status_code == 200
    assert client.get("/users/me/level", headers=h).json() == {"level": 1, "total_experience": 0}
    for method, path in [("GET", "/users/me/deck"), ("PUT", "/users/me/deck"), ("GET", "/users/me/profile"),
                         ("PATCH", "/users/me/profile"), ("GET", "/users/me/level")]:
        r = client.request(method, path, json={})
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
