import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.database import engine as default_engine

TABLE = "rank_history"
COLUMNS = {
    "tier_before": "VARCHAR(20)",
    "division_before": "INTEGER",
    "tier_after": "VARCHAR(20)",
    "division_after": "INTEGER",
}


def missing_columns(connection: Connection) -> list[str] | None:
    inspector = inspect(connection)
    if not inspector.has_table(TABLE):
        return None
    existing = {column["name"] for column in inspector.get_columns(TABLE)}
    return [name for name in COLUMNS if name not in existing]


def migrate(engine: Engine, apply: bool = False) -> dict:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            missing = missing_columns(connection)
            rows = 0 if missing is None else connection.scalar(text(f"SELECT count(*) FROM {TABLE}"))
            added = []
            if apply and missing:
                for name in missing:
                    connection.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {name} {COLUMNS[name]} NULL"))
                    added.append(name)
                transaction.commit()
            else:
                transaction.rollback()
        except Exception:
            transaction.rollback()
            raise
    return {"table_exists": missing is not None, "rows": rows, "missing": missing or [], "added": added}


def main(argv=None, engine=default_engine) -> int:
    parser = argparse.ArgumentParser(description="Add missing tier columns to rank_history.")
    parser.add_argument("--apply", action="store_true", help="Apply the migration. Without it this is a dry run.")
    parser.add_argument("--database", help="Target database name, required with --apply.")
    args = parser.parse_args(argv)

    target = engine.url.database
    print(f"Target database: {target} ({engine.url.host}:{engine.url.port})")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    if args.apply and args.database != target:
        print(f"Aborted: confirm the target with --database {target}")
        return 2

    result = migrate(engine, apply=args.apply)
    if not result["table_exists"]:
        print(f"Table {TABLE} does not exist. Nothing to migrate.")
        return 0
    print(f"Existing rows: {result['rows']}")
    print(f"Missing columns: {', '.join(result['missing']) or 'none'}")
    print(f"Added columns: {', '.join(result['added']) or 'none'}" + ("" if args.apply else " (dry run)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
