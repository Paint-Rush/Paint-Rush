import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from app.database import SessionLocal, engine
from app.models import Match, MatchPlayerResult, User
from app.results import load_match_record
from tests.test_game_engine import client, now
from tests.test_result_db import finished_room, save
from tests.test_user_deck import headers
from tests.test_websocket import clear_state, make_token

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
URL = "/users/me/matches"
STATS = ("painted_tiles", "total_painted", "kills", "deaths", "cards_used", "cost_used")


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


@pytest.fixture
def ids(client):
    with SessionLocal() as db:
        rows = [User(username=f"history{i}", password_hash="unused") for i in range(6)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def stats(seed):
    return {key: seed * 10 + index for index, key in enumerate(STATS)}


def add_match(user_ids, ended_at, winner_id=None, map_id="default", match_id=None):
    match_id = match_id or uuid.uuid4()
    with SessionLocal() as db:
        db.add(Match(
            id=match_id, map_id=map_id, started_at=ended_at - timedelta(seconds=300), ended_at=ended_at,
            winner_id=user_ids[0] if winner_id is None else winner_id,
        ))
        db.flush()
        db.add_all(
            MatchPlayerResult(match_id=match_id, user_id=uid, rank=rank, matchmaking_order=rank - 1, **stats(uid + rank))
            for rank, uid in enumerate(user_ids, 1)
        )
        db.commit()
    return match_id


def get(client, user_id, query=""):
    response = client.get(URL + query, headers=headers(user_id))
    assert response.status_code == 200, response.text
    return response.json()


def snapshot():
    with SessionLocal() as db:
        matches = [
            (m.id, m.map_id, m.started_at, m.ended_at, m.winner_id)
            for m in db.scalars(select(Match).order_by(Match.id))
        ]
        players = [
            tuple(getattr(p, key) for key in ("match_id", "user_id", "rank", "matchmaking_order", *STATS))
            for p in db.scalars(select(MatchPlayerResult).order_by(MatchPlayerResult.match_id, MatchPlayerResult.user_id))
        ]
    return matches, players


def test_user_without_matches_gets_empty_list(client, ids):
    add_match(ids[1:3], BASE_TIME)
    assert get(client, ids[0]) == {"total": 0, "matches": []}


def test_two_player_match(client, ids):
    match_id = add_match([ids[0], ids[1]], BASE_TIME, map_id="crossroads")
    winner = get(client, ids[0])
    loser = get(client, ids[1])
    assert winner == {"total": 1, "matches": [{
        "match_id": str(match_id),
        "map_id": "crossroads",
        "started_at": winner["matches"][0]["started_at"],
        "ended_at": winner["matches"][0]["ended_at"],
        "player_count": 2,
        "placement": 1,
        "is_winner": True,
        **stats(ids[0] + 1),
    }]}
    entry = winner["matches"][0]
    assert datetime.fromisoformat(entry["started_at"]) == BASE_TIME - timedelta(seconds=300)
    assert datetime.fromisoformat(entry["ended_at"]) == BASE_TIME
    assert loser == {"total": 1, "matches": [entry | {"placement": 2, "is_winner": False, **stats(ids[1] + 2)}]}


def test_four_player_match(client, ids):
    order = [ids[2], ids[0], ids[3], ids[1]]
    match_id = add_match(order, BASE_TIME, map_id="glass_garden")
    for placement, uid in enumerate(order, 1):
        body = get(client, uid)
        assert body["total"] == 1
        entry = body["matches"][0]
        assert entry["match_id"] == str(match_id) and entry["map_id"] == "glass_garden"
        assert entry["player_count"] == 4
        assert entry["placement"] == placement
        assert entry["is_winner"] is (placement == 1)
        assert {key: entry[key] for key in STATS} == stats(uid + placement)


def test_latest_match_first(client, ids):
    old = add_match(ids[:2], BASE_TIME)
    new = add_match(ids[:4], BASE_TIME + timedelta(hours=2))
    middle = add_match([ids[1], ids[0]], BASE_TIME + timedelta(hours=1))
    body = get(client, ids[0])
    assert [m["match_id"] for m in body["matches"]] == [str(new), str(middle), str(old)]
    assert [m["player_count"] for m in body["matches"]] == [4, 2, 2]
    assert [m["placement"] for m in body["matches"]] == [1, 2, 1]


def test_same_end_time_order_is_stable(client, ids):
    tied = [add_match(ids[:2], BASE_TIME) for _ in range(5)]
    later = add_match(ids[:2], BASE_TIME + timedelta(minutes=1))
    expected = [str(later)] + sorted(map(str, tied), reverse=True)
    for _ in range(3):
        assert [m["match_id"] for m in get(client, ids[0])["matches"]] == expected
    pages = [m["match_id"] for offset in range(0, 6, 2) for m in get(client, ids[0], f"?limit=2&offset={offset}")["matches"]]
    assert pages == expected


def test_pagination(client, ids):
    created = [add_match(ids[:2], BASE_TIME + timedelta(minutes=i)) for i in range(5)]
    expected = [str(match_id) for match_id in reversed(created)]
    assert [m["match_id"] for m in get(client, ids[0])["matches"]] == expected
    for limit, offset in [(2, 0), (2, 2), (2, 4), (3, 3), (1, 4), (50, 0)]:
        body = get(client, ids[0], f"?limit={limit}&offset={offset}")
        assert body["total"] == 5
        assert [m["match_id"] for m in body["matches"]] == expected[offset:offset + limit]
    assert get(client, ids[0], "?offset=5") == {"total": 5, "matches": []}
    assert get(client, ids[0], "?offset=100")["matches"] == []


def test_default_limit_is_twenty(client, ids):
    for i in range(23):
        add_match(ids[:2], BASE_TIME + timedelta(minutes=i))
    body = get(client, ids[0])
    assert body["total"] == 23 and len(body["matches"]) == 20


def test_limit_and_offset_applied_in_query(client, ids):
    for i in range(6):
        add_match(ids[:2], BASE_TIME + timedelta(minutes=i))
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "match_player_results" in statement and "ORDER BY" in statement:
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        body = get(client, ids[0], "?limit=2&offset=3")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(body["matches"]) == 2
    assert len(statements) == 1
    statement, parameters = statements[0]
    values = list(parameters.values() if isinstance(parameters, dict) else parameters)
    assert "LIMIT" in statement and "OFFSET" in statement
    assert 2 in values and 3 in values


def test_other_users_records_hidden(client, ids):
    mine = add_match([ids[0], ids[1]], BASE_TIME)
    add_match([ids[1], ids[2]], BASE_TIME + timedelta(hours=1))
    add_match([ids[2], ids[3], ids[4], ids[1]], BASE_TIME + timedelta(hours=2))
    body = get(client, ids[0])
    assert body["total"] == 1
    assert [m["match_id"] for m in body["matches"]] == [str(mine)]
    assert {key: body["matches"][0][key] for key in STATS} == stats(ids[0] + 1)
    assert get(client, ids[1])["total"] == 3
    assert get(client, ids[5]) == {"total": 0, "matches": []}
    assert get(client, ids[0], f"?user_id={ids[1]}")["total"] == 1


def test_stored_values_returned_without_correction(client, ids):
    match_id = add_match([ids[0], ids[1]], BASE_TIME, winner_id=ids[1])
    entry = get(client, ids[0])["matches"][0]
    assert entry["match_id"] == str(match_id)
    assert entry["placement"] == 1 and entry["is_winner"] is False
    assert get(client, ids[1])["matches"][0]["is_winner"] is True


@pytest.mark.parametrize("header", [None, "Bearer invalid", "Basic abc"])
def test_requires_authentication(client, ids, header):
    add_match(ids[:2], BASE_TIME)
    response = client.get(URL, headers={"Authorization": header} if header else {})
    assert response.status_code == 401


def test_expired_or_unknown_user_token_rejected(client, ids):
    expired = make_token(ids[0], expires=timedelta(seconds=-1))
    assert client.get(URL, headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert client.get(URL, headers={"Authorization": f"Bearer {make_token(ids[0], key='x' * 40)}"}).status_code == 401


@pytest.mark.parametrize("query", [
    "?limit=0", "?limit=51", "?limit=-1", "?limit=abc", "?limit=1.5",
    "?offset=-1", "?offset=abc", f"?offset={2**31}", f"?offset={10**30}",
])
def test_invalid_limit_or_offset_rejected(client, ids, query):
    add_match(ids[:2], BASE_TIME)
    assert client.get(URL + query, headers=headers(ids[0])).status_code == 422


def test_read_does_not_change_database(client, ids):
    add_match(ids[:4], BASE_TIME)
    add_match([ids[1], ids[0]], BASE_TIME + timedelta(hours=1), winner_id=ids[2])
    before = snapshot()
    for uid in ids:
        get(client, uid)
        get(client, uid, "?limit=1&offset=1")
    assert snapshot() == before


def test_saved_match_result_appears_in_history(client, ids, now):
    room = finished_room(ids[:4], {
        ids[0]: {"tiles": 3, "total_painted": 5, "kills": 1},
        ids[1]: {"tiles": 9, "total_painted": 12, "deaths": 2, "cards_used": 4, "cost_used": 11},
        ids[2]: {"tiles": 6},
        ids[3]: {"tiles": 1, "cards_used": 2},
    })
    save(room)
    with SessionLocal() as db:
        match, players = load_match_record(db, room.room_id)
    before = snapshot()
    for player in players:
        entry = get(client, player["user_id"])["matches"][0]
        assert entry["match_id"] == str(room.room_id) and entry["map_id"] == match["map_id"]
        assert datetime.fromisoformat(entry["started_at"]) == match["started_at"]
        assert datetime.fromisoformat(entry["ended_at"]) == match["ended_at"]
        assert entry["player_count"] == 4
        assert entry["placement"] == player["rank"]
        assert entry["is_winner"] is (player["user_id"] == match["winner_id"])
        assert {key: entry[key] for key in STATS} == {key: player[key] for key in STATS}
    assert match["winner_id"] == ids[1]
    assert snapshot() == before
    save(room)
    assert snapshot() == before
    assert get(client, ids[1])["total"] == 1
