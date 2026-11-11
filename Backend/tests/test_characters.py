import copy
import json
import threading

import pytest

from app import event_log, matchmaking, websocket
from app.characters import CHARACTERS, Character, character_info, get_character, list_characters
from app.database import SessionLocal
from app.game_engine import advance, start, use_ultimate
from app.game_room import DEFAULT_CHARACTER_ID, RoomStatus, create_room, get_player_room
from app.matchmaking import cancel_queue, get_waiting_players, join_queue, try_match
from app.models import User
from tests.test_game_engine import client, now
from tests.test_websocket import WS_URL, auth, clear_state, wait_until
from tests.test_action_events import card_message


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
    return collect_until(ws, "room_state")


def read_log(room):
    event_log.close()
    return [json.loads(line) for line in event_log.log_path(room.room_id).read_text(encoding="utf-8").splitlines()]


def join(ws, character_id=None):
    message = {"type": "queue_join"}
    if character_id is not None:
        message["character_id"] = character_id
    ws.send_json(message)
    return ws.receive_json()


def match(ws1, ws2, users, characters=(None, None)):
    auth(ws1, users[0])
    auth(ws2, users[1])
    assert join(ws1, characters[0])["type"] == "queue_waiting"
    assert join(ws2, characters[1])["type"] == "queue_waiting"
    found = collect_until(ws1, "match_found")[-1]
    collect_until(ws2, "match_found")
    collect_until(ws1, "game_ready")
    collect_until(ws2, "game_ready")
    return get_player_room(users[0]), found


def start_playing(room, now, *sockets):
    now[0] = 10
    assert wait_until(lambda: room.status is RoomStatus.PLAYING)
    for ws in sockets:
        sync(ws)


def playing_room(t=10, user_ids=(1, 2), character_ids=None):
    room = create_room(list(user_ids), character_ids=character_ids)
    start(room, 0)
    advance(room, t)
    return room


def snapshot(room, user_id):
    player = room.players[user_id]
    return (
        list(player.hand), list(player.draw_queue), player.cost, player.cards_used, player.cost_used,
        player.position, player.character_id, copy.deepcopy(room.tile_owners), dict(room.tile_counts),
    )


def test_character_list_api(client):
    response = client.get("/characters")
    assert response.status_code == 200
    body = response.json()
    assert body == {"characters": [
        {"character_id": "character_01", "name": "테스트 캐릭터 1", "ultimate_id": "ultimate_01", "ultimate_name": "테스트 궁극기 1"},
        {"character_id": "character_02", "name": "테스트 캐릭터 2", "ultimate_id": "ultimate_02", "ultimate_name": "테스트 궁극기 2"},
    ]}
    assert body["characters"] == list_characters()
    text = response.text.lower()
    assert "password" not in text and "token" not in text


def test_character_list_api_reflects_registry(client, monkeypatch):
    monkeypatch.setitem(CHARACTERS, "character_03", Character("character_03", "추가 캐릭터", "ultimate_03", "추가 궁극기"))
    ids = [c["character_id"] for c in client.get("/characters").json()["characters"]]
    assert ids == ["character_01", "character_02", "character_03"]


def test_character_data():
    assert len(CHARACTERS) == 2
    assert DEFAULT_CHARACTER_ID in CHARACTERS
    ultimates = [c.ultimate_id for c in CHARACTERS.values()]
    assert len(set(ultimates)) == len(ultimates)
    for character_id, character in CHARACTERS.items():
        assert isinstance(character, Character)
        assert character.character_id == character_id
        assert set(character._asdict()) == {"character_id", "name", "ultimate_id", "ultimate_name"}
        assert all(isinstance(v, str) and v for v in character)
        assert get_character(character_id) is character
    for bad in ("ghost", "", None, 1, ["character_01"], {"id": 1}):
        assert get_character(bad) is None
    assert character_info("character_02") == {
        "character_id": "character_02", "character_name": "테스트 캐릭터 2",
        "ultimate_id": "ultimate_02", "ultimate_name": "테스트 궁극기 2",
    }
    assert character_info("ghost") == {"character_id": "ghost", "character_name": None, "ultimate_id": None, "ultimate_name": None}


