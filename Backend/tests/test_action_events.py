import copy
import json
import threading

import pytest

from app import event_log, websocket
from app.cards import ACTION_EVENTS, CARDS, CardType
from app.database import SessionLocal
from app.game_room import RoomStatus, get_player_room
from app.models import User
from tests.test_game_engine import client, now
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, wait_until

ACTIONS = set(ACTION_EVENTS.values())
HAND = ["move_dash", "attack_slash", "defense_shield"]


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def users(client):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(4)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def collect_until(ws, event):
    messages = []
    while True:
        messages.append(ws.receive_json())
        if messages[-1]["type"] == event:
            return messages


def sync(ws):
    ws.send_json({"type": "room_state"})
    return collect_until(ws, "room_state")[:-1]


def read_log(room):
    event_log.close()
    return [json.loads(line) for line in event_log.log_path(room.room_id).read_text(encoding="utf-8").splitlines()]


def start_game(ws1, ws2, users, now):
    auth(ws1, users[0])
    auth(ws2, users[1])
    join_and_match(ws1, ws2)
    room = get_player_room(users[0])
    now[0] = 10
    assert wait_until(lambda: room.status is RoomStatus.PLAYING)
    sync(ws1)
    sync(ws2)
    with websocket.state_lock:
        for uid in users[:2]:
            room.players[uid].hand = list(HAND)
    return room


def test_action_mapping_covers_all_cards():
    assert set(ACTION_EVENTS) == set(CardType)
    for card in CARDS.values():
        assert card.type in ACTION_EVENTS


@pytest.mark.parametrize("index, action", [(0, "move_requested"), (1, "attack_requested"), (2, "defense_requested")])
def test_card_use_emits_action(client, users, now, index, action):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        player = room.players[users[0]]
        card_id = HAND[index]
        next_card = next(iter(player.draw_queue))
        queue_before = list(player.draw_queue)
        position, tiles, counts = player.position, copy.deepcopy(room.tile_owners), dict(room.tile_counts)

        ws1.send_json({"type": "card_use", "hand_index": index})
        own = collect_until(ws1, action)
        assert own == [
            {"type": "card_used", "user_id": users[0], "card_id": card_id, "hand_index": index},
            {"type": "hand_updated", "hand": player.hand, "cost": 6 - CARDS[card_id].cost},
            {"type": "cost_updated", "cost": 6 - CARDS[card_id].cost, "max_cost": 10},
            {"type": action, "user_id": users[0], "card_id": card_id},
        ]
        other = collect_until(ws2, action)
        assert other == [
            {"type": "card_used", "user_id": users[0], "card_id": card_id, "hand_index": index},
            {"type": action, "user_id": users[0], "card_id": card_id},
        ]
        assert sync(ws1) == [] and sync(ws2) == []

        assert player.cost == 6 - CARDS[card_id].cost
        assert player.cards_used == 1
        assert player.cost_used == CARDS[card_id].cost
        assert player.hand[index] == next_card
        assert list(player.draw_queue) == queue_before[1:] + [card_id]
        assert player.position == position
        assert room.tile_owners == tiles
        assert room.tile_counts == counts
        assert room.players[users[1]].cards_used == 0

    records = read_log(room)
    names = [r["type"] for r in records]
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
    i = names.index("card_used")
    assert names[i:i + 3] == ["card_used", "cost_changed", action]
    assert records[i + 2]["data"] == {"user_id": users[0], "card_id": card_id}
    assert sum(n in ACTIONS for n in names) == 1


