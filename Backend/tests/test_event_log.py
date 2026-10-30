import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta

import pytest

from app import event_log, websocket
from app.game_engine import advance, start
from app.game_room import create_room, get_player_room
from app.maps import create_default_map
from app.painting import paint_tile
from tests.test_game_engine import client, now, receive_until
from tests.test_room_leave import users
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, make_token, wait_until


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def read_log(room_id):
    event_log.close()
    path = event_log.log_path(room_id)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def types(records):
    return [r["type"] for r in records]


def test_log_format_and_sequence():
    a, b = uuid.uuid4(), uuid.uuid4()
    event_log.log_event(a, "match_created", {"map_id": "default"})
    event_log.log_event(b, "match_created", {"map_id": "other"})
    event_log.log_event(a, "game_ready", {"remaining_seconds": 10})
    event_log.log_event(a, "game_finished")

    records = read_log(a)
    assert event_log.log_path(a).name == f"{a}.jsonl"
    assert event_log.log_path(a).parent == event_log.LOG_DIR
    assert [r["sequence"] for r in records] == [1, 2, 3]
    assert types(records) == ["match_created", "game_ready", "game_finished"]
    for r in records:
        assert set(r) == {"timestamp", "room_id", "sequence", "type", "data"}
        assert r["room_id"] == str(a)
        assert r["timestamp"].endswith("Z")
        assert datetime.fromisoformat(r["timestamp"]).utcoffset() == timedelta(0)
    assert records[0]["data"] == {"map_id": "default"}
    assert records[2]["data"] == {}

    other = read_log(b)
    assert [(r["sequence"], r["data"]["map_id"]) for r in other] == [(1, "other")]


def test_utf8_and_snapshot():
    room_id = uuid.uuid4()
    data = {"name": "맵", "players": [1, 2]}
    event_log.log_event(room_id, "match_created", data)
    data["players"].append(3)
    data["name"] = "changed"
    assert read_log(room_id)[0]["data"] == {"name": "맵", "players": [1, 2]}
    assert "맵" in event_log.log_path(room_id).read_text(encoding="utf-8")


def test_append_to_existing_file():
    room_id = uuid.uuid4()
    path = event_log.log_path(room_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"old": true}\n', encoding="utf-8")
    event_log.log_event(room_id, "game_ready")
    assert event_log.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"old": true}'
    assert json.loads(lines[1])["type"] == "game_ready"


