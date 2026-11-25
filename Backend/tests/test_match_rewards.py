import threading

import pytest
from sqlalchemy import func, select

from app import ranks, rewards
from app.database import Base, SessionLocal, engine
from app.game_engine import advance, start
from app.game_room import create_room, get_player_room
from app.levels import save_level
from app.models import Match, MatchPlayerResult, MatchReward, RankHistory, User, UserLevel, UserRank, UserWallet
from app.results import calculate_results, save_match_result
from app.rewards import PLACEMENT_REWARDS, reward_amounts
from tests.test_game_engine import client, now, receive_until
from tests.test_websocket import WS_URL, auth, clear_state, wait_until


@pytest.fixture(autouse=True)
def db_reset():
    clear_state()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    clear_state()


def make_users(count, gold=None, rp=None):
    with SessionLocal() as db:
        rows = [User(username=f"rw{i}", password_hash="unused") for i in range(count)]
        db.add_all(rows)
        db.flush()
        for i, row in enumerate(rows):
            if gold is not None:
                db.add(UserWallet(user_id=row.id, gold=gold[i]))
            if rp is not None:
                db.add(UserRank(user_id=row.id, total_rp=rp[i]))
        db.commit()
        return [row.id for row in rows]


def finished_room(ids, tiles=None, kills=None):
    room = create_room(list(ids))
    start(room, 0)
    advance(room, 310)
    for uid, value in (tiles or {}).items():
        room.tile_counts[uid] = value
    for uid, value in (kills or {}).items():
        room.players[uid].kills = value
    room.result = calculate_results(room)
    return room


def save(room):
    with SessionLocal() as db:
        save_match_result(room, db)


def order(room):
    return [r["user_id"] for r in room.result["results"]]


def state(ids):
    with SessionLocal() as db:
        gold = dict(db.execute(select(UserWallet.user_id, UserWallet.gold).where(UserWallet.user_id.in_(ids))).all())
        levels = {r.user_id: (r.level, r.total_experience) for r in db.scalars(select(UserLevel).where(UserLevel.user_id.in_(ids)))}
        rp = dict(db.execute(select(UserRank.user_id, UserRank.total_rp).where(UserRank.user_id.in_(ids))).all())
    return {uid: (gold.get(uid), levels.get(uid), rp.get(uid)) for uid in ids}


def reward_rows(match_id=None):
    with SessionLocal() as db:
        query = select(MatchReward)
        if match_id is not None:
            query = query.where(MatchReward.match_id == match_id)
        return {
            r.user_id: (r.placement, r.gold_earned, r.gold_before, r.gold_after, r.experience_earned,
                        r.experience_before, r.experience_after, r.level_before, r.level_after)
            for r in db.scalars(query)
        }


def counts():
    with SessionLocal() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Match, MatchPlayerResult, RankHistory, MatchReward))


def results(*ranks_):
    return [{"user_id": 10 + i, "rank": rank} for i, rank in enumerate(ranks_)]


def test_four_player_reward_table():
    amounts = reward_amounts(results(1, 2, 3, 4))
    assert [(a["placement"], a["gold"], a["experience"]) for a in amounts.values()] == [(1, 100, 100), (2, 80, 80), (3, 60, 60), (4, 40, 40)]


def test_two_player_reward_table_is_half():
    amounts = reward_amounts(results(1, 2))
    assert [(a["gold"], a["experience"]) for a in amounts.values()] == [(50, 50), (40, 40)]
    for placement, value in PLACEMENT_REWARDS[2].items():
        assert value * 2 == PLACEMENT_REWARDS[4][placement]


@pytest.mark.parametrize("bad", [
    results(1, 2, 3),
    results(1),
    results(1, 2, 3, 4, 5),
    results(1, 1, 3, 4),
    results(2, 1, 3, 4),
    results(1, 3),
    [{"user_id": 1, "rank": 1}, {"user_id": 1, "rank": 2}],
    [{"user_id": "1", "rank": 1}, {"user_id": 2, "rank": 2}],
])
def test_invalid_results_get_nothing(bad):
    assert reward_amounts(bad) == {}


