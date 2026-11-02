import asyncio
import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from app import websocket
from app.database import Base, SessionLocal, engine
from app.game_engine import advance, start
from app.game_room import RoomStatus, create_room, get_player_room
from app.models import Match, MatchPlayerResult, User
from app.results import calculate_results, load_match_record, save_match_result
from tests.test_game_engine import client, now, receive_until
from tests.test_websocket import WS_URL, auth, clear_state, join_and_match, wait_until


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def user_ids():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(8)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def finished_room(ids, stats=None):
    room = create_room(list(ids))
    start(room, 0)
    advance(room, 310)
    for uid, values in (stats or {}).items():
        player = room.players[uid]
        room.tile_counts[uid] = values.get("tiles", 0)
        for key in ("total_painted", "kills", "deaths", "cards_used", "cost_used"):
            setattr(player, key, values.get(key, 0))
    room.result = calculate_results(room)
    return room


def save(room):
    with SessionLocal() as db:
        save_match_result(room, db)


def counts():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Match)), db.scalar(select(func.count()).select_from(MatchPlayerResult))


def stored(room):
    with SessionLocal() as db:
        return load_match_record(db, room.room_id)


def test_tables_created(user_ids):
    inspector = inspect(engine)
    assert {"users", "matches", "match_player_results"} <= set(inspector.get_table_names())

    columns = {c["name"]: c for c in inspector.get_columns("matches")}
    assert set(columns) == {"id", "map_id", "started_at", "ended_at", "winner_id"}
    assert str(columns["id"]["type"]) == "UUID"
    assert columns["map_id"]["type"].length == 50
    assert columns["started_at"]["type"].timezone is True
    assert columns["ended_at"]["type"].timezone is True
    assert not any(c["nullable"] for c in columns.values())
    assert inspector.get_pk_constraint("matches")["constrained_columns"] == ["id"]
    assert [(fk["constrained_columns"], fk["referred_table"]) for fk in inspector.get_foreign_keys("matches")] == [
        (["winner_id"], "users"),
    ]

    columns = {c["name"]: c for c in inspector.get_columns("match_player_results")}
    assert set(columns) == {
        "match_id", "user_id", "rank", "matchmaking_order", "painted_tiles",
        "total_painted", "kills", "deaths", "cards_used", "cost_used",
    }
    assert not any(c["nullable"] for c in columns.values())
    assert inspector.get_pk_constraint("match_player_results")["constrained_columns"] == ["match_id", "user_id"]
    fks = {(tuple(fk["constrained_columns"]), fk["referred_table"]) for fk in inspector.get_foreign_keys("match_player_results")}
    assert fks == {(("match_id",), "matches"), (("user_id",), "users")}


@pytest.mark.parametrize("size", [2, 3, 4])
def test_save_match(user_ids, size):
    ids = user_ids[:size]
    stats = {
        uid: {"tiles": i * 3, "total_painted": i * 5 + 1, "kills": i, "deaths": size - i,
              "cards_used": i + 2, "cost_used": i * 4 + 3}
        for i, uid in enumerate(ids)
    }
    room = finished_room(ids, stats)
    room.players[ids[0]].connected = False
    save(room)

    match, players = stored(room)
    assert match == {
        "id": room.room_id,
        "map_id": room.map_id,
        "started_at": room.started_at,
        "ended_at": room.started_at + timedelta(seconds=300),
        "winner_id": ids[-1],
    }
    assert match["started_at"].utcoffset() is not None
    assert [p["user_id"] for p in players] == list(reversed(ids))
    assert [p["rank"] for p in players] == list(range(1, size + 1))
    for p in players:
        i = ids.index(p["user_id"])
        assert p == {
            "match_id": room.room_id,
            "user_id": p["user_id"],
            "rank": size - i,
            "matchmaking_order": i,
            "painted_tiles": i * 3,
            "total_painted": i * 5 + 1,
            "kills": i,
            "deaths": size - i,
            "cards_used": i + 2,
            "cost_used": i * 4 + 3,
        }
    assert counts() == (1, size)


def test_room_unchanged_after_save(user_ids):
    room = finished_room(user_ids[:2], {user_ids[0]: {"tiles": 2, "kills": 1}})
    before = (dict(room.tile_counts), room.result, room.players[user_ids[0]].kills, room.status)
    save(room)
    assert (dict(room.tile_counts), room.result, room.players[user_ids[0]].kills, room.status) == before
    assert room.result == calculate_results(room)


