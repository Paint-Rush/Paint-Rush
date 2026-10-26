from uuid import uuid4

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine
from app.models import User


def main():
    if engine.dialect.name != "postgresql" or engine.url.database != "paint_rush":
        raise RuntimeError("DATABASE_URL must point to PostgreSQL paint_rush.")

    with engine.connect() as connection:
        row = connection.execute(text("SELECT current_database(), version()" )).one()
        print(f"Connected: {row[0]} / {row[1].split(',')[0]}")

    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    assert set(columns) == {"id", "username", "password_hash"}
    assert inspector.get_pk_constraint("users")["constrained_columns"] == ["id"]
    assert not columns["username"]["nullable"]
    assert not columns["password_hash"]["nullable"]
    assert any(item["column_names"] == ["username"] for item in inspector.get_unique_constraints("users"))
    print("PASS: users columns, primary key, UNIQUE and NOT NULL")

    username = "db_" + uuid4().hex[:17]
    with Session(engine) as session:
        try:
            user = User(username=username, password_hash="database-constraint-test")
            session.add(user)
            session.flush()
            assert user.id is not None
            assert session.scalar(select(User).where(User.username == username)).id == user.id
            print("PASS: insert, generated id and select")

            for name, values in [
                ("UNIQUE username", {"username": username, "password_hash": "test"}),
                ("NOT NULL username", {"username": None, "password_hash": "test"}),
                ("NOT NULL password_hash", {"username": "db_" + uuid4().hex[:17], "password_hash": None}),
            ]:
                try:
                    with session.begin_nested():
                        session.execute(User.__table__.insert().values(**values))
                except IntegrityError as error:
                    expected = "23505" if name.startswith("UNIQUE") else "23502"
                    assert error.orig.sqlstate == expected
                    print(f"PASS: {name} rejects invalid data")
                else:
                    raise AssertionError(f"Missing constraint: {name}")
        finally:
            session.rollback()

    with engine.connect() as connection:
        assert connection.execute(select(User.id).where(User.username == username)).first() is None
    engine.dispose()
    print("PASS: test data rolled back; existing users preserved")


if __name__ == "__main__":
    main()
