import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, inspect, select

from app import ranks
from app.database import Base, SessionLocal, engine
from app.game_engine import advance, start
from app.game_room import create_room, get_player_room
from app.models import Match, RankHistory, User, UserRank
from app.ranks import apply_placement, rank_info, ranked_placements, read_challengers, read_ranking, settle_match
from app.results import calculate_results, save_match_result
from tests.test_game_engine import client, now, receive_until
from tests.test_user_deck import headers
from tests.test_websocket import WS_URL, auth, clear_state, wait_until

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def db_reset():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def make_users(count, rps=None, reached=None):
    with SessionLocal() as db:
        rows = [User(username=f"rank{i}", password_hash="unused") for i in range(count)]
        db.add_all(rows)
        db.flush()
        if rps is not None:
            db.add_all(
                UserRank(
                    user_id=row.id,
                    total_rp=rp,
                    rp_reached_at=(reached or {}).get(i, BASE_TIME),
                )
                for i, (row, rp) in enumerate(zip(rows, rps))
            )
        db.commit()
        return [row.id for row in rows]


def rp_of(user_ids):
    with SessionLocal() as db:
        values = dict(db.execute(select(UserRank.user_id, UserRank.total_rp).where(UserRank.user_id.in_(user_ids))).all())
    return [values.get(user_id) for user_id in user_ids]


def history(user_ids=None):
    with SessionLocal() as db:
        query = select(RankHistory).order_by(RankHistory.id)
        if user_ids is not None:
            query = query.where(RankHistory.user_id.in_(user_ids))
        return [
            (row.user_id, row.placement, row.rp_before, row.placement_rp_change, row.rp_change, row.rp_after)
            for row in db.scalars(query)
        ]


def finished_room(ids, tiles=None):
    room = create_room(list(ids))
    start(room, 0)
    advance(room, 310)
    for uid, value in (tiles or {}).items():
        room.tile_counts[uid] = value
    room.result = calculate_results(room)
    return room


def save(room):
    with SessionLocal() as db:
        save_match_result(room, db)


def ranking(limit=100, offset=0):
    with SessionLocal() as db:
        return read_ranking(db, limit, offset)


def challengers():
    with SessionLocal() as db:
        return read_challengers(db)


def settle(user_ids, match_id=None):
    import uuid

    match_id = match_id or uuid.uuid4()
    with SessionLocal() as db:
        db.add(Match(id=match_id, map_id="default", started_at=BASE_TIME, ended_at=BASE_TIME, winner_id=user_ids[0]))
        db.flush()
        changes = settle_match(db, match_id, [{"user_id": uid, "rank": i + 1} for i, uid in enumerate(user_ids)])
        db.commit()
    return changes


@pytest.mark.parametrize("placement, change", [(1, 30), (2, 10), (3, -10), (4, -30)])
def test_placement_rp(placement, change):
    assert apply_placement(500, placement) == (change, change, 500 + change)


@pytest.mark.parametrize("before, placement, expected", [
    (0, 4, (-30, 0, 0)),
    (0, 3, (-10, 0, 0)),
    (25, 4, (-30, -25, 0)),
    (10, 3, (-10, -10, 0)),
])
def test_bronze_floor(before, placement, expected):
    assert apply_placement(before, placement) == expected


def test_overflow_carries_over():
    _, _, after = apply_placement(90, 1)
    assert after == 120
    info = rank_info(after)
    assert (info["tier"], info["division"], info["rp_to_next"]) == ("BRONZE", 2, 80)


@pytest.mark.parametrize("rp, tier, division", [
    (0, "BRONZE", 1), (99, "BRONZE", 1), (100, "BRONZE", 2), (199, "BRONZE", 2), (200, "BRONZE", 3), (299, "BRONZE", 3),
    (300, "SILVER", 1), (400, "SILVER", 2), (599, "SILVER", 3),
    (600, "GOLD", 1), (799, "GOLD", 2), (899, "GOLD", 3),
    (900, "PLATINUM", 1), (1000, "PLATINUM", 2), (1199, "PLATINUM", 3),
    (1200, "DIAMOND", 1), (1300, "DIAMOND", 2), (1499, "DIAMOND", 3),
    (1500, "MASTER", None), (1799, "MASTER", None), (9999, "MASTER", None),
])
def test_tier_boundaries(rp, tier, division):
    info = rank_info(rp)
    assert (info["tier"], info["division"]) == (tier, division)