def test_action_not_sent_to_other_room(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2, \
            client.websocket_connect(WS_URL) as ws3, client.websocket_connect(WS_URL) as ws4:
        room = start_game(ws1, ws2, users, now)
        auth(ws3, users[2])
        auth(ws4, users[3])
        join_and_match(ws3, ws4)
        other = get_player_room(users[2])
        assert other is not room
        sync(ws3)
        sync(ws4)

        ws1.send_json({"type": "card_use", "hand_index": 1})
        collect_until(ws1, "attack_requested")
        collect_until(ws2, "attack_requested")
        for ws in (ws3, ws4):
            assert not any(m["type"] in ACTIONS | {"card_used"} for m in sync(ws))
    assert not any(r["type"] in ACTIONS for r in read_log(other))


def test_failed_card_use_emits_nothing(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        player = room.players[users[0]]

        ws1.send_json({"type": "card_use", "hand_index": 0})
        assert collect_until(ws1, "error")[-1]["code"] == "game_not_playing"

        now[0] = 10
        assert wait_until(lambda: room.status is RoomStatus.PLAYING)
        with websocket.state_lock:
            player.hand = list(HAND)
            player.cost = 1
        for message, code in [
            ({"type": "card_use", "hand_index": 0}, "insufficient_cost"),
            ({"type": "card_use", "hand_index": 7}, "invalid_hand_index"),
            ({"type": "card_use"}, "invalid_hand_index"),
        ]:
            ws1.send_json(message)
            assert collect_until(ws1, "error")[-1]["code"] == code

        now[0] = 310
        assert wait_until(lambda: room.status is RoomStatus.FINISHED)
        with websocket.state_lock:
            player.cost = 10
        ws1.send_json({"type": "card_use", "hand_index": 0})
        assert collect_until(ws1, "error")[-1]["code"] == "game_not_playing"

        for ws in (ws1, ws2):
            assert not any(m["type"] in ACTIONS | {"card_used"} for m in sync(ws))
        assert player.hand == HAND
        assert player.cards_used == 0
    names = [r["type"] for r in read_log(room)]
    assert not any(n in ACTIONS | {"card_used"} for n in names)


def test_reconnect_does_not_repeat_action(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            room = start_game(ws1, ws2, users, now)
            ws1.send_json({"type": "card_use", "hand_index": 2})
            collect_until(ws1, "defense_requested")
            collect_until(ws2, "defense_requested")

        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            assert not any(m["type"] in ACTIONS for m in collect_until(ws1, "room_state"))
            assert not any(m["type"] in ACTIONS for m in sync(ws1))
        assert not any(m["type"] in ACTIONS for m in sync(ws2))
        assert room.players[users[0]].cards_used == 1

    names = [r["type"] for r in read_log(room)]
    assert names.count("defense_requested") == 1
    assert names.count("card_used") == 1


def test_concurrent_card_use(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room = start_game(ws1, ws2, users, now)
        initial = {uid: sorted(room.players[uid].hand + list(room.players[uid].draw_queue)) for uid in users[:2]}
        barrier = threading.Barrier(2)

        def spam(ws):
            barrier.wait()
            for i in range(4):
                ws.send_json({"type": "card_use", "hand_index": i % 3})

        threads = [threading.Thread(target=spam, args=(ws,)) for ws in (ws1, ws2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        seen = {ws: [] for ws in (ws1, ws2)}
        for ws in (ws1, ws2):
            seen[ws] = sync(ws)
            while sum(m["type"] in ("hand_updated", "error") for m in seen[ws]) < 4:
                seen[ws] += sync(ws)

        for uid, ws in zip(users[:2], (ws1, ws2)):
            player = room.players[uid]
            own_actions = [m for m in seen[ws] if m["type"] in ACTIONS and m["user_id"] == uid]
            assert len(own_actions) == player.cards_used
            assert player.cost == 6 - player.cost_used >= 0
            assert sorted(player.hand + list(player.draw_queue)) == initial[uid]
            assert len(player.hand) == 3 and len(player.draw_queue) == 3

    records = read_log(room)
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
    for uid in users[:2]:
        used = [r for r in records if r["type"] == "card_used" and r["data"]["user_id"] == uid]
        actions = [r for r in records if r["type"] in ACTIONS and r["data"]["user_id"] == uid]
        assert len(used) == len(actions) == room.players[uid].cards_used
        for u, a in zip(used, actions):
            assert a["type"] == ACTION_EVENTS[CARDS[u["data"]["card_id"]].type]
            assert a["data"]["card_id"] == u["data"]["card_id"]
            between = [r["type"] for r in records if u["sequence"] < r["sequence"] < a["sequence"]]
            assert between in ([], ["cost_changed"])
