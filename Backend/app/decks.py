## 게임 시작 전 세팅하고 들어가는 덱 모듈
## 카드는 아직 안정해져서 임의 카드 4장을 넣어서 테스트만 해봄ㅁ

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AfterValidator, BaseModel, StrictStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.auth import DB, current_user_id
from app.cards import DEFAULT_DECK, validate_deck
from app.database import SessionLocal
from app.models import UserDeck

router = APIRouter(prefix="/users/me", tags=["deck"])
UserId = Annotated[int, Depends(current_user_id)]


class Deck(BaseModel):
    cards: Annotated[list[StrictStr], AfterValidator(validate_deck)]


def is_valid(cards: list[str]) -> bool:
    try:
        validate_deck(cards)
    except ValueError:
        return False
    return True


def load_deck(user_id: int) -> list[str]:
    with SessionLocal() as db:
        cards = db.scalar(select(UserDeck.cards).where(UserDeck.user_id == user_id))
    return list(DEFAULT_DECK if cards is None else cards)


@router.get("/deck", response_model=Deck)
def get_deck(user_id: UserId, db: DB):
    cards = db.scalar(select(UserDeck.cards).where(UserDeck.user_id == user_id))
    return {"cards": DEFAULT_DECK if cards is None else cards}


@router.put("/deck", response_model=Deck)
def put_deck(body: Deck, user_id: UserId, db: DB):
    statement = insert(UserDeck).values(user_id=user_id, cards=body.cards)
    try:
        db.execute(statement.on_conflict_do_update(index_elements=[UserDeck.user_id], set_={"cards": statement.excluded.cards}))
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "덱틀 저장하지 못했습니다")
    return {"cards": body.cards}
