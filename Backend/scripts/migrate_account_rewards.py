import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import exists, func, inspect, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine

from app.database import engine as default_engine
from app.models import MatchReward, User, UserWallet

TABLES = (UserWallet.__table__, MatchReward.__table__)
REQUIRED = ("users", "matches")


def missing_wallets():
    return select(User.id).where(~exists().where(UserWallet.user_id == User.id))


def plan(connection: Connection) -> dict:
    inspector = inspect(connection)
    absent = [name for name in REQUIRED if not inspector.has_table(name)]
    if absent:
        raise RuntimeError(f"Required tables are missing: {', '.join(absent)}")
    missing_tables = [table.name for table in TABLES if not inspector.has_table(table.name)]
    total_users = connection.scalar(select(func.count()).select_from(User.__table__))
    if UserWallet.__table__.name in missing_tables:
        existing_wallets, missing = 0, total_users
    else:
        existing_wallets = connection.scalar(select(func.count()).select_from(UserWallet.__table__))
        missing = connection.scalar(select(func.count()).select_from(missing_wallets().subquery()))
    return {
        "missing_tables": missing_tables,
        "total_users": total_users,
        "existing_wallets": existing_wallets,
        "missing_wallets": missing,
    }


def migrate(engine: Engine, apply: bool = False) -> dict:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            result = plan(connection)
            created_tables = []
            created_wallets = 0
            if apply:
                for table in TABLES:
                    if table.name in result["missing_tables"]:
                        table.create(connection)
                        created_tables.append(table.name)
                statement = (
                    insert(UserWallet)
                    .from_select(["user_id"], missing_wallets().order_by(User.id))
                    .on_conflict_do_nothing(index_elements=[UserWallet.user_id])
                    .returning(UserWallet.user_id)
                )
                created_wallets = len(connection.scalars(statement).all())
                transaction.commit()
            else:
                transaction.rollback()
        except Exception:
            transaction.rollback()
            raise
    return {**result, "created_tables": created_tables, "created_wallets": created_wallets}


def main(argv=None, engine=default_engine) -> int:
    parser = argparse.ArgumentParser(description="Create account reward tables and missing user wallets.")
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
    suffix = "" if args.apply else " (dry run)"
    print(f"Missing tables: {', '.join(result['missing_tables']) or 'none'}")
    print(f"Created tables: {', '.join(result['created_tables']) or 'none'}{suffix}")
    print(f"Total users: {result['total_users']}")
    print(f"Existing wallets: {result['existing_wallets']}")
    print(f"Missing wallets: {result['missing_wallets']}")
    print(f"Created wallets: {result['created_wallets']}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