@pytest.mark.parametrize("before, placement, tier_before, tier_after", [
    (290, 1, ("BRONZE", 3), ("SILVER", 1)),
    (305, 4, ("SILVER", 1), ("BRONZE", 3)),
    (1490, 1, ("DIAMOND", 3), ("MASTER", None)),
    (1505, 3, ("MASTER", None), ("DIAMOND", 3)),
    (1520, 4, ("MASTER", None), ("DIAMOND", 3)),
])
def test_promotion_and_demotion(before, placement, tier_before, tier_after):
    _, _, after = apply_placement(before, placement)
    assert (rank_info(before)["tier"], rank_info(before)["division"]) == tier_before
    assert (rank_info(after)["tier"], rank_info(after)["division"]) == tier_after


@pytest.mark.parametrize("rp, next_tier, next_division, rp_to_next", [
    (0, "BRONZE", 2, 100),
    (299, "SILVER", 1, 1),
    (1499, "MASTER", None, 1),
    (1500, "CHALLENGER", None, 300),
    (1800, None, None, None),
])
def test_next_tier(rp, next_tier, next_division, rp_to_next):
    info = rank_info(rp)
    assert (info["next_tier"], info["next_division"], info["rp_to_next"]) == (next_tier, next_division, rp_to_next)


@pytest.mark.parametrize("rp, position, tier", [
    (1799, 1, "MASTER"),
    (1800, 1, "CHALLENGER"),
    (1800, 100, "CHALLENGER"),
    (1800, 101, "MASTER"),
    (5000, 101, "MASTER"),
    (1800, None, "MASTER"),
])
def test_challenger_rule(rp, position, tier):
    assert rank_info(rp, position)["tier"] == tier


@pytest.mark.parametrize("results", [
    [{"user_id": 1, "rank": 1}, {"user_id": 2, "rank": 2}],
    [{"user_id": i, "rank": i} for i in (1, 2, 3)],
    [{"user_id": i, "rank": i} for i in (1, 2, 3, 4, 5)],
    [{"user_id": 1, "rank": 1}, {"user_id": 1, "rank": 2}, {"user_id": 3, "rank": 3}, {"user_id": 4, "rank": 4}],
    [{"user_id": i, "rank": r} for i, r in ((1, 1), (2, 1), (3, 3), (4, 4))],
    [{"user_id": i, "rank": r} for i, r in ((1, 2), (2, 1), (3, 3), (4, 4))],
    [{"user_id": str(i), "rank": i} for i in (1, 2, 3, 4)],
])
def test_invalid_results_not_ranked(results):
    assert ranked_placements(results) is None


def test_tables_created(db_reset):
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("user_ranks")}
    assert columns == {"user_id", "total_rp", "rp_reached_at", "created_at", "updated_at"}
    columns = {c["name"] for c in inspector.get_columns("rank_history")}
    assert columns == {
        "id", "match_id", "user_id", "placement", "rp_before", "placement_rp_change", "rp_change", "rp_after", "created_at",
        "tier_before", "division_before", "tier_after", "division_after",
    }
    uniques = [u["column_names"] for u in inspector.get_unique_constraints("rank_history")]
    assert ["match_id", "user_id"] in uniques


def test_register_starts_bronze_one(client, db_reset):
    response = client.post("/api/v1/auth/register", json={"username": "newbie", "password": "password123"})
    assert response.status_code == 201
    user_id = response.json()["id"]
    assert rp_of([user_id]) == [0]
    body = client.get("/users/me/rank", headers=headers(user_id)).json()
    assert body == {
        "user_id": user_id, "total_rp": 0, "tier": "BRONZE", "division": 1,
        "next_tier": "BRONZE", "next_division": 2, "rp_to_next": 100, "ranking_position": None,
    }


def test_four_player_match_awards_rp(db_reset):
    ids = make_users(4, rps=[0, 100, 300, 1505])
    room = finished_room(ids, {ids[0]: 9, ids[1]: 7, ids[2]: 5, ids[3]: 1})
    save(room)
    order = [r["user_id"] for r in room.result["results"]]
    assert order == ids
    assert rp_of(ids) == [30, 110, 290, 1475]
    assert sorted(history()) == sorted([
        (ids[0], 1, 0, 30, 30, 30),
        (ids[1], 2, 100, 10, 10, 110),
        (ids[2], 3, 300, -10, -10, 290),
        (ids[3], 4, 1505, -30, -30, 1475),
    ])


def test_placement_follows_quick_sort_result(db_reset, monkeypatch):
    from app import results as results_module

    calls = []
    original = results_module.quick_sort
    monkeypatch.setattr(results_module, "quick_sort", lambda arr, low, high: calls.append(1) or original(arr, low, high))
    ids = make_users(4)
    room = finished_room(ids, {ids[2]: 8, ids[0]: 6, ids[3]: 6})
    save(room)
    assert calls
    placements = {uid: placement for uid, placement, *_ in history()}
    assert placements == {r["user_id"]: r["rank"] for r in room.result["results"]}
    assert [r["user_id"] for r in room.result["results"]] == [ids[2], ids[0], ids[3], ids[1]]


