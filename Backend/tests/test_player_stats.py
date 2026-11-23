import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.database import SessionLocal, engine
from app.models import Match, MatchPlayerResult, User
from tests.test_game_engine import client, now
from tests.test_match_history import snapshot
from tests.test_result_db import finished_room, save
from tests.test_user_deck import headers
from tests.test_websocket import clear_state, make_token

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
URL = "/users/me/stats"
EMPTY = {
    "total_matches": 0, "wins": 0, "win_rate": 0, "average_placement": None,
    "total_kills": 0, "total_deaths": 0, "kd_ratio": None, "total_painted": 0,
    "average_painted_tiles": None, "best_painted_tiles": 0, "total_cards_used": 0, "total_cost_used": 0,
}
FILLER = {"painted_tiles": 50, "total_painted": 500, "kills": 40, "deaths": 30, "cards_used": 70, "cost_used": 90}


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def ids(client):
    with SessionLocal() as db:
        rows = [User(username=f"stats{i}", password_hash="unused") for i in range(6)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def play(order, me=None, values=None, winner=None, ended_at=BASE_TIME):
    match_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Match(
            id=match_id, map_id="default", started_at=ended_at - timedelta(seconds=300), ended_at=ended_at,
            winner_id=order[0] if winner is None else winner,
        ))
        db.flush()
        db.add_all(
            MatchPlayerResult(
                match_id=match_id, user_id=uid, rank=rank, matchmaking_order=rank - 1,
                **(values if uid == me and values else FILLER),
            )
            for rank, uid in enumerate(order, 1)
        )
        db.commit()
    return match_id


def line(painted_tiles=0, total_painted=0, kills=0, deaths=0, cards_used=0, cost_used=0):
    return {
        "painted_tiles": painted_tiles, "total_painted": total_painted, "kills": kills,
        "deaths": deaths, "cards_used": cards_used, "cost_used": cost_used,
    }


def four(ids, me, placement):
    others = [uid for uid in ids[:5] if uid != me][:3]
    return others[:placement - 1] + [me] + others[placement - 1:]


def get(client, user_id):
    response = client.get(URL, headers=headers(user_id))
    assert response.status_code == 200, response.text
    return response.json()


def test_user_without_matches(client, ids):
    assert get(client, ids[0]) == EMPTY


def test_two_player_only_user_has_empty_stats(client, ids):
    for i in range(3):
        play([ids[0], ids[1]], ids[0], line(30, 40, 5, 1, 7, 9), ended_at=BASE_TIME + timedelta(minutes=i))
    assert get(client, ids[0]) == EMPTY
    assert get(client, ids[1]) == EMPTY
    assert client.get("/users/me/matches", headers=headers(ids[0])).json()["total"] == 3


def test_four_player_only_user(client, ids):
    me = ids[0]
    play(four(ids, me, 1), me, line(40, 60, 3, 1, 10, 25))
    play(four(ids, me, 3), me, line(20, 35, 1, 2, 8, 20), ended_at=BASE_TIME + timedelta(hours=1))
    assert get(client, me) == {
        "total_matches": 2, "wins": 1, "win_rate": 50.0, "average_placement": 2.0,
        "total_kills": 4, "total_deaths": 3, "kd_ratio": 1.33, "total_painted": 95,
        "average_painted_tiles": 30.0, "best_painted_tiles": 40, "total_cards_used": 18, "total_cost_used": 45,
    }


