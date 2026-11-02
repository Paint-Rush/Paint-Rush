from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AfterValidator, BaseModel, ConfigDict, StrictStr, StringConstraints, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.auth import DB, current_user_id
from app.characters import get_character
from app.database import SessionLocal
from app.game_room import DEFAULT_CHARACTER_ID
from app.models import User, UserProfile

router = APIRouter(prefix="/users/me", tags=["profile"])
UserId = Annotated[int, Depends(current_user_id)]


def check_character(character_id: str) -> str:
    if get_character(character_id) is None:
        raise ValueError("등록되지 않은 캐릭터입니다.")
    return character_id


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)] = None
    preferred_character_id: Annotated[StrictStr, AfterValidator(check_character)] = None

    @model_validator(mode="after")
    def not_empty(self):
        if not self.model_fields_set:
            raise ValueError("수정할 항목이 없습니다.")
        return self


class Profile(BaseModel):
    user_id: int
    username: str
    display_name: str
    preferred_character_id: str


def read_profile(db, user_id: int) -> dict:
    user, profile = db.execute(
        select(User, UserProfile).outerjoin(UserProfile, UserProfile.user_id == User.id).where(User.id == user_id)
    ).one()
    return {
        "user_id": user.id,
        "username": user.username,
        "display_name": profile.display_name if profile else user.username,
        "preferred_character_id": profile.preferred_character_id if profile else DEFAULT_CHARACTER_ID,
    }


def load_preferred_character(user_id: int) -> str:
    with SessionLocal() as db:
        character_id = db.scalar(select(UserProfile.preferred_character_id).where(UserProfile.user_id == user_id))
    return DEFAULT_CHARACTER_ID if character_id is None else character_id


@router.get("/profile", response_model=Profile)
def get_profile(user_id: UserId, db: DB):
    return read_profile(db, user_id)


@router.patch("/profile", response_model=Profile)
def patch_profile(body: ProfileUpdate, user_id: UserId, db: DB):
    changes = body.model_dump(exclude_unset=True)
    try:
        values = {**read_profile(db, user_id), **changes}
        statement = insert(UserProfile).values(
            user_id=user_id,
            display_name=values["display_name"],
            preferred_character_id=values["preferred_character_id"],
        )
        db.execute(statement.on_conflict_do_update(index_elements=[UserProfile.user_id], set_=changes))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "프로필을 저장하지 못했습니다.")
    return read_profile(db, user_id)