def test_new_users_without_row_start_at_zero(db_reset):
    ids = make_users(4)
    assert rp_of(ids) == [None] * 4
    save(finished_room(ids, {ids[3]: 5}))
    assert rp_of(ids) == [10, 0, 0, 30]


def test_two_player_match_no_rp(db_reset):
    ids = make_users(2, rps=[500, 500])
    save(finished_room(ids, {ids[0]: 3}))
    assert rp_of(ids) == [500, 500]
    assert history() == []


def test_invalid_result_saved_without_rp(db_reset):
    ids = make_users(4, rps=[500] * 4)
    room = finished_room(ids)
    room.result["results"][1]["rank"] = 1
    save(room)
    with SessionLocal() as db:
        assert db.get(Match, room.room_id) is not None
    assert rp_of(ids) == [500] * 4
    assert history() == []


def test_duplicate_save_awards_once(db_reset):
    ids = make_users(4, rps=[500] * 4)
    room = finished_room(ids)
    for _ in range(3):
        save(room)
    assert rp_of(ids) == [530, 510, 490, 470]
    assert len(history()) == 4


def test_concurrent_saves_award_once(db_reset):
    ids = make_users(4, rps=[500] * 4)
    room = finished_room(ids)
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
    assert rp_of(ids) == [530, 510, 490, 470]
    assert len(history()) == 4


def test_rank_failure_rolls_back_match(db_reset, monkeypatch):
    ids = make_users(4, rps=[500] * 4)
    room = finished_room(ids)

    def broken(db, match_id, results):
        raise RuntimeError("rank failure")

    monkeypatch.setattr(ranks, "settle_match", broken)
    with pytest.raises(RuntimeError):
        save(room)
    with SessionLocal() as db:
        assert db.get(Match, room.room_id) is None
    assert rp_of(ids) == [500] * 4
    monkeypatch.undo()
    save(room)
    assert rp_of(ids) == [530, 510, 490, 470]


def test_history_matches_rp_over_many_matches(client, db_reset):
    ids = make_users(4, rps=[20, 1490, 1500, 0])
    orders = [ids, ids[::-1], [ids[2], ids[0], ids[3], ids[1]], ids]
    for order in orders:
        settle(order)
    with SessionLocal() as db:
        for uid in ids:
            rows = db.scalars(select(RankHistory).where(RankHistory.user_id == uid).order_by(RankHistory.id)).all()
            assert all(a.rp_after == b.rp_before for a, b in zip(rows, rows[1:]))
            assert rows[-1].rp_after == db.get(UserRank, uid).total_rp
            assert all(r.rp_after == r.rp_before + r.rp_change for r in rows)
    body = client.get("/users/me/rank/history?limit=2", headers=headers(ids[3])).json()
    assert len(body) == 2
    assert body[0]["rp_after"] == rp_of([ids[3]])[0]
    assert {"match_id", "placement", "rp_before", "placement_rp_change", "rp_change", "rp_after", "created_at"} == set(body[0])


def test_rp_kept_after_reconnect(client, db_reset, now):
    ids = make_users(4, rps=[500] * 4)
    sockets = [client.websocket_connect(WS_URL) for _ in ids]
    try:
        for ws, uid in zip(sockets, ids):
            ws.__enter__()
            auth(ws, uid)
        for ws in sockets:
            ws.send_json({"type": "queue_join"})
            assert ws.receive_json()["type"] == "queue_waiting"
        assert wait_until(lambda: get_player_room(ids[0]) is not None)
        room = get_player_room(ids[0])
        sockets[0].__exit__(None, None, None)
        now[0] = 320
        result = receive_until(sockets[1], "game_result")
        assert wait_until(lambda: room.result_saved)
        expected = [500 + ranks.PLACEMENT_RP[r["rank"]] for r in sorted(result["results"], key=lambda r: ids.index(r["user_id"]))]
        assert rp_of(ids) == expected

        with client.websocket_connect(WS_URL) as ws:
            auth(ws, ids[0])
            assert receive_until(ws, "game_result") == result
        assert rp_of(ids) == expected
        assert len(history()) == 4
    finally:
        for ws in sockets[1:]:
            ws.__exit__(None, None, None)


def test_ranking_master_only_sorted(client, db_reset):
    rps = [1499, 1500, 2000, 1700, 0, 1700]
    reached = {3: BASE_TIME + timedelta(seconds=5), 5: BASE_TIME}
    ids = make_users(6, rps=rps, reached=reached)
    body = client.get("/rankings", headers=headers(ids[0])).json()
    assert body["total"] == 4
    assert [(e["user_id"], e["position"], e["total_rp"], e["tier"]) for e in body["rankings"]] == [
        (ids[2], 1, 2000, "CHALLENGER"),
        (ids[5], 2, 1700, "MASTER"),
        (ids[3], 3, 1700, "MASTER"),
        (ids[1], 4, 1500, "MASTER"),
    ]
    assert body["rankings"][0]["display_name"] == "rank2"
    assert client.get("/users/me/rank", headers=headers(ids[3])).json()["ranking_position"] == 3
    assert client.get("/users/me/rank", headers=headers(ids[0])).json()["ranking_position"] is None
    assert client.get("/rankings").status_code == 401