def test_mixed_two_and_four_player_matches(client, ids):
    me = ids[0]
    for i in range(10):
        play([me, ids[1]], me, line(90, 900, 9, 0, 99, 99), ended_at=BASE_TIME + timedelta(minutes=i))
    for i in range(5):
        play(four(ids, me, i % 4 + 1), me, line(10 + i, 20, 2, 1, 3, 4), ended_at=BASE_TIME + timedelta(hours=1, minutes=i))
    history = client.get("/users/me/matches?limit=50", headers=headers(me)).json()
    assert history["total"] == 15 and len(history["matches"]) == 15
    assert sorted(m["player_count"] for m in history["matches"]) == [2] * 10 + [4] * 5
    body = get(client, me)
    assert body["total_matches"] == 5
    assert body["wins"] == 2 and body["win_rate"] == 40.0
    assert body["average_placement"] == 2.2
    assert body["total_kills"] == 10 and body["total_deaths"] == 5 and body["kd_ratio"] == 2.0
    assert body["total_painted"] == 100
    assert body["average_painted_tiles"] == 12.0 and body["best_painted_tiles"] == 14
    assert body["total_cards_used"] == 15 and body["total_cost_used"] == 20


@pytest.mark.parametrize("size", [1, 2, 3, 5])
def test_non_four_player_matches_excluded(client, ids, size):
    me = ids[0]
    play([me] + [uid for uid in ids[1:6]][:size - 1], me, line(70, 80, 6, 2, 9, 9))
    play(four(ids, me, 2), me, line(10, 15, 1, 1, 2, 3), ended_at=BASE_TIME + timedelta(hours=1))
    assert get(client, me) == {
        "total_matches": 1, "wins": 0, "win_rate": 0.0, "average_placement": 2.0,
        "total_kills": 1, "total_deaths": 1, "kd_ratio": 1.0, "total_painted": 15,
        "average_painted_tiles": 10.0, "best_painted_tiles": 10, "total_cards_used": 2, "total_cost_used": 3,
    }


def test_wins_use_match_winner(client, ids):
    me = ids[0]
    play(four(ids, me, 1), me, line(), winner=ids[1])
    play(four(ids, me, 2), me, line(), winner=me, ended_at=BASE_TIME + timedelta(hours=1))
    play(four(ids, me, 1), me, line(), ended_at=BASE_TIME + timedelta(hours=2))
    body = get(client, me)
    assert body["total_matches"] == 3 and body["wins"] == 2 and body["win_rate"] == 66.67
    assert body["average_placement"] == 1.33


def test_zero_deaths_gives_null_kd(client, ids):
    me = ids[0]
    play(four(ids, me, 1), me, line(kills=4, deaths=0))
    body = get(client, me)
    assert body["total_kills"] == 4 and body["total_deaths"] == 0 and body["kd_ratio"] is None


def test_kd_without_kills_is_zero(client, ids):
    play(four(ids, ids[0], 4), ids[0], line(kills=0, deaths=3))
    assert get(client, ids[0])["kd_ratio"] == 0.0


def test_two_decimal_rounding(client, ids):
    me = ids[0]
    play(four(ids, me, 1), me, line(painted_tiles=10, kills=107, deaths=40))
    play(four(ids, me, 2), me, line(painted_tiles=11), ended_at=BASE_TIME + timedelta(hours=1))
    play(four(ids, me, 2), me, line(painted_tiles=11), ended_at=BASE_TIME + timedelta(hours=2))
    body = get(client, me)
    assert body["win_rate"] == 33.33
    assert body["average_placement"] == 1.67
    assert body["average_painted_tiles"] == 10.67
    assert body["kd_ratio"] == 2.68


def test_painted_tiles_and_total_painted_kept_apart(client, ids):
    me = ids[0]
    play(four(ids, me, 1), me, line(painted_tiles=25, total_painted=90))
    play(four(ids, me, 2), me, line(painted_tiles=45, total_painted=60), ended_at=BASE_TIME + timedelta(hours=1))
    body = get(client, me)
    assert body["total_painted"] == 150
    assert body["average_painted_tiles"] == 35.0
    assert body["best_painted_tiles"] == 45