def test_select_character_on_join(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        assert join(ws, "character_02")["type"] == "queue_waiting"
        assert get_waiting_players() == [users[0]]
        assert matchmaking.selections == {users[0]: "character_02"}


def test_default_character_when_omitted(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        assert join(ws)["type"] == "queue_waiting"
        assert matchmaking.selections == {users[0]: DEFAULT_CHARACTER_ID}


@pytest.mark.parametrize("character_id", ["ghost", "", None, 1, ["character_01"], {"id": "character_01"}])
def test_reject_invalid_character(client, users, character_id):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        ws.send_json({"type": "queue_join", "character_id": character_id})
        reply = ws.receive_json()
        assert reply["code"] == "unknown_character"
        assert set(reply) == {"type", "code", "message"}
        assert get_waiting_players() == []
        assert matchmaking.selections == {}
        assert join(ws, "character_01")["type"] == "queue_waiting"


def test_join_queue_rejects_invalid_character():
    with pytest.raises(ValueError, match="unknown_character"):
        join_queue(1, "ghost")
    assert get_waiting_players() == []
    assert matchmaking.selections == {}


def test_match_applies_character(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room, found = match(ws1, ws2, users, ("character_02", "character_01"))
        assert room.players[users[0]].character_id == "character_02"
        assert room.players[users[1]].character_id == "character_01"
        assert [p["character_id"] for p in found["players"]] == ["character_02", "character_01"]
        assert found["players"][0] | character_info("character_02") == found["players"][0]
        assert found["players"][1] | character_info("character_01") == found["players"][1]
        assert matchmaking.selections == {}

        for ws in (ws1, ws2):
            state = sync(ws)[-1]
            assert state["my_ultimate_used"] is False
            for p in state["players"]:
                assert p | character_info(room.players[p["user_id"]].character_id) == p


@pytest.mark.parametrize("size", [2, 3, 4])
def test_selections_independent(monkeypatch, size):
    monkeypatch.setattr(matchmaking, "MATCH_SIZE", size)
    chosen = {uid: ("character_01", "character_02")[uid % 2] for uid in range(1, size + 1)}
    for uid, character_id in chosen.items():
        join_queue(uid, character_id)
    room = try_match()
    assert {uid: p.character_id for uid, p in room.players.items()} == chosen
    assert all(p.ultimate_used is False for p in room.players.values())
    assert matchmaking.selections == {}


def test_same_character_allowed(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room, found = match(ws1, ws2, users, ("character_02", "character_02"))
        assert [p.character_id for p in room.players.values()] == ["character_02", "character_02"]
        assert [p["ultimate_id"] for p in found["players"]] == ["ultimate_02", "ultimate_02"]


def test_cancel_clears_selection(client, users):
    join_queue(1, "character_02")
    assert cancel_queue(1) is True
    assert matchmaking.selections == {}
    join_queue(1)
    join_queue(2, "character_02")
    room = try_match()
    assert room.players[1].character_id == DEFAULT_CHARACTER_ID
    assert room.players[2].character_id == "character_02"

    clear_state()
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        join(ws, "character_02")
        ws.send_json({"type": "queue_cancel"})
        assert ws.receive_json() == {"type": "queue_cancelled"}
        assert matchmaking.selections == {}
        join(ws, "character_02")
    assert wait_until(lambda: matchmaking.selections == {})
    assert get_waiting_players() == []


def test_selection_kept_when_match_fails():
    join_queue(1, "character_02")
    join_queue(2, "character_01")
    create_room([2, 3])
    with pytest.raises(ValueError):
        try_match()
    assert get_waiting_players() == [1, 2]
    assert matchmaking.selections == {1: "character_02", 2: "character_01"}


def test_duplicate_join_keeps_selection_and_order(client, users):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join(ws1, "character_01")
        assert join(ws1, "character_02")["code"] == "already_in_queue"
        assert join(ws1, "ghost")["code"] == "already_in_queue"
        assert get_waiting_players() == [users[0]]
        assert matchmaking.selections == {users[0]: "character_01"}

        join(ws2, "character_02")
        collect_until(ws1, "game_ready")
        room = get_player_room(users[0])
        assert room.player_order == users[:2]
        assert room.players[users[0]].character_id == "character_01"
        assert join(ws1, "character_02")["code"] == "already_in_game"
        assert room.players[users[0]].character_id == "character_01"
        assert matchmaking.selections == {}


def test_reconnect_keeps_character(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            room, _ = match(ws1, ws2, users, ("character_02", "character_01"))
        assert wait_until(lambda: not room.players[users[0]].connected)
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            state = collect_until(ws1, "room_state")[-1]
            me = next(p for p in state["players"] if p["user_id"] == users[0])
            assert me | character_info("character_02") == me
            assert state["my_ultimate_used"] is False
        assert room.players[users[0]].character_id == "character_02"
        assert room.players[users[1]].character_id == "character_01"


def test_use_ultimate_engine():
    room = playing_room(character_ids={1: "character_02"})
    before = snapshot(room, 1)
    character = use_ultimate(room, 1, 10)
    assert character is CHARACTERS["character_02"]
    assert room.players[1].ultimate_used is True
    assert room.players[2].ultimate_used is False
    assert snapshot(room, 1) == before
    with pytest.raises(ValueError, match="ultimate_already_used"):
        use_ultimate(room, 1, 11)
    assert use_ultimate(room, 2, 11) is CHARACTERS[DEFAULT_CHARACTER_ID]


@pytest.mark.parametrize("t, status", [(5, RoomStatus.READY), (310, RoomStatus.FINISHED)])
def test_ultimate_rejected_when_not_playing(t, status):
    room = playing_room(t)
    assert room.status is status
    with pytest.raises(ValueError, match="game_not_playing"):
        use_ultimate(room, 1, t)
    assert room.players[1].ultimate_used is False


def test_ultimate_rejected_after_time_up_before_tick():
    room = playing_room(309)
    assert room.status is RoomStatus.DEATHMATCH
    assert use_ultimate(room, 2, 309)
    with pytest.raises(ValueError, match="game_not_playing"):
        use_ultimate(room, 1, 310)
    assert room.players[1].ultimate_used is False


def test_ultimate_rejected_when_dead():
    room = playing_room()
    room.players[1].alive = False
    with pytest.raises(ValueError, match="player_dead"):
        use_ultimate(room, 1, 10)
    assert room.players[1].ultimate_used is False


def test_ultimate_rejected_for_unknown_character():
    room = playing_room(character_ids={1: "ghost"})
    with pytest.raises(ValueError, match="unknown_character"):
        use_ultimate(room, 1, 10)
    assert room.players[1].ultimate_used is False


def test_concurrent_ultimate_once():
    room = playing_room()
    results = []
    barrier = threading.Barrier(8)

    def run():
        barrier.wait()
        with websocket.state_lock:
            try:
                results.append(use_ultimate(room, 1, 10).ultimate_id)
            except ValueError as e:
                results.append(str(e))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ultimate_01") == 1
    assert results.count("ultimate_already_used") == 7


def test_ultimate_over_websocket(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room, _ = match(ws1, ws2, users, ("character_02", "character_01"))
        ws1.send_json({"type": "ultimate_use"})
        reply = collect_until(ws1, "error")[-1]
        assert reply == {"type": "error", "code": "game_not_playing", "message": websocket.ULTIMATE_ERRORS["game_not_playing"]}

        start_playing(room, now, ws1, ws2)
        before = snapshot(room, users[0]), snapshot(room, users[1])
        ws1.send_json({"type": "ultimate_use", "ultimate_id": "ultimate_01", "character_id": "character_01"})
        activated = {"type": "ultimate_activated", "user_id": users[0], "character_id": "character_02", "ultimate_id": "ultimate_02"}
        assert ws1.receive_json() == activated
        assert collect_until(ws2, "ultimate_activated") == [activated]
        assert room.players[users[0]].ultimate_used is True
        assert room.players[users[1]].ultimate_used is False
        assert (snapshot(room, users[0]), snapshot(room, users[1])) == before

        ws1.send_json({"type": "ultimate_use"})
        assert collect_until(ws1, "error")[-1]["code"] == "ultimate_already_used"
        assert not any(m["type"] == "ultimate_activated" for m in sync(ws2)[:-1])
        assert sync(ws1)[-1]["my_ultimate_used"] is True
        assert sync(ws2)[-1]["my_ultimate_used"] is False

        with websocket.state_lock:
            message = card_message(room, users[0], 0)
        ws1.send_json(message)
        assert collect_until(ws1, "hand_updated")[0]["type"] == "card_used"
        assert room.players[users[0]].cards_used == 1

        with websocket.state_lock:
            room.players[users[1]].alive = False
        ws2.send_json({"type": "ultimate_use"})
        assert collect_until(ws2, "error")[-1]["code"] == "player_dead"

        now[0] = 310
        assert wait_until(lambda: room.status is RoomStatus.FINISHED)
        with websocket.state_lock:
            room.players[users[1]].alive = True
        ws2.send_json({"type": "ultimate_use"})
        assert collect_until(ws2, "error")[-1]["code"] == "game_not_playing"
        assert room.players[users[1]].ultimate_used is False


def test_ultimate_without_room(client, users):
    with client.websocket_connect(WS_URL) as ws:
        auth(ws, users[0])
        ws.send_json({"type": "ultimate_use"})
        assert ws.receive_json()["code"] == "not_in_room"


def test_repeated_ultimate_over_websocket(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room, _ = match(ws1, ws2, users)
        start_playing(room, now, ws1, ws2)
        for _ in range(4):
            ws1.send_json({"type": "ultimate_use"})
        messages = []
        while sum(m["type"] in ("ultimate_activated", "error") for m in messages) < 4:
            messages.append(ws1.receive_json())
        assert sum(m["type"] == "ultimate_activated" for m in messages) == 1
        assert [m["code"] for m in messages if m["type"] == "error"] == ["ultimate_already_used"] * 3
    names = [r["type"] for r in read_log(room)]
    assert names.count("ultimate_activated") == 1


def test_reconnect_keeps_ultimate_state(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            room, _ = match(ws1, ws2, users, ("character_02", "character_01"))
            start_playing(room, now, ws1, ws2)
            ws1.send_json({"type": "ultimate_use"})
            collect_until(ws1, "ultimate_activated")
            collect_until(ws2, "ultimate_activated")
        assert wait_until(lambda: not room.players[users[0]].connected)

        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            messages = collect_until(ws1, "room_state")
            assert not any(m["type"] == "ultimate_activated" for m in messages)
            assert messages[-1]["my_ultimate_used"] is True
            ws1.send_json({"type": "ultimate_use"})
            assert collect_until(ws1, "error")[-1]["code"] == "ultimate_already_used"
        assert room.players[users[0]].ultimate_used is True
        assert room.players[users[0]].character_id == "character_02"

    names = [r["type"] for r in read_log(room)]
    assert names.count("ultimate_activated") == 1
    assert names.count("character_selected") == 2


def test_rematch_resets_ultimate_and_allows_new_character(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2, \
            client.websocket_connect(WS_URL) as ws3:
        old, _ = match(ws1, ws2, users, ("character_01", "character_02"))
        auth(ws3, users[2])
        start_playing(old, now, ws1, ws2)
        ws1.send_json({"type": "ultimate_use"})
        collect_until(ws1, "ultimate_activated")
        now[0] = 310
        collect_until(ws1, "game_result")
        collect_until(ws2, "game_result")
        assert wait_until(lambda: old.result_saved)
        ws1.send_json({"type": "room_leave"})
        collect_until(ws1, "room_left")

        assert join(ws1, "character_02")["type"] == "queue_waiting"
        assert join(ws3, "character_02")["type"] == "queue_waiting"
        found = collect_until(ws1, "match_found")[-1]
        collect_until(ws1, "game_ready")
        new = get_player_room(users[0])
        assert new is not old
        assert new.players[users[0]].character_id == "character_02"
        assert new.players[users[0]].ultimate_used is False
        assert found["players"][0]["ultimate_id"] == "ultimate_02"
        assert old.players[users[0]].ultimate_used is True
        assert old.players[users[0]].character_id == "character_01"
        assert old.players[users[1]].ultimate_used is False
        assert sync(ws1)[-1]["my_ultimate_used"] is False

        now[0] = 330
        assert wait_until(lambda: new.status is RoomStatus.PLAYING)
        ws1.send_json({"type": "ultimate_use"})
        assert collect_until(ws1, "ultimate_activated")[-1]["ultimate_id"] == "ultimate_02"
    records = read_log(new)
    selected = [r["data"] for r in records if r["type"] == "character_selected"]
    assert selected == [
        {"user_id": users[0], "character_id": "character_02", "ultimate_id": "ultimate_02"},
        {"user_id": users[2], "character_id": "character_02", "ultimate_id": "ultimate_02"},
    ]


def test_event_log_records(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        room, _ = match(ws1, ws2, users, ("character_02", "character_01"))
        ws1.send_json({"type": "ultimate_use"})
        collect_until(ws1, "error")
        start_playing(room, now, ws1, ws2)
        ws2.send_json({"type": "ultimate_use"})
        collect_until(ws2, "ultimate_activated")
        ws2.send_json({"type": "ultimate_use"})
        collect_until(ws2, "error")
        sync(ws1)
        sync(ws2)

    records = read_log(room)
    names = [r["type"] for r in records]
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
    assert names[:4] == ["match_created", "game_ready", "character_selected", "character_selected"]
    assert [r["data"] for r in records if r["type"] == "character_selected"] == [
        {"user_id": users[0], "character_id": "character_02", "ultimate_id": "ultimate_02"},
        {"user_id": users[1], "character_id": "character_01", "ultimate_id": "ultimate_01"},
    ]
    assert [r["data"] for r in records if r["type"] == "ultimate_activated"] == [
        {"user_id": users[1], "character_id": "character_01", "ultimate_id": "ultimate_01"},
    ]
    assert names.index("ultimate_activated") > names.index("game_started")
    players = records[0]["data"]["players"]
    assert [p["character_id"] for p in players] == ["character_02", "character_01"]


def test_other_room_unaffected(client, users, now):
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2, \
            client.websocket_connect(WS_URL) as ws3, client.websocket_connect(WS_URL) as ws4:
        room, _ = match(ws1, ws2, users, ("character_02", "character_02"))
        other, _ = match(ws3, ws4, users[2:], ("character_01", "character_02"))
        assert other is not room
        start_playing(room, now, ws1, ws2)
        assert wait_until(lambda: other.status is RoomStatus.PLAYING)
        sync(ws3)
        sync(ws4)
        before = [snapshot(other, uid) for uid in users[2:]]

        ws1.send_json({"type": "ultimate_use"})
        collect_until(ws1, "ultimate_activated")
        collect_until(ws2, "ultimate_activated")
        for ws in (ws3, ws4):
            messages = sync(ws)
            assert not any(m["type"] == "ultimate_activated" for m in messages)
            assert messages[-1]["my_ultimate_used"] is False
        assert [snapshot(other, uid) for uid in users[2:]] == before
        assert all(p.ultimate_used is False for p in other.players.values())
        assert [other.players[uid].character_id for uid in users[2:]] == ["character_01", "character_02"]

        ws3.send_json({"type": "ultimate_use"})
        assert collect_until(ws3, "ultimate_activated")[-1]["ultimate_id"] == "ultimate_01"
        assert room.players[users[1]].ultimate_used is False
    assert not any(r["type"] == "ultimate_activated" and r["data"]["user_id"] in users[:2] for r in read_log(other))
    assert sum(r["type"] == "ultimate_activated" for r in read_log(room)) == 1
