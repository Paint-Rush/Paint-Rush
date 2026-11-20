import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.database import Base, SessionLocal, engine
from app.models import Match, MatchReward, User, UserLevel, UserRank, UserWallet
from tests.test_game_engine import client, now

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def db_reset():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def make_users(count, prefix="reward"):
    with SessionLocal() as db:
        rows = [User(username=f"{prefix}{i}", password_hash="unused") for i in range(count)]
        db.add_all(rows)
        db.commit()
        return [row.id for row in rows]


def make_match(winner_id):
    match_id = uuid.uuid4()
    with SessionLocal() as db:
        db.add(Match(id=match_id, map_id="default", started_at=BASE_TIME, ended_at=BASE_TIME, winner_id=winner_id))
        db.commit()
    return match_id


def reward(match_id, user_id, **change):
    values = {
        "match_id": match_id, "user_id": user_id, "placement": 1,
        "gold_earned": 50, "gold_before": 100, "gold_after": 150,
        "experience_earned": 30, "experience_before": 90, "experience_after": 120,
        "level_before": 1, "level_after": 2,
    }
    return MatchReward(**{**values, **change})


def save(*rows):
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()


def rejected(*rows):
    with pytest.raises(IntegrityError):
        save(*rows)


def test_wallet_defaults_to_zero_gold():
    [uid] = make_users(1)
    save(UserWallet(user_id=uid))
    with SessionLocal() as db:
        assert db.get(UserWallet, uid).gold == 0
        db.execute(UserWallet.__table__.insert().values(user_id=make_users(1, prefix="raw")[0]))
        db.commit()
        assert set(db.scalars(select(UserWallet.gold))) == {0}


def test_wallet_rejects_negative_gold():
    [uid] = make_users(1)
    rejected(UserWallet(user_id=uid, gold=-1))


def test_one_wallet_per_user():
    [uid] = make_users(1)
    save(UserWallet(user_id=uid, gold=10))
    rejected(UserWallet(user_id=uid, gold=20))
    with SessionLocal() as db:
        assert db.get(UserWallet, uid).gold == 10


def test_wallet_requires_existing_user():
    rejected(UserWallet(user_id=999999, gold=0))


def test_match_reward_saved():
    ids = make_users(2)
    match_id = make_match(ids[0])
    save(reward(match_id, ids[0]), reward(match_id, ids[1], placement=2, gold_earned=0, gold_before=0, gold_after=0,
                                          experience_earned=0, experience_before=0, experience_after=0, level_after=1))
    with SessionLocal() as db:
        row = db.get(MatchReward, (match_id, ids[0]))
        assert (row.placement, row.gold_before, row.gold_after, row.experience_after, row.level_after) == (1, 100, 150, 120, 2)
        assert row.created_at is not None


def test_duplicate_reward_rejected():
    [uid] = make_users(1)
    match_id = make_match(uid)
    save(reward(match_id, uid))
    rejected(reward(match_id, uid, placement=2))


@pytest.mark.parametrize("placement", [0, 5, -1])
def test_invalid_placement_rejected(placement):
    [uid] = make_users(1)
    rejected(reward(make_match(uid), uid, placement=placement))


@pytest.mark.parametrize("change", [
    {"gold_earned": -10, "gold_after": 90},
    {"gold_before": -1, "gold_earned": 1, "gold_after": 0},
    {"experience_earned": -5, "experience_after": 85},
    {"experience_before": -10, "experience_earned": 10, "experience_after": 0},
])
def test_negative_rewards_rejected(change):
    [uid] = make_users(1)
    rejected(reward(make_match(uid), uid, **change))


@pytest.mark.parametrize("change", [
    {"gold_after": 151},
    {"gold_after": 149},
    {"experience_after": 121},
    {"experience_after": 119},
    {"level_before": 0},
    {"level_after": 0},
])
def test_inconsistent_rewards_rejected(change):
    [uid] = make_users(1)
    rejected(reward(make_match(uid), uid, **change))


def test_reward_requires_existing_match_and_user():
    [uid] = make_users(1)
    rejected(reward(uuid.uuid4(), uid))
    rejected(reward(make_match(uid), 999999))


def test_schema_matches_model():
    inspector = inspect(engine)
    for model in (UserWallet, MatchReward):
        table = model.__table__
        actual = {c["name"]: c for c in inspector.get_columns(table.name)}
        assert set(actual) == {c.name for c in table.columns}
        for column in table.columns:
            assert actual[column.name]["nullable"] is column.nullable is False
    assert inspector.get_pk_constraint("user_wallets")["constrained_columns"] == ["user_id"]
    assert inspector.get_pk_constraint("match_rewards")["constrained_columns"] == ["match_id", "user_id"]
    assert {(tuple(fk["constrained_columns"]), fk["referred_table"]) for fk in inspector.get_foreign_keys("match_rewards")} == {
        (("match_id",), "matches"), (("user_id",), "users"),
    }
    assert ["user_id"] in [i["column_names"] for i in inspector.get_indexes("match_rewards")]
    checks = {c["name"] for c in inspector.get_check_constraints("match_rewards")}
    assert {"ck_match_rewards_placement", "ck_match_rewards_gold_change", "ck_match_rewards_experience_change"} <= checks
    assert "ck_user_wallets_gold" in {c["name"] for c in inspector.get_check_constraints("user_wallets")}
    gold = {c["name"]: c for c in inspector.get_columns("user_wallets")}["gold"]
    assert "0" in str(gold["default"])


def test_register_creates_wallet_and_rank(client):
    response = client.post("/api/v1/auth/register", json={"username": "walletuser", "password": "password123"})
    assert response.status_code == 201
    user_id = response.json()["id"]
    with SessionLocal() as db:
        assert db.get(UserWallet, user_id).gold == 0
        assert db.get(UserRank, user_id).total_rp == 0
        assert db.get(UserLevel, user_id) is None
    login = client.post("/api/v1/auth/login", json={"username": "walletuser", "password": "password123"})
    assert login.status_code == 200 and login.json()["token_type"] == "bearer"


def test_failed_register_creates_no_wallet(client):
    assert client.post("/api/v1/auth/register", json={"username": "dupe_user", "password": "password123"}).status_code == 201
    assert client.post("/api/v1/auth/register", json={"username": "dupe_user", "password": "password123"}).status_code == 409
    with SessionLocal() as db:
        assert len(db.scalars(select(UserWallet)).all()) == 1


def test_register_rolls_back_wallet_on_failure(client, monkeypatch):
    from app import auth

    original = auth.UserWallet

    def broken(**kwargs):
        return original(gold=-1, **kwargs)

    monkeypatch.setattr(auth, "UserWallet", broken)
    response = client.post("/api/v1/auth/register", json={"username": "rollback1", "password": "password123"})
    assert response.status_code == 409
    with SessionLocal() as db:
        assert db.scalars(select(User).where(User.username == "rollback1")).first() is None
        assert db.scalars(select(UserRank)).all() == []
        assert db.scalars(select(UserWallet)).all() == []