def test_kills_and_tiles_do_not_change_amounts():
    ids = make_users(4)
    low = finished_room(ids, tiles={ids[0]: 4, ids[1]: 3, ids[2]: 2, ids[3]: 1})
    clear_state()
    high = finished_room(ids, tiles={ids[0]: 400, ids[1]: 300, ids[2]: 200, ids[3]: 1}, kills={ids[0]: 9, ids[3]: 20})
    assert reward_amounts(low.result["results"]) == reward_amounts(high.result["results"])


def test_four_player_match_settles_rp_gold_and_experience():
    ids = make_users(4, gold=[10, 0, 500, 0], rp=[500] * 4)
    with SessionLocal() as db:
        save_level(db, ids[3], 2, 150)
    room = finished_room(ids, tiles={ids[0]: 9, ids[1]: 7, ids[2]: 5, ids[3]: 1})
    save(room)
    assert order(room) == ids
    assert state(ids) == {
        ids[0]: (110, (2, 100), 530),
        ids[1]: (80, (1, 80), 510),
        ids[2]: (560, (1, 60), 490),
        ids[3]: (40, (2, 190), 470),
    }
    assert reward_rows(room.room_id) == {
        ids[0]: (1, 100, 10, 110, 100, 0, 100, 1, 2),
        ids[1]: (2, 80, 0, 80, 80, 0, 80, 1, 1),
        ids[2]: (3, 60, 500, 560, 60, 0, 60, 1, 1),
        ids[3]: (4, 40, 0, 40, 40, 150, 190, 2, 2),
    }
    assert counts() == (1, 4, 4, 4)
    with SessionLocal() as db:
        assert all(r.created_at is not None for r in db.scalars(select(MatchReward)))


def test_reward_placement_follows_quick_sort_result():
    ids = make_users(4)
    room = finished_room(ids, tiles={ids[2]: 8, ids[0]: 6, ids[3]: 6})
    save(room)
    placements = {uid: row[0] for uid, row in reward_rows().items()}
    assert placements == {r["user_id"]: r["rank"] for r in room.result["results"]}
    assert order(room) == [ids[2], ids[0], ids[3], ids[1]]


def test_two_player_match_gold_and_experience_only():
    ids = make_users(2, gold=[0, 0], rp=[500, 500])
    room = finished_room(ids, tiles={ids[1]: 3})
    save(room)
    assert state(ids) == {ids[1]: (50, (1, 50), 500), ids[0]: (40, (1, 40), 500)}
    assert reward_rows(room.room_id) == {
        ids[1]: (1, 50, 0, 50, 50, 0, 50, 1, 1),
        ids[0]: (2, 40, 0, 40, 40, 0, 40, 1, 1),
    }
    assert counts() == (1, 2, 0, 2)


def test_level_up_from_several_matches():
    ids = make_users(4)
    for _ in range(3):
        clear_state()
        save(finished_room(ids, tiles={ids[0]: 9, ids[1]: 7, ids[2]: 5, ids[3]: 1}))
    assert {uid: s[1] for uid, s in state(ids).items()} == {ids[0]: (3, 300), ids[1]: (2, 240), ids[2]: (2, 180), ids[3]: (2, 120)}
    with SessionLocal() as db:
        levels = sorted((r.experience_before, r.level_before, r.level_after) for r in db.scalars(select(MatchReward).where(MatchReward.user_id == ids[0])))
    assert levels == [(0, 1, 2), (100, 2, 2), (200, 2, 3)]


def test_disconnected_player_still_rewarded():
    ids = make_users(4)
    room = finished_room(ids, tiles={ids[0]: 9, ids[1]: 7, ids[2]: 5, ids[3]: 1})
    room.players[ids[3]].connected = False
    save(room)
    assert reward_rows(room.room_id)[ids[3]][:2] == (4, 40)


def test_invalid_four_player_result_saved_without_rewards():
    ids = make_users(4, gold=[7] * 4, rp=[500] * 4)
    room = finished_room(ids)
    room.result["results"][1]["rank"] = 1
    save(room)
    assert counts() == (1, 4, 0, 0)
    assert state(ids) == {uid: (7, None, 500) for uid in ids}


def test_repeat_settlement_pays_once():
    ids = make_users(4, rp=[500] * 4)
    room = finished_room(ids)
    for _ in range(3):
        save(room)
    assert counts() == (1, 4, 4, 4)
    assert [s[0] for s in state(order(room)).values()] == [100, 80, 60, 40]


