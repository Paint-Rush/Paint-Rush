## 로컬 DB생성하는 레 ! 전 ! 드 스크립트
## DB테스트할떄 사용하고 나중에 대호서버에 올리면 폐기예정
import os
import secrets
from getpass import getpass
from pathlib import Path

import psycopg
from dotenv import dotenv_values, set_key
from sqlalchemy import URL


def main():
    password = getpass("Local PostgreSQL postgres password: ")

    with psycopg.connect(
        host="localhost",
        port=5432,
        user="postgres",
        password=password,
        dbname="postgres",
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        for database in ("paint_rush", "paint_rush_test"):
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database,),
            ).fetchone()

            if not exists:
                connection.execute(f"CREATE DATABASE {database}")
                print(f"Created database: {database}")
            else:
                print(f"Database already exists: {database}")

    def make_url(database):
        return URL.create(
            "postgresql+psycopg",
            username="postgres",
            password=password,
            host="localhost",
            port=5432,
            database=database,
        ).render_as_string(hide_password=False)

    env_path = Path(__file__).resolve().parent / ".env"
    values = dotenv_values(env_path)

    main_url = make_url("paint_rush")
    test_url = make_url("paint_rush_test")

    set_key(env_path, "DATABASE_URL", main_url)
    set_key(env_path, "TEST_DATABASE_URL", test_url)

    if not values.get("JWT_SECRET_KEY"):
        set_key(env_path, "JWT_SECRET_KEY", secrets.token_urlsafe(48))

    os.environ["DATABASE_URL"] = main_url

    from app.database import Base, engine
    from app.models import User

    Base.metadata.create_all(engine)
    engine.dispose()

    print("Databases configured successfully.")


if __name__ == "__main__":
    main()
