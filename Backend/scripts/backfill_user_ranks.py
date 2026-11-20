import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database import engine as default_engine
from app.models import User, UserRank


def missing_users():
    return select(User.id).where(~exists().where(UserRank.user_id == User.id))


def backfill(db: Session, apply: bool = False) -> dict:
    try:
        total_users = db.scalar(select(func.count()).select_from(User))
        existing_ranks = db.scalar(select(func.count()).select_from(UserRank))
        missing = db.scalar(select(func.count()).select_from(missing_users().subquery()))
        created = []
        if apply:
            statement = (
                insert(UserRank)
                .from_select(["user_id"], missing_users().order_by(User.id))
                .on_conflict_do_nothing(index_elements=[UserRank.user_id])
                .returning(UserRank.user_id)
            )
            created = list(db.scalars(statement))
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    return {
        "total_users": total_users,
        "existing_ranks": existing_ranks,
        "missing_ranks": missing,
        "created": len(created),
    }


def main(argv=None, engine=default_engine) -> int:
    parser = argparse.ArgumentParser(description="랭크 데이터가 없는 유저에게 Bronze 1, 0 RP 행을 생성합니다.")
    parser.add_argument("--apply", action="store_true", help="실제로 DB에 반영합니다. 생략하면 Dry Run입니다.")
    parser.add_argument("--database", help="--apply 시 대상 DB 이름을 다시 입력해 확인합니다.")
    args = parser.parse_args(argv)

    target = engine.url.database
    print(f"Target database: {target} ({engine.url.host}:{engine.url.port})")
    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    if args.apply and args.database != target:
        print(f"Aborted: confirm the target with --database {target}")
        return 2

    with Session(engine) as db:
        result = backfill(db, apply=args.apply)
    print(f"Total users: {result['total_users']}")
    print(f"Existing ranks: {result['existing_ranks']}")
    print(f"Missing ranks: {result['missing_ranks']}")
    print(f"Created: {result['created']}" + ("" if args.apply else " (dry run)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
