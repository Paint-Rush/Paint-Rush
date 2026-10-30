## 테스트전 DB형식 확인
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

env_path = Path(__file__).resolve().parents[1] / ".env"
values = dotenv_values(env_path)

test_url = os.environ.get("TEST_DATABASE_URL") or values.get("TEST_DATABASE_URL")

if not test_url:
    raise RuntimeError("TEST_DATABASE_URL is not configured.")

parsed_url = make_url(test_url)

if (
    parsed_url.get_backend_name() != "postgresql"
    or parsed_url.database != "paint_rush_test"
):
    raise RuntimeError("Tests must use the PostgreSQL paint_rush_test database.")

os.environ["DATABASE_URL"] = test_url
os.environ["JWT_SECRET_KEY"] = "paint-rush-testing-secret-key"


@pytest.fixture(autouse=True)
def event_log_dir(tmp_path, monkeypatch):
    from app import event_log

    monkeypatch.setattr(event_log, "LOG_DIR", tmp_path / "logs" / "matches")
    yield
    event_log.close()
