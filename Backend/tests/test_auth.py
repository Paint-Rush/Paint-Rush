##테스트 스크립트
import os
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app

URL = "/api/v1/auth"


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:
        yield c


def test_register_and_login(client):
    r = client.post(
        f"{URL}/register",
        json={"username": "Painter123", "password": "password1234"},
    )
    assert r.status_code == 201
    assert r.json() == {"id": 1, "username": "painter123"}

    r = client.post(
        f"{URL}/login",
        json={"username": "painter123", "password": "password1234"},
    )
    assert r.status_code == 200

    body = r.json()
    assert body["token_type"] == "bearer"

    payload = jwt.decode(
        body["access_token"],
        os.environ["JWT_SECRET_KEY"],
        algorithms=["HS256"],
    )
    assert payload["sub"] == "1"
    assert abs(payload["exp"] - time.time() - 86400) < 60


def test_duplicate_username(client):
    data = {"username": "painter123", "password": "password1234"}
    client.post(f"{URL}/register", json=data)

    r = client.post(
        f"{URL}/register",
        json={**data, "username": "PAINTER123"},
    )
    assert r.status_code == 409


def test_login_failure(client):
    client.post(
        f"{URL}/register",
        json={"username": "painter123", "password": "password1234"},
    )

    assert client.post(
        f"{URL}/login",
        json={"username": "painter123", "password": "wrongpass1"},
    ).status_code == 401

    assert client.post(
        f"{URL}/login",
        json={"username": "nobody", "password": "password1234"},
    ).status_code == 401


@pytest.mark.parametrize("data", [
    {"username": "ab", "password": "password1234"},
    {"username": "a" * 21, "password": "password1234"},
    {"username": "bad-name", "password": "password1234"},
    {"username": "painter123", "password": "short"},
    {"username": "painter123"},
])
def test_invalid_input(client, data):
    assert client.post(
        f"{URL}/register",
        json=data,
    ).status_code == 422
