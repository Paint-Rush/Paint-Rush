import threading

import pytest
from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.levels import add_experience
from app.models import User, UserLevel, UserWallet
from app.wallets import MAX_INT, InsufficientGold, add_gold, spend_gold
from tests.test_game_engine import client, now
from tests.test_user_deck import headers


@pytest.fixture(autouse=True)
def db_reset():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def make_users(count, golds=None):
    with SessionLocal() as db:
        rows = [User(username=f"gold{i}", password_hash="unused") for i in range(count)]
        db.add_all(rows)
        db.flush()
        for row, gold in zip(rows, golds or []):
            if gold is not None:
                db.add(UserWallet(user_id=row.id, gold=gold))
        db.commit()
        return [row.id for row in rows]


def gold_of(user_id):
    with SessionLocal() as db:
        row = db.get(UserWallet, user_id)
        return None if row is None else row.gold


def earn(user_id, amount):
    with SessionLocal() as db:
        result = add_gold(db, user_id, amount)
        db.commit()
    return result


def spend(user_id, amount):
    with SessionLocal() as db:
        result = spend_gold(db, user_id, amount)
        db.commit()
    return result


BAD_TYPES = [1.0, 1.5, "10", None, True, False, [10]]


def test_add_gold():
    [uid] = make_users(1, [100])
    assert earn(uid, 50) == {"user_id": uid, "gold_earned": 50, "gold_before": 100, "gold_after": 150}
    assert gold_of(uid) == 150


def test_add_gold_accumulates_and_zero():
    [uid] = make_users(1, [0])
    for amount in (10, 20, 30):
        earn(uid, amount)
    assert gold_of(uid) == 60
    assert earn(uid, 0) == {"user_id": uid, "gold_earned": 0, "gold_before": 60, "gold_after": 60}
    assert gold_of(uid) == 60


def test_add_gold_creates_missing_wallet():
    [uid] = make_users(1)
    assert gold_of(uid) is None
    assert earn(uid, 25)["gold_before"] == 0
    assert gold_of(uid) == 25


@pytest.mark.parametrize("amount", [-1, -100, *BAD_TYPES])
def test_add_gold_rejects_bad_amount(amount):
    [uid] = make_users(1, [10])
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            add_gold(db, uid, amount)
    assert gold_of(uid) == 10


@pytest.mark.parametrize("user_id", [999999, 0, "1", None, True])
def test_add_gold_rejects_unknown_user(user_id):
    make_users(1)
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            add_gold(db, user_id, 10)
    with SessionLocal() as db:
        assert db.scalars(select(UserWallet)).all() == []


def test_add_gold_max_int():
    [uid] = make_users(1, [MAX_INT - 10])
    assert earn(uid, 10)["gold_after"] == MAX_INT
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            add_gold(db, uid, 1)
    assert gold_of(uid) == MAX_INT


def test_spend_gold():
    [uid] = make_users(1, [100])
    assert spend(uid, 30) == {"user_id": uid, "gold_spent": 30, "gold_before": 100, "gold_after": 70}
    assert spend(uid, 70)["gold_after"] == 0
    assert gold_of(uid) == 0


def test_spend_gold_insufficient():
    [uid] = make_users(1, [50])
    with SessionLocal() as db:
        with pytest.raises(InsufficientGold, match="골드가 부족합니다"):
            spend_gold(db, uid, 51)
        db.commit()
    assert gold_of(uid) == 50


def test_spend_without_wallet_changes_nothing():
    [uid] = make_users(1)
    with SessionLocal() as db:
        with pytest.raises(InsufficientGold):
            spend_gold(db, uid, 1)
        db.commit()
    assert gold_of(uid) is None


@pytest.mark.parametrize("amount", [0, -1, -50, *BAD_TYPES])
def test_spend_gold_rejects_bad_amount(amount):
    [uid] = make_users(1, [100])
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            spend_gold(db, uid, amount)
        db.commit()
    assert gold_of(uid) == 100


def test_spend_gold_rejects_unknown_user():
    make_users(1, [100])
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            spend_gold(db, 999999, 10)


def test_failed_spend_keeps_balance_for_next_operations():
    [uid] = make_users(1, [40])
    with SessionLocal() as db:
        with pytest.raises(InsufficientGold):
            spend_gold(db, uid, 100)
        assert add_gold(db, uid, 5)["gold_before"] == 40
        db.commit()
    assert gold_of(uid) == 45