def test_duplicate_save_ignored(user_ids):
    room = finished_room(user_ids[:2])
    save(room)
    save(room)
    assert counts() == (1, 2)


def test_conflicting_result_rejected(user_ids):
    room = finished_room(user_ids[:2])
    save(room)
    record = stored(room)
    room.result = {**room.result, "winner_id": user_ids[1]}
    with pytest.raises(ValueError):
        save(room)
    assert stored(room) == record
    assert counts() == (1, 2)


def test_duplicate_detected_by_primary_key(user_ids):
    room = finished_room(user_ids[:2])
    save(room)
    with SessionLocal() as db:
        real_get = db.get
        calls = []

        def stale_get(entity, ident):
            calls.append(entity)
            return None if len(calls) == 1 else real_get(entity, ident)

        db.get = stale_get
        save_match_result(room, db)
    assert counts() == (1, 2)


def test_concurrent_saves(user_ids):
    room = finished_room(user_ids[:2])
    errors = []
    barrier = threading.Barrier(6)

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
    assert counts() == (1, 2)


def test_matches_independent(user_ids):
    a = finished_room(user_ids[:2], {user_ids[1]: {"tiles": 1}})
    b = finished_room(user_ids[2:4], {user_ids[2]: {"tiles": 1}})
    save(a)
    save(b)
    assert stored(a)[0]["winner_id"] == user_ids[1]
    assert stored(b)[0]["winner_id"] == user_ids[2]
    assert counts() == (2, 4)


def test_rollback_on_error(user_ids):
    room = finished_room([user_ids[0], 99999], {user_ids[0]: {"tiles": 1}})
    with pytest.raises(IntegrityError):
        save(room)
    assert counts() == (0, 0)

    other = finished_room(user_ids[2:4])
    save(other)
    assert counts() == (1, 2)


@pytest.mark.parametrize("t", [5, 100])
def test_rejected_before_finished(user_ids, t):
    room = create_room(user_ids[:2])
    start(room, 0)
    advance(room, t)
    room.result = {"room_id": str(room.room_id), "winner_id": user_ids[0], "results": []}
    with pytest.raises(ValueError):
        save(room)
    assert counts() == (0, 0)


def test_rejected_without_result(user_ids):
    room = create_room(user_ids[:2])
    start(room, 0)
    advance(room, 310)
    with pytest.raises(ValueError):
        save(room)
    other = finished_room(user_ids[2:4])
    other.result = {**other.result, "room_id": str(room.room_id)}
    with pytest.raises(ValueError):
        save(other)
    assert counts() == (0, 0)


def test_auto_save_on_finish(client, now):
    with SessionLocal() as db:
        rows = [User(username=f"player{i}", password_hash="unused") for i in range(2)]
        db.add_all(rows)
        db.commit()
        users = [row.id for row in rows]

    with client.websocket_connect(WS_URL) as ws1, client.websocket_connect(WS_URL) as ws2:
        auth(ws1, users[0])
        auth(ws2, users[1])
        join_and_match(ws1, ws2)
        room = get_player_room(users[0])
        now[0] = 310
        result = receive_until(ws1, "game_result")
        assert receive_until(ws2, "game_result") == result
        assert wait_until(lambda: room.result_saved)

    match, players = stored(room)
    assert match["id"] == room.room_id
    assert match["winner_id"] == result["winner_id"]
    assert [p["user_id"] for p in players] == [r["user_id"] for r in result["results"]]


def test_save_failure_isolated(user_ids, now, monkeypatch):
    original = websocket.store_result

    def broken(room):
        if room.player_order[0] == user_ids[0]:
            raise RuntimeError("db down")
        return original(room)

    monkeypatch.setattr(websocket, "store_result", broken)

    async def main():
        a, b = create_room(user_ids[:2]), create_room(user_ids[2:4])
        websocket.start_timer(a)
        websocket.start_timer(b)
        now[0] = 310
        for _ in range(300):
            if not websocket.timers:
                break
            await asyncio.sleep(0.01)
        return a, b

    a, b = asyncio.run(main())
    assert a.status is b.status is RoomStatus.FINISHED
    assert a.result is not None
    assert a.result_saved is False
    assert b.result_saved is True
    assert stored(a) is None
    assert stored(b)[0]["id"] == b.room_id
