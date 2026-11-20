import uuid

import pytest
from sqlalchemy import select

from app import ranks, websocket
from app.database import Base, SessionLocal, engine
from app.game_room import get_player_room
from app.models import Match, RankHistory
from app.ranks import SETTLEMENT_FIELDS, settle_match
from tests.test_game_engine import client, now, receive_until
from tests.test_ranks import BASE_TIME, history, make_users, rp_of
from tests.test_websocket import WS_URL, auth, clear_state, wait_until

FIELDS = {"type", "room_id", "match_id", "applied", "reason", *SETTLEMENT_FIELDS}


@pytest.fixture(autouse=True)
def clean():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def db_reset():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def settle(user_ids):
    match_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Match(id=match_id, map_id="default", started_at=BASE_TIME, ended_at=BASE_TIME, winner_id=user_ids[0]))
        db.flush()
        changes = settle_match(db, match_id, [{"user_id": uid, "rank": i + 1} for i, uid in enumerate(user_ids)])
        db.commit()
    return {c["user_id"]: c for c in changes}


def tiers(change):
    return (change["tier_before"], change["division_before"], change["tier_after"], change["division_after"])


def history_rows(match_id=None):
    with SessionLocal() as db:
        query = select(RankHistory)
        if match_id is not None:
            query = query.where(RankHistory.match_id == uuid.UUID(match_id))
        return {row.user_id: {key: getattr(row, key) for key in SETTLEMENT_FIELDS} for row in db.scalars(query)}


def open_players(client, ids):
    sockets = [client.websocket_connect(WS_URL) for _ in ids]
    for ws, uid in zip(sockets, ids):
        ws.__enter__()
        auth(ws, uid)
    return sockets


def close(sockets):
    for ws in sockets:
        ws.__exit__(None, None, None)


def play(sockets, ids, now, tiles=None):
    for ws in sockets:
        ws.send_json({"type": "queue_join"})
        assert ws.receive_json()["type"] == "queue_waiting"
    assert wait_until(lambda: all(get_player_room(uid) for uid in ids))
    room = get_player_room(ids[0])
    if tiles:
        with websocket.state_lock:
            room.tile_counts.update(tiles)
    now[0] = 320
    return room


def test_tier_transitions(db_reset):
    ids = make_users(4, rps=[1470, 1500, 1700, 610])
    changes = settle(ids)
    assert tiers(changes[ids[0]]) == ("DIAMOND", 3, "MASTER", None)
    assert tiers(changes[ids[1]]) == ("MASTER", None, "MASTER", None)
    assert tiers(changes[ids[2]]) == ("MASTER", None, "MASTER", None)
    assert tiers(changes[ids[3]]) == ("GOLD", 1, "SILVER", 3)


def test_challenger_snapshot(db_reset):
    ids = make_users(4, rps=[1790, 1500, 1500, 1810])
    changes = settle(ids)
    assert tiers(changes[ids[0]]) == ("MASTER", None, "CHALLENGER", None)
    assert tiers(changes[ids[3]]) == ("CHALLENGER", None, "MASTER", None)
    assert rp_of([ids[0], ids[3]]) == [1820, 1780]


def test_master_below_1800_stays_master_and_bronze_floor(db_reset):
    ids = make_users(4, rps=[1760, 0, 1500, 5])
    changes = settle(ids)
    assert tiers(changes[ids[0]]) == ("MASTER", None, "MASTER", None)
    assert changes[ids[0]]["rp_after"] == 1790
    assert tiers(changes[ids[1]]) == ("BRONZE", 1, "BRONZE", 1)
    assert (changes[ids[3]]["placement_rp_change"], changes[ids[3]]["rp_change"], changes[ids[3]]["rp_after"]) == (-30, -5, 0)


