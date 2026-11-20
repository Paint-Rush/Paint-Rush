## 유저 레벨 시스템
## 경험치 할당량은 미정

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import DB, current_user_id
from app.models import User, UserLevel

DEFAULT_LEVEL = 1
DEFAULT_EXPERIENCE = 0
MAX_INT = 2**31 - 1
EXPERIENCE_STEP = 50

router = APIRouter(prefix="/users/me", tags=["level"])
UserId = Annotated[int, Depends(current_user_id)]


class Level(BaseModel):
    level: int
    total_experience: int


def read_level(db: Session, user_id: int) -> dict:
    row = db.execute(select(UserLevel.level, UserLevel.total_experience).where(UserLevel.user_id == user_id)).one_or_none()
    if row is None:
        return {"level": DEFAULT_LEVEL, "total_experience": DEFAULT_EXPERIENCE}
    return {"level": row.level, "total_experience": row.total_experience}


def is_int(value) -> bool:
    return type(value) is int


def required_experience(level: int) -> int:
    if not is_int(level) or level < DEFAULT_LEVEL:
        raise ValueError(f"잘못된 레벨입니다: {level!r}")
    return EXPERIENCE_STEP * level * (level - 1)


def experience_to_next(level: int) -> int:
    return required_experience(level + 1) - required_experience(level)


def level_for_experience(total_experience: int) -> int:
    if not is_int(total_experience) or total_experience < 0:
        raise ValueError(f"잘못된 누적 경험치입니다: {total_experience!r}")
    return (1 + math.isqrt(1 + 4 * (total_experience // EXPERIENCE_STEP))) // 2


def progress(total_experience: int) -> dict:
    level = level_for_experience(total_experience)
    return {
        "level": level,
        "total_experience": total_experience,
        "level_experience": required_experience(level),
        "next_level_experience": required_experience(level + 1),
        "experience_to_next": required_experience(level + 1) - total_experience,
    }


def add_experience(db: Session, user_id: int, amount: int) -> dict:
    if not is_int(amount) or amount < 0:
        raise ValueError(f"잘못된 경험치 지급량입니다: {amount!r}")
    if not is_int(user_id) or db.get(User, user_id) is None:
        raise ValueError(f"존재하지 않는 사용자입니다: {user_id!r}")
    db.execute(insert(UserLevel).values(user_id=user_id).on_conflict_do_nothing(index_elements=[UserLevel.user_id]))
    row = db.execute(
        select(UserLevel)
        .where(UserLevel.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    experience_before, level_before = row.total_experience, row.level
    experience_after = experience_before + amount
    if experience_after > MAX_INT:
        raise ValueError(f"누적 경험치가 최대값을 초과합니다: {experience_after!r}")
    level_after = max(level_before, level_for_experience(experience_after))
    row.total_experience = experience_after
    row.level = level_after
    db.flush()
    return {
        "user_id": user_id,
        "experience_earned": amount,
        "experience_before": experience_before,
        "experience_after": experience_after,
        "level_before": level_before,
        "level_after": level_after,
        "leveled_up": level_after > level_before,
    }


def save_level(db: Session, user_id: int, level: int, total_experience: int) -> dict:
    if type(level) is not int or not DEFAULT_LEVEL <= level <= MAX_INT:
        raise ValueError(f"잘못된 레벨입니다: {level!r}")
    if type(total_experience) is not int or not 0 <= total_experience <= MAX_INT:
        raise ValueError(f"잘못된 누적 경험치입니다: {total_experience!r}")
    if type(user_id) is not int or db.get(User, user_id) is None:
        raise ValueError(f"존재하지 않는 사용자입니다: {user_id!r}")
    values = {"level": level, "total_experience": total_experience}
    statement = insert(UserLevel).values(user_id=user_id, **values)
    try:
        db.execute(statement.on_conflict_do_update(index_elements=[UserLevel.user_id], set_=values))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return read_level(db, user_id)


@router.get("/level", response_model=Level)
def get_level(user_id: UserId, db: DB):
    try:
        return read_level(db, user_id)
    except SQLAlchemyError:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "레벨 정보를 불러오지 못했습니다.")