def test_rollback_cancels_add_and_spend():
    [uid] = make_users(1, [100])
    with SessionLocal() as db:
        add_gold(db, uid, 500)
        assert db.get(UserWallet, uid).gold == 600
        assert gold_of(uid) == 100
        db.rollback()
    assert gold_of(uid) == 100
    with SessionLocal() as db:
        spend_gold(db, uid, 60)
        db.rollback()
    assert gold_of(uid) == 100


def test_closing_without_commit_discards():
    ids = make_users(2, [100])
    with SessionLocal() as db:
        add_gold(db, ids[0], 10)
        spend_gold(db, ids[0], 20)
        add_gold(db, ids[1], 30)
    assert (gold_of(ids[0]), gold_of(ids[1])) == (100, None)


def test_other_failure_rolls_back_gold_and_experience():
    ids = make_users(2, [100, 0])
    with SessionLocal() as db:
        add_gold(db, ids[0], 50)
        add_experience(db, ids[0], 300)
        with pytest.raises(InsufficientGold):
            spend_gold(db, ids[1], 10)
        db.rollback()
    assert gold_of(ids[0]) == 100
    with SessionLocal() as db:
        assert db.get(UserLevel, ids[0]) is None


def test_gold_and_experience_commit_together():
    [uid] = make_users(1, [0])
    with SessionLocal() as db:
        add_gold(db, uid, 70)
        add_experience(db, uid, 120)
        db.commit()
    assert gold_of(uid) == 70
    with SessionLocal() as db:
        assert (db.get(UserLevel, uid).level, db.get(UserLevel, uid).total_experience) == (2, 120)


def test_concurrent_add_and_spend_consistent():
    [uid] = make_users(1, [1000])
    barrier = threading.Barrier(10)
    errors = []
    outcomes = {"spent": 0, "refused": 0}
    lock = threading.Lock()

    def adder():
        barrier.wait()
        try:
            for _ in range(10):
                earn(uid, 5)
        except Exception as e:
            errors.append(e)

    def spender():
        barrier.wait()
        for _ in range(20):
            try:
                spend(uid, 40)
                with lock:
                    outcomes["spent"] += 1
            except InsufficientGold:
                with lock:
                    outcomes["refused"] += 1
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=adder) for _ in range(5)] + [threading.Thread(target=spender) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert outcomes["spent"] + outcomes["refused"] == 100
    assert gold_of(uid) == 1000 + 5 * 10 * 5 - 40 * outcomes["spent"]
    assert gold_of(uid) >= 0
    assert outcomes["refused"] > 0


def test_wallet_api(client):
    ids = make_users(2, [1500, None])
    assert client.get("/users/me/wallet", headers=headers(ids[0])).json() == {"user_id": ids[0], "gold": 1500}
    assert client.get("/users/me/wallet", headers=headers(ids[1])).json() == {"user_id": ids[1], "gold": 0}
    assert gold_of(ids[1]) is None


def test_wallet_api_requires_auth(client):
    make_users(1, [10])
    assert client.get("/users/me/wallet").status_code == 401
    assert client.get("/users/me/wallet", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_wallet_api_only_own_wallet(client):
    ids = make_users(2, [111, 222])
    for query in ("?user_id={}", "/{}"):
        response = client.get("/users/me/wallet" + query.format(ids[1]), headers=headers(ids[0]))
        assert response.status_code == 404 or response.json() == {"user_id": ids[0], "gold": 111}


def test_no_public_gold_change_api(client):
    [uid] = make_users(1, [10])
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/users/me/wallet", headers=headers(uid)).status_code == 405
    assert gold_of(uid) == 10


def test_register_and_login_still_create_wallet(client):
    response = client.post("/api/v1/auth/register", json={"username": "goldnew", "password": "password123"})
    assert response.status_code == 201
    user_id = response.json()["id"]
    assert gold_of(user_id) == 0
    token = client.post("/api/v1/auth/login", json={"username": "goldnew", "password": "password123"}).json()["access_token"]
    assert client.get("/users/me/wallet", headers={"Authorization": f"Bearer {token}"}).json() == {"user_id": user_id, "gold": 0}
