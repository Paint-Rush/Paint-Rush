from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import DB, current_user_id
from app.models import User, UserWallet

DEFAULT_GOLD = 0
MAX_INT = 2**31 - 1

router = APIRouter(prefix="/users/me", tags=["wallet"])
UserId = Annotated[int, Depends(current_user_id)]


class InsufficientGold(ValueError):
    pass


class Wallet(BaseModel):
    user_id: int
    gold: int


def check_user(db: Session, user_id: int) -> None:
    if type(user_id) is not int or db.get(User, user_id) is None:
        raise ValueError(f"존재하지 않는 사용자입니다: {user_id!r}")


def locked_wallet(db: Session, user_id: int) -> UserWallet | None:
    return db.execute(
        select(UserWallet)
        .where(UserWallet.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def add_gold(db: Session, user_id: int, amount: int) -> dict:
    if type(amount) is not int or amount < 0:
        raise ValueError(f"잘못된 골드 지급량입니다: {amount!r}")
    check_user(db, user_id)
    db.execute(insert(UserWallet).values(user_id=user_id).on_conflict_do_nothing(index_elements=[UserWallet.user_id]))
    wallet = locked_wallet(db, user_id)
    gold_before = wallet.gold
    gold_after = gold_before + amount
    if gold_after > MAX_INT:
        raise ValueError(f"보유 골드가 최대값을 초과합니다: {gold_after!r}")
    wallet.gold = gold_after
    db.flush()
    return {"user_id": user_id, "gold_earned": amount, "gold_before": gold_before, "gold_after": gold_after}


def spend_gold(db: Session, user_id: int, amount: int) -> dict:
    if type(amount) is not int or amount <= 0:
        raise ValueError(f"잘못된 골드 차감량입니다: {amount!r}")
    check_user(db, user_id)
    wallet = locked_wallet(db, user_id)
    gold_before = DEFAULT_GOLD if wallet is None else wallet.gold
    if gold_before < amount:
        raise InsufficientGold(f"골드가 부족합니다: 보유 {gold_before}, 필요 {amount}")
    wallet.gold = gold_before - amount
    db.flush()
    return {"user_id": user_id, "gold_spent": amount, "gold_before": gold_before, "gold_after": wallet.gold}


def read_wallet(db: Session, user_id: int) -> dict:
    gold = db.scalar(select(UserWallet.gold).where(UserWallet.user_id == user_id))
    return {"user_id": user_id, "gold": DEFAULT_GOLD if gold is None else gold}


@router.get("/wallet", response_model=Wallet)
def get_wallet(user_id: UserId, db: DB):
    try:
        return read_wallet(db, user_id)
    except SQLAlchemyError:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "지갑 정보를 불러오지 못했습니다.")