def test_concurrent_settlement_pays_once():
    ids = make_users(4, rp=[500] * 4)
    room = finished_room(ids)
    barrier = threading.Barrier(6)
    errors = []

    def run():
        barrier.wait()
        try:
            save(room)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert counts() == (1, 4, 4, 4)
    assert [s for s in state(order(room)).values()] == [(100, (2, 100), 530), (80, (1, 80), 510), (60, (1, 60), 490), (40, (1, 40), 470)]


def failing_after(monkeypatch, module, name, calls_before_failure):
    original = getattr(module, name)
    calls = []

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) > calls_before_failure:
            raise RuntimeError(f"{name} failed")
        return result

    monkeypatch.setattr(module, name, wrapper)


@pytest.mark.parametrize("module, name, after", [
    (rewards, "add_gold", 0),
    (rewards, "add_gold", 2),
    (rewards, "add_experience", 0),
    (rewards, "add_experience", 3),
    (ranks, "settle_match", 0),
])
def test_failure_rolls_back_everything(monkeypatch, module, name, after):
    ids = make_users(4, gold=[25] * 4, rp=[500] * 4)
    with SessionLocal() as db:
        save_level(db, ids[0], 3, 333)
    before = state(ids)
    room = finished_room(ids)
    failing_after(monkeypatch, module, name, after)
    with pytest.raises(RuntimeError):
        save(room)
    assert counts() == (0, 0, 0, 0)
    assert state(ids) == before


def test_reward_row_failure_rolls_back_everything(monkeypatch):
    ids = make_users(4, gold=[25] * 4, rp=[500] * 4)
    before = state(ids)
    room = finished_room(ids)
    original = rewards.MatchReward
    monkeypatch.setattr(rewards, "MatchReward", lambda **kw: original(**{**kw, "gold_after": kw["gold_after"] + 1}))
    with pytest.raises(Exception):
        save(room)
    assert counts() == (0, 0, 0, 0)
    assert state(ids) == before


def test_retry_after_failure_pays_once(monkeypatch):
    ids = make_users(4, gold=[0] * 4, rp=[500] * 4)
    room = finished_room(ids)
    failing_after(monkeypatch, rewards, "add_experience", 1)
    with pytest.raises(RuntimeError):
        save(room)
    monkeypatch.undo()
    save(room)
    save(room)
    assert counts() == (1, 4, 4, 4)
    assert [s[0] for s in state(order(room)).values()] == [100, 80, 60, 40]


def test_existing_gold_and_experience_preserved():
    ids = make_users(2, gold=[1234, 0])
    with SessionLocal() as db:
        save_level(db, ids[1], 4, 650)
    save(finished_room(ids, tiles={ids[0]: 2}))
    assert state(ids) == {ids[0]: (1284, (1, 50), None), ids[1]: (40, (4, 690), None)}


@pytest.mark.usefixtures("immediate_matchmaking")
def test_websocket_save_failure_then_leave_pays_once(client, now, monkeypatch):
    from app import websocket

    ids = make_users(2)
    original = websocket.store_result
    failing = [True]

    def flaky(room):
        if failing[0]:
            raise RuntimeError("db down")
        return original(room)

    monkeypatch.setattr(websocket, "store_result", flaky)
    sockets = [client.websocket_connect(WS_URL) for _ in ids]
    try:
        for ws, uid in zip(sockets, ids):
            ws.__enter__()
            auth(ws, uid)
        for ws in sockets:
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        now[0] = 10
        assert wait_until(lambda: get_player_room(ids[0]) is not None)
        now[0] = 330
        receive_until(sockets[0], "game_result")
        assert wait_until(lambda: not websocket.timers)
        assert counts() == (0, 0, 0, 0)
        failing[0] = False
        sockets[0].send_json({"type": "room_leave"})
        settlement = receive_until(sockets[0], "rank_settlement")
        receive_until(sockets[0], "room_left")
        sockets[1].send_json({"type": "room_leave"})
        receive_until(sockets[1], "room_left")
    finally:
        for ws in sockets:
            ws.__exit__(None, None, None)
    assert settlement["applied"] is False and settlement["reason"] == "unranked_match"
    assert counts() == (1, 2, 0, 2)
    assert sorted(s[0] for s in state(ids).values()) == [40, 50]