def test_same_rp_and_time_ordered_by_user_id(db_reset):
    ids = make_users(3, rps=[1600, 1600, 1600])
    assert [e["user_id"] for e in ranking()["rankings"]] == sorted(ids)


def test_ranking_pagination(db_reset):
    ids = make_users(5, rps=[1500, 1600, 1700, 1800, 1900])
    page = ranking(limit=2, offset=2)
    assert page["total"] == 5
    assert [(e["position"], e["user_id"]) for e in page["rankings"]] == [(3, ids[2]), (4, ids[1])]


def test_challenger_requires_1800(db_reset):
    ids = make_users(3, rps=[1799, 1500, 1800])
    assert [(e["user_id"], e["tier"]) for e in ranking()["rankings"]] == [
        (ids[2], "CHALLENGER"), (ids[0], "MASTER"), (ids[1], "MASTER"),
    ]
    assert [e["user_id"] for e in challengers()["rankings"]] == [ids[2]]


def test_top_100_limit_and_101st_is_master(db_reset):
    ids = make_users(102, rps=[3000 - i for i in range(102)])
    body = ranking()
    assert len(body["rankings"]) == 100 and body["total"] == 102
    rest = ranking(limit=10, offset=100)["rankings"]
    assert [(e["user_id"], e["position"], e["tier"]) for e in rest] == [
        (ids[100], 101, "MASTER"), (ids[101], 102, "MASTER"),
    ]
    top = challengers()
    assert top["total"] == 100
    assert [e["user_id"] for e in top["rankings"]] == ids[:100]
    assert all(e["tier"] == "CHALLENGER" for e in top["rankings"])


def test_boundary_tie_at_100(db_reset):
    rps = [3000 - i for i in range(98)] + [1800] * 4
    reached = {98: BASE_TIME + timedelta(seconds=1), 99: BASE_TIME, 100: BASE_TIME, 101: BASE_TIME + timedelta(seconds=1)}
    ids = make_users(102, rps=rps, reached=reached)
    tail = ranking(limit=4, offset=98)["rankings"]
    expected_tail = sorted(ids[99:101]) + sorted([ids[98], ids[101]])
    assert [e["user_id"] for e in tail] == expected_tail
    assert [e["tier"] for e in tail] == ["CHALLENGER", "CHALLENGER", "MASTER", "MASTER"]
    for _ in range(3):
        assert [e["user_id"] for e in ranking(limit=4, offset=98)["rankings"]] == expected_tail


def test_challenger_pushed_out_by_new_user(db_reset):
    ids = make_users(104, rps=[1900 - i for i in range(100)] + [1790, 1500, 1500, 1500])
    last = ids[99]
    entry = ranking(limit=1, offset=99)["rankings"][0]
    assert (entry["user_id"], entry["total_rp"], entry["tier"]) == (last, 1801, "CHALLENGER")
    settle([ids[100], ids[101], ids[102], ids[103]])
    assert rp_of([ids[100]]) == [1820]
    entries = {e["user_id"]: e for e in ranking(limit=100, offset=0)["rankings"] + ranking(limit=10, offset=100)["rankings"]}
    assert entries[ids[100]]["tier"] == "CHALLENGER"
    assert (entries[last]["position"], entries[last]["tier"]) == (101, "MASTER")
    assert last not in [e["user_id"] for e in challengers()["rankings"]]


def test_challenger_demoted_by_rp_loss(client, db_reset):
    ids = make_users(4, rps=[1500, 1500, 1500, 1810])
    assert client.get("/users/me/rank", headers=headers(ids[3])).json()["tier"] == "CHALLENGER"
    settle(ids)
    body = client.get("/users/me/rank", headers=headers(ids[3])).json()
    assert (body["total_rp"], body["tier"], body["ranking_position"]) == (1780, "MASTER", 1)
    assert body["rp_to_next"] == 20
    assert challengers()["rankings"] == []


def test_master_drops_to_diamond_three(client, db_reset):
    ids = make_users(4, rps=[1500, 1500, 1500, 1510])
    settle(ids)
    body = client.get("/users/me/rank", headers=headers(ids[3])).json()
    assert (body["total_rp"], body["tier"], body["division"], body["ranking_position"]) == (1480, "DIAMOND", 3, None)
    assert [e["user_id"] for e in ranking()["rankings"]] == [ids[0], ids[1]]