def test_concurrent_events_same_room():
    room_id = uuid.uuid4()
    barrier = threading.Barrier(8)

    def run(worker):
        barrier.wait()
        for i in range(50):
            event_log.log_event(room_id, "cost_changed", {"worker": worker, "i": i})

    threads = [threading.Thread(target=run, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = read_log(room_id)
    assert [r["sequence"] for r in records] == list(range(1, 401))
    for worker in range(8):
        assert [r["data"]["i"] for r in records if r["data"]["worker"] == worker] == list(range(50))


def test_write_failure_isolated(caplog):
    bad, good = uuid.uuid4(), uuid.uuid4()
    event_log.log_path(bad).mkdir(parents=True)
    with caplog.at_level(logging.ERROR, logger="app.event_log"):
        event_log.log_event(bad, "game_ready")
        event_log.log_event(good, "game_ready")
        event_log.log_event(good, "game_started")
        assert event_log.close()
    assert "event log write failed" in caplog.text
    assert types(read_log(good)) == ["game_ready", "game_started"]


def test_close_flushes_and_does_not_hang():
    room_id = uuid.uuid4()
    for i in range(200):
        event_log.log_event(room_id, "cost_changed", {"i": i})
    assert event_log.close()
    assert len(event_log.log_path(room_id).read_text(encoding="utf-8").splitlines()) == 200

    release = threading.Event()

    class Blocking:
        class parent:
            @staticmethod
            def mkdir(**kwargs):
                release.wait(5)
                raise OSError("blocked")

    event_log.log_event(room_id, "game_ready")
    event_log.pending.put((Blocking(), ""))
    event_log.log_event(room_id, "game_started")
    try:
        assert event_log.close(timeout=0.1) is False
    finally:
        release.set()
    for _ in range(100):
        if types(read_log(room_id))[-1] == "game_started":
            break
    assert types(read_log(room_id))[-2:] == ["game_ready", "game_started"]


def test_tile_painted_only_on_change():
    room = create_room([1, 2])
    start(room, 0)
    advance(room, 10)
    map_data = create_default_map()
    map_data["tiles"][0][1] = "GLASS"
    assert paint_tile(room, 1, 3, 4, map_data, 10)
    assert not paint_tile(room, 1, 3, 4, map_data, 10)
    assert not paint_tile(room, 1, 1, 0, map_data, 10)
    with pytest.raises(ValueError):
        paint_tile(room, 1, -1, 0, map_data, 10)
    assert paint_tile(room, 2, 3, 4, map_data, 10)
    assert [r["data"] for r in read_log(room.room_id)] == [
        {"user_id": 1, "x": 3, "y": 4, "previous_owner": None, "new_owner": 1},
        {"user_id": 2, "x": 3, "y": 4, "previous_owner": 1, "new_owner": 2},
    ]


def test_full_match_log(client, users, now):
    a, b, c = users
    token_a = make_token(a)
    with client.websocket_connect(WS_URL) as ws_b:
        with client.websocket_connect(WS_URL) as ws_a:
            ws_a.send_json({"type": "auth", "token": token_a})
            ws_a.receive_json()
            auth(ws_b, b)
            join_and_match(ws_a, ws_b)
            room = get_player_room(a)

            ws_a.send_json({"type": "card_use", "hand_index": 0})
            assert receive_until(ws_a, "error")["code"] == "game_not_playing"
            now[0] = 10
            receive_until(ws_a, "game_started")
            with websocket.state_lock:
                card_id = room.players[a].hand[0]
            ws_a.send_json({"type": "card_use", "hand_index": 0})
            receive_until(ws_a, "hand_updated")
            ws_a.send_json({"type": "card_use", "hand_index": 9})
            receive_until(ws_a, "error")
            now[0] = 10.86
            receive_until(ws_a, "cost_updated")
            with websocket.state_lock:
                paint_tile(room, a, 5, 5, create_default_map(), 10.86)
            now[0] = 250
            receive_until(ws_a, "deathmatch_started")
        receive_until(ws_b, "player_disconnected")

        with client.websocket_connect(WS_URL) as ws_a:
            auth(ws_a, a)
            receive_until(ws_a, "room_state")
            receive_until(ws_b, "player_reconnected")
            with client.websocket_connect(WS_URL) as ws_a2:
                auth(ws_a2, a)
                receive_until(ws_a2, "room_state")
                now[0] = 310
                receive_until(ws_a2, "game_result")
                assert wait_until(lambda: room.result_saved)

                with client.websocket_connect(WS_URL) as ws_c:
                    auth(ws_c, c)
                    ws_a2.send_json({"type": "room_leave"})
                    assert receive_until(ws_a2, "room_left")["room_id"] == str(room.room_id)
                    ws_b.send_json({"type": "room_leave"})
                    receive_until(ws_b, "room_left")

                    ws_a2.send_json({"type": "queue_join"})
                    receive_until(ws_a2, "queue_waiting")
                    ws_c.send_json({"type": "queue_join"})
                    receive_until(ws_c, "game_ready")
                    new_room = get_player_room(a)

    records = read_log(room.room_id)
    assert [r["sequence"] for r in records] == list(range(1, len(records) + 1))
    names = types(records)
    order = ["match_created", "game_ready", "game_started", "card_used", "tile_painted", "deathmatch_started",
             "player_disconnected", "player_reconnected", "game_finished", "game_result", "result_saved",
             "room_left", "room_left", "room_removed"]
    assert [n for n in names if n in order] == order
    assert names.count("card_used") == 1
    assert names.count("player_reconnected") == 1
    assert names.count("game_result") == 1
    assert names[-1] == "room_removed"

    by_type = {r["type"]: r["data"] for r in records}
    assert by_type["match_created"]["map_id"] == "default"
    assert [p["user_id"] for p in by_type["match_created"]["players"]] == [a, b]
    assert by_type["game_ready"] == {"remaining_seconds": 10}
    assert by_type["game_started"]["duration_seconds"] == 300
    assert datetime.fromisoformat(by_type["game_started"]["started_at"]) == room.started_at
    assert by_type["deathmatch_started"] == {"remaining_seconds": 60, "cost_interval": 0.45}
    assert by_type["game_finished"] == {}
    assert by_type["game_result"] == {"winner_id": room.result["winner_id"], "results": room.result["results"]}
    assert by_type["card_used"] == {
        "user_id": a, "card_id": card_id, "hand_index": 0,
        "cost": room.players[a].cost_used, "remaining_cost": 6 - room.players[a].cost_used,
    }
    costs = [r["data"] for r in records if r["type"] == "cost_changed"]
    assert costs[0] == {"user_id": a, "cost": 6 - room.players[a].cost_used, "reason": "card_used"}
    assert {"user_id": b, "cost": 7, "reason": "regen"} in costs
    assert all(c["reason"] in ("regen", "card_used") for c in costs)
    assert by_type["player_disconnected"] == {"user_id": a}
    assert [r["data"] for r in records if r["type"] == "room_left"] == [{"user_id": a}, {"user_id": b}]

    text = event_log.log_path(room.room_id).read_text(encoding="utf-8")
    assert token_a not in text
    assert os.environ["JWT_SECRET_KEY"] not in text
    assert os.environ["DATABASE_URL"] not in text
    for word in ("password", "token", "hand\"", "deck", "draw_queue", "cost_mark"):
        assert word not in text

    new_records = read_log(new_room.room_id)
    assert new_room.room_id != room.room_id
    assert types(new_records)[:2] == ["match_created", "game_ready"]
    assert new_records[0]["sequence"] == 1


def test_result_save_failure_logged(client, users, now, monkeypatch):
    original = websocket.store_result
    failing = [True]

    def flaky(room):
        if failing[0]:
            raise RuntimeError("password=secret host=db")
        return original(room)

    monkeypatch.setattr(websocket, "store_result", flaky)
    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        now[0] = 310
        receive_until(ws1, "game_result")
        assert wait_until(lambda: not websocket.timers)

        ws1.send_json({"type": "room_leave"})
        assert receive_until(ws1, "error")["code"] == "result_save_failed"
        failing[0] = False
        ws1.send_json({"type": "room_leave"})
        receive_until(ws1, "room_left")
        assert asyncio.run(websocket.save_result(room)) is True
        ws2.send_json({"type": "room_leave"})
        receive_until(ws2, "room_left")

    records = read_log(room.room_id)
    names = types(records)
    assert names.count("result_save_failed") == 2
    assert names.count("result_saved") == 1
    assert names.index("result_saved") > names.index("result_save_failed")
    assert [r["data"] for r in records if r["type"] == "result_save_failed"] == [{"error": "RuntimeError"}] * 2
    assert "secret" not in event_log.log_path(room.room_id).read_text(encoding="utf-8")


def test_session_replace_and_result_resend_not_logged(client, users, now):
    with client.websocket_connect(WS_URL) as ws2:
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            auth(ws2, users[1])
            join_and_match(ws1, ws2)
            room = get_player_room(users[0])
            with client.websocket_connect(WS_URL) as replacement:
                auth(replacement, users[0])
                receive_until(replacement, "room_state")
                now[0] = 310
                receive_until(replacement, "game_result")
                assert wait_until(lambda: room.result_saved)

        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            receive_until(ws1, "game_result")
        with client.websocket_connect(WS_URL) as ws1:
            auth(ws1, users[0])
            receive_until(ws1, "game_result")

    names = types(read_log(room.room_id))
    assert names.count("game_result") == 1
    assert names.count("game_finished") == 1
    assert names.count("result_saved") == 1
    assert names.count("player_reconnected") == 2
    assert names.count("player_disconnected") == 4