def test_four_player_settlement_sent_after_commit(client, db_reset, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    seen_in_db = []
    original = websocket.send_settlements

    async def checking(room, user_ids):
        seen_in_db.append(len(history_rows(str(room.room_id))))
        await original(room, user_ids)

    monkeypatch.setattr(websocket, "send_settlements", checking)
    sockets = open_players(client, ids)
    try:
        room = play(sockets, ids, now, {ids[0]: 9, ids[1]: 7, ids[2]: 5, ids[3]: 1})
        results = [receive_until(ws, "game_result") for ws in sockets]
        settlements = [receive_until(ws, "rank_settlement") for ws in sockets]
    finally:
        close(sockets)
    match_id = str(room.room_id)
    assert all(r["match_id"] == match_id == r["room_id"] for r in results)
    assert seen_in_db == [4]
    assert all(set(s) == FIELDS for s in settlements)
    assert [(s["applied"], s["reason"], s["placement"], s["rp_change"], s["rp_after"]) for s in settlements] == [
        (True, None, 1, 30, 530), (True, None, 2, 10, 510), (True, None, 3, -10, 490), (True, None, 4, -30, 470),
    ]
    assert all(s["room_id"] == s["match_id"] == match_id for s in settlements)
    assert all((s["tier_before"], s["division_before"]) == ("SILVER", 3) for s in settlements)
    assert [(s["tier_after"], s["division_after"]) for s in settlements] == [("SILVER", 3), ("SILVER", 3), ("SILVER", 2), ("SILVER", 2)]
    stored = history_rows(match_id)
    for uid, s in zip(ids, settlements):
        assert {key: s[key] for key in SETTLEMENT_FIELDS} == stored[uid]
    assert rp_of(ids) == [530, 510, 490, 470]


def test_two_player_settlement_not_applied(client, db_reset, now):
    ids = make_users(2, rps=[500, 500])
    sockets = open_players(client, ids)
    try:
        for ws in sockets:
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        now[0] = 10
        assert wait_until(lambda: get_player_room(ids[0]) is not None)
        room = get_player_room(ids[0])
        now[0] = 330
        settlements = [receive_until(ws, "rank_settlement") for ws in sockets]
    finally:
        close(sockets)
    expected = {"type": "rank_settlement", "room_id": str(room.room_id), "match_id": str(room.room_id), "applied": False, "reason": "unranked_match"}
    assert settlements == [expected, expected]
    assert rp_of(ids) == [500, 500]
    assert history() == []


def test_failed_save_sends_no_settlement_until_retry(client, db_reset, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    original = websocket.store_result
    failing = [True]

    def flaky(room):
        if failing[0]:
            raise RuntimeError("db down")
        return original(room)

    monkeypatch.setattr(websocket, "store_result", flaky)
    sockets = open_players(client, ids)
    try:
        room = play(sockets, ids, now)
        receive_until(sockets[0], "game_result")
        assert wait_until(lambda: not websocket.timers)
        assert room.result_saved is False
        sockets[0].send_json({"type": "room_state"})
        assert receive_until(sockets[0], "room_state")["status"] == "FINISHED"
        assert rp_of(ids) == [500] * 4

        failing[0] = False
        sockets[1].send_json({"type": "room_leave"})
        first = receive_until(sockets[1], "rank_settlement")
        assert receive_until(sockets[1], "room_left")["room_id"] == str(room.room_id)
        other = receive_until(sockets[0], "rank_settlement")
    finally:
        close(sockets)
    assert first["applied"] is True and other["applied"] is True
    assert rp_of(ids) == [500 + ranks.PLACEMENT_RP[r["rank"]] for r in sorted(room.result["results"], key=lambda r: ids.index(r["user_id"]))]


def test_failed_save_never_reports_success(client, db_reset, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    sent = []
    original_send = websocket.send_to

    async def recording(user_id, message):
        sent.append(message["type"])
        return await original_send(user_id, message)

    def broken(room):
        raise RuntimeError("db down")

    monkeypatch.setattr(websocket, "store_result", broken)
    monkeypatch.setattr(websocket, "send_to", recording)
    sockets = open_players(client, ids)
    try:
        play(sockets, ids, now)
        receive_until(sockets[0], "game_result")
        assert wait_until(lambda: not websocket.timers)
        assert receive_until(sockets[1], "game_result")["match_id"] == receive_until(sockets[2], "game_result")["match_id"]
    finally:
        close(sockets)
    assert "rank_settlement" not in sent
    assert history() == []


def test_reconnect_restores_settlement_without_resettling(client, db_reset, now):
    ids = make_users(4, rps=[500] * 4)
    sockets = open_players(client, ids)
    try:
        room = play(sockets, ids, now)
        result = receive_until(sockets[0], "game_result")
        settlement = receive_until(sockets[0], "rank_settlement")
        sockets[0].__exit__(None, None, None)
        assert wait_until(lambda: not room.players[ids[0]].connected)
        before = rp_of(ids)
        for _ in range(2):
            with client.websocket_connect(WS_URL) as again:
                auth(again, ids[0])
                assert again.receive_json()["type"] == "room_state"
                assert again.receive_json() == result
                assert again.receive_json() == settlement
        assert rp_of(ids) == before
        assert len(history()) == 4
    finally:
        close(sockets[1:])


def test_duplicate_save_sends_settlement_once(client, db_reset, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    sent = []
    original_send = websocket.send_to

    async def recording(user_id, message):
        if message["type"] == "rank_settlement":
            sent.append(user_id)
        return await original_send(user_id, message)

    monkeypatch.setattr(websocket, "send_to", recording)
    sockets = open_players(client, ids)
    try:
        play(sockets, ids, now)
        receive_until(sockets[0], "rank_settlement")
        assert wait_until(lambda: len(sent) == 4)
        for ws in sockets[1:3]:
            ws.send_json({"type": "room_leave"})
            receive_until(ws, "room_left")
        assert sorted(sent) == sorted(ids)
        assert len(history()) == 4
    finally:
        close(sockets)
