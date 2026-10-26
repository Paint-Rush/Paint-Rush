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
        host="localhost", port=5432, user="postgres", password=password,
        dbname="postgres", autocommit=True, connect_timeout=5,
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", ("paint_rush",)
        ).fetchone()
        if not exists:
            connection.execute("CREATE DATABASE paint_rush")
            print("Created database: paint_rush")
        else:
            print("Database already exists: paint_rush")

    url = URL.create(
        "postgresql+psycopg", username="postgres", password=password,
        host="localhost", port=5432, database="paint_rush",
    ).render_as_string(hide_password=False)
    env_path = Path(__file__).resolve().parent / ".env"
    values = dotenv_values(env_path)
    set_key(env_path, "DATABASE_URL", url)
    if not values.get("JWT_SECRET_KEY"):
        set_key(env_path, "JWT_SECRET_KEY", secrets.token_urlsafe(48))
    os.environ["DATABASE_URL"] = url

    from app.database import Base, engine
    from app.models import User

    Base.metadata.create_all(engine)
    engine.dispose()
    print("Saved Backend/.env and created missing model tables.")
    print("Next: .venv\\Scripts\\python.exe check_db.py")


if __name__ == "__main__":
    main()
