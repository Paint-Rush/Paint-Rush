## 유저 레벨 시스템
## 경험치 할당량은 미정

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
