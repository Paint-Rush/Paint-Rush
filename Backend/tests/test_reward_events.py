import pytest
from sqlalchemy import select

from app import websocket
from app.database import Base, SessionLocal, engine
from app.game_room import get_player_room
from app.models import MatchReward, UserLevel, UserWallet
from app.rewards import REWARD_FIELDS
from app.ranks import SETTLEMENT_FIELDS
from tests.test_game_engine import client, now, receive_until
from tests.test_rank_settlement import close, open_players, play
from tests.test_ranks import make_users
from tests.test_websocket import WS_URL, auth, clear_state, wait_until

FIELDS = {"type", "room_id", "match_id", "applied", "reason", "leveled_up", *REWARD_FIELDS}
RANK_FIELDS = {"type", "room_id", "match_id", "applied", "reason", *SETTLEMENT_FIELDS}


@pytest.fixture(autouse=True)
def clean():
    clear_state()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    clear_state()


def rows(match_id):
    with SessionLocal() as db:
        return {
            r.user_id: {key: getattr(r, key) for key in REWARD_FIELDS}
            for r in db.scalars(select(MatchReward).where(MatchReward.match_id == match_id))
        }


def balances(ids):
    with SessionLocal() as db:
        gold = dict(db.execute(select(UserWallet.user_id, UserWallet.gold).where(UserWallet.user_id.in_(ids))).all())
        exp = dict(db.execute(select(UserLevel.user_id, UserLevel.total_experience).where(UserLevel.user_id.in_(ids))).all())
    return [(gold.get(uid), exp.get(uid)) for uid in ids]


def recording(monkeypatch):
    sent = []
    original = websocket.send_to

    async def record(user_id, message):
        sent.append((user_id, message))
        return await original(user_id, message)

    monkeypatch.setattr(websocket, "send_to", record)
    return sent


def test_four_player_events_only_carry_own_reward(client, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    sent = recording(monkeypatch)
    sockets = open_players(client, ids)
    try:
        room = play(sockets, ids, now, {ids[0]: 9, ids[1]: 7, ids[2]: 5, ids[3]: 1})
        ranked = [receive_until(ws, "rank_settlement") for ws in sockets]
        events = [ws.receive_json() for ws in sockets]
    finally:
        close(sockets)
    match_id = str(room.room_id)
    stored = rows(room.room_id)
    assert all(r["applied"] is True and set(r) == RANK_FIELDS for r in ranked)
    for uid, event in zip(ids, events):
        assert event["type"] == "reward_settlement" and set(event) == FIELDS
        assert event["room_id"] == event["match_id"] == match_id
        assert (event["applied"], event["reason"]) == (True, None)
        assert {key: event[key] for key in REWARD_FIELDS} == stored[uid]
        assert event["leveled_up"] is (event["level_after"] > event["level_before"])
    assert [(e["placement"], e["gold_earned"], e["experience_earned"], e["leveled_up"]) for e in events] == [
        (1, 100, 100, True), (2, 80, 80, False), (3, 60, 60, False), (4, 40, 40, False),
    ]
    reward_sends = [(uid, m) for uid, m in sent if m["type"] == "reward_settlement"]
    assert sorted(uid for uid, _ in reward_sends) == sorted(ids)
    for uid, message in reward_sends:
        assert message["placement"] == stored[uid]["placement"]
        assert message["gold_after"] == stored[uid]["gold_after"]


def test_two_player_events_with_unranked_rp(client, now):
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
        pairs = [(receive_until(ws, "rank_settlement"), ws.receive_json()) for ws in sockets]
    finally:
        close(sockets)
    stored = rows(room.room_id)
    for uid, (rank, reward) in zip(ids, pairs):
        assert rank == {"type": "rank_settlement", "room_id": str(room.room_id), "match_id": str(room.room_id), "applied": False, "reason": "unranked_match"}
        assert reward["type"] == "reward_settlement" and reward["applied"] is True
        assert {key: reward[key] for key in REWARD_FIELDS} == stored[uid]
    assert sorted(r["gold_earned"] for _, r in pairs) == [40, 50]


def test_events_sent_only_after_commit(client, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    seen = []
    original = websocket.send_settlements

    async def checking(room, user_ids):
        seen.append(len(rows(room.room_id)))
        await original(room, user_ids)

    monkeypatch.setattr(websocket, "send_settlements", checking)
    sockets = open_players(client, ids)
    try:
        play(sockets, ids, now)
        receive_until(sockets[0], "reward_settlement")
    finally:
        close(sockets)
    assert seen == [4]


def test_failed_save_sends_nothing_then_retry_sends(client, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    sent = recording(monkeypatch)
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
        assert not any(m["type"] == "reward_settlement" for _, m in sent)
        assert rows(room.room_id) == {}
        assert balances(ids) == [(None, None)] * 4

        failing[0] = False
        sockets[2].send_json({"type": "room_leave"})
        mine = receive_until(sockets[2], "reward_settlement")
        receive_until(sockets[2], "room_left")
        other = receive_until(sockets[0], "reward_settlement")
    finally:
        close(sockets)
    stored = rows(room.room_id)
    assert {key: mine[key] for key in REWARD_FIELDS} == stored[ids[2]]
    assert {key: other[key] for key in REWARD_FIELDS} == stored[ids[0]]
    assert len([m for _, m in sent if m["type"] == "reward_settlement"]) == 4


def test_reconnect_restores_reward_without_paying_again(client, now):
    ids = make_users(4, rps=[500] * 4)
    sockets = open_players(client, ids)
    try:
        room = play(sockets, ids, now)
        result = receive_until(sockets[0], "game_result")
        rank = receive_until(sockets[0], "rank_settlement")
        reward = sockets[0].receive_json()
        assert reward["type"] == "reward_settlement"
        sockets[0].__exit__(None, None, None)
        assert wait_until(lambda: not room.players[ids[0]].connected)
        before = balances(ids)
        for _ in range(2):
            with client.websocket_connect(WS_URL) as again:
                auth(again, ids[0])
                assert [again.receive_json()["type"], again.receive_json(), again.receive_json(), again.receive_json()] == [
                    "room_state", result, rank, reward,
                ]
        assert balances(ids) == before
        assert len(rows(room.room_id)) == 4
    finally:
        close(sockets[1:])


def test_duplicate_saves_send_rewards_once(client, now, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    sent = recording(monkeypatch)
    sockets = open_players(client, ids)
    try:
        play(sockets, ids, now)
        for ws in sockets:
            receive_until(ws, "reward_settlement")
        for ws in sockets[1:3]:
            ws.send_json({"type": "room_leave"})
            receive_until(ws, "room_left")
    finally:
        close(sockets)
    rewards_sent = [uid for uid, m in sent if m["type"] == "reward_settlement"]
    assert sorted(rewards_sent) == sorted(ids)
    assert balances(ids) == [(100, 100), (80, 80), (60, 60), (40, 40)]