def test_other_players_not_mixed(client, ids):
    me = ids[0]
    play(four(ids, me, 2), me, line(5, 6, 1, 1, 2, 3))
    play([ids[1], ids[2], ids[3], ids[4]], ended_at=BASE_TIME + timedelta(hours=1))
    play([ids[1], ids[5]], ended_at=BASE_TIME + timedelta(hours=2))
    assert get(client, me) == {
        "total_matches": 1, "wins": 0, "win_rate": 0.0, "average_placement": 2.0,
        "total_kills": 1, "total_deaths": 1, "kd_ratio": 1.0, "total_painted": 6,
        "average_painted_tiles": 5.0, "best_painted_tiles": 5, "total_cards_used": 2, "total_cost_used": 3,
    }
    other = get(client, ids[1])
    assert other["total_matches"] == 2 and other["wins"] == 2
    assert other["total_kills"] == FILLER["kills"] * 2
    assert get(client, ids[5]) == EMPTY


@pytest.mark.parametrize("header", [None, "Bearer invalid", "Basic abc"])
def test_requires_authentication(client, ids, header):
    response = client.get(URL, headers={"Authorization": header} if header else {})
    assert response.status_code == 401


def test_expired_or_forged_token_rejected(client, ids):
    expired = make_token(ids[0], expires=timedelta(seconds=-1))
    assert client.get(URL, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    forged = make_token(ids[0], key="y" * 40)
    assert client.get(URL, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_query_parameters_cannot_select_other_user(client, ids):
    play([ids[1], ids[2], ids[3], ids[4]], ids[1], line(9, 9, 9, 9, 9, 9))
    response = client.get(f"{URL}?user_id={ids[1]}", headers=headers(ids[0]))
    assert response.status_code == 200 and response.json() == EMPTY


def test_read_does_not_change_database(client, ids):
    play(four(ids, ids[0], 1), ids[0], line(5, 6, 1, 1, 2, 3))
    play([ids[0], ids[1]], ended_at=BASE_TIME + timedelta(hours=1))
    play([ids[0], ids[1], ids[2]], ended_at=BASE_TIME + timedelta(hours=2))
    before = snapshot()
    for uid in ids:
        get(client, uid)
    assert snapshot() == before


def test_saved_four_player_result_counted_once(client, ids, now):
    room = finished_room(ids[:4], {
        ids[0]: {"tiles": 3, "total_painted": 5, "kills": 1, "deaths": 2, "cards_used": 4, "cost_used": 7},
        ids[1]: {"tiles": 9, "total_painted": 12, "kills": 2},
        ids[2]: {"tiles": 6},
        ids[3]: {"tiles": 1},
    })
    save(room)
    expected = {
        "total_matches": 1, "wins": 0, "win_rate": 0.0, "average_placement": 3.0,
        "total_kills": 1, "total_deaths": 2, "kd_ratio": 0.5, "total_painted": 5,
        "average_painted_tiles": 3.0, "best_painted_tiles": 3, "total_cards_used": 4, "total_cost_used": 7,
    }
    assert get(client, ids[0]) == expected
    assert get(client, ids[1])["wins"] == 1 and get(client, ids[1])["win_rate"] == 100.0
    save(room)
    assert get(client, ids[0]) == expected
    assert client.get("/users/me/matches", headers=headers(ids[0])).json()["total"] == 1


def test_saved_two_player_result_excluded(client, ids, now):
    room = finished_room(ids[:2], {ids[0]: {"tiles": 8, "kills": 3}})
    save(room)
    assert get(client, ids[0]) == EMPTY
    history = client.get("/users/me/matches", headers=headers(ids[0])).json()
    assert history["total"] == 1 and history["matches"][0]["player_count"] == 2


def test_stats_use_single_aggregate_query(client, ids):
    me = ids[0]
    for i in range(6):
        play(four(ids, me, i % 4 + 1), me, line(i, i, 1, 1, 1, 1), ended_at=BASE_TIME + timedelta(minutes=i))
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "match_player_results" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        body = get(client, me)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert body["total_matches"] == 6
    assert len(statements) == 1
    sql = statements[0].lower()
    for keyword in ("count(", "sum(", "avg(", "max(", "round(", "group by", "having"):
        assert keyword in sql
