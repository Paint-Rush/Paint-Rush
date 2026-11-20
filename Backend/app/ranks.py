## 랭킹 및 RP 모듈
## 브 - 실 - 골 - 플 - 다 - 마 - 챌
## 마스터 이전까진 브 1 2 3처럼 100점 단위
## 마스터부터는 랭킹제여서 100위권부터 챌린저

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import DB, current_user_id
from app.models import RankHistory, User, UserProfile, UserRank

TIERS = ("BRONZE", "SILVER", "GOLD", "PLATINUM", "DIAMOND")
DIVISIONS = 3
DIVISION_RP = 100
MASTER_RP = len(TIERS) * DIVISIONS * DIVISION_RP
CHALLENGER_RP = 1800
CHALLENGER_LIMIT = 100
RANKED_PLAYERS = 4
PLACEMENT_RP = {1: 30, 2: 10, 3: -10, 4: -30}
SETTLEMENT_FIELDS = (
    "placement", "rp_before", "placement_rp_change", "rp_change", "rp_after",
    "tier_before", "division_before", "tier_after", "division_after",
)
RANKING_ORDER = (UserRank.total_rp.desc(), UserRank.rp_reached_at.asc(), UserRank.user_id.asc())

me_router = APIRouter(prefix="/users/me", tags=["rank"])
router = APIRouter(prefix="/rankings", tags=["rank"])
UserId = Annotated[int, Depends(current_user_id)]


class MyRank(BaseModel):
    user_id: int
    total_rp: int
    tier: str
    division: int | None
    next_tier: str | None
    next_division: int | None
    rp_to_next: int | None
    ranking_position: int | None


class RankChange(BaseModel):
    match_id: str
    placement: int
    rp_before: int
    placement_rp_change: int
    rp_change: int
    rp_after: int
    created_at: str


class RankingEntry(BaseModel):
    position: int
    user_id: int
    username: str
    display_name: str
    total_rp: int
    tier: str


class Ranking(BaseModel):
    total: int
    rankings: list[RankingEntry]


def base_tier(total_rp: int) -> tuple[str, int | None]:
    if total_rp >= MASTER_RP:
        return "MASTER", None
    step = total_rp // DIVISION_RP
    return TIERS[step // DIVISIONS], step % DIVISIONS + 1


def is_challenger(total_rp: int, position: int | None) -> bool:
    return total_rp >= CHALLENGER_RP and position is not None and position <= CHALLENGER_LIMIT


def tier_of(total_rp: int, position: int | None = None) -> tuple[str, int | None]:
    tier, division = base_tier(total_rp)
    return ("CHALLENGER", None) if is_challenger(total_rp, position) else (tier, division)


def rank_info(total_rp: int, position: int | None = None) -> dict:
    tier, division = tier_of(total_rp, position)
    if total_rp < MASTER_RP:
        next_rp = (total_rp // DIVISION_RP + 1) * DIVISION_RP
        next_tier, next_division = base_tier(next_rp)
    elif total_rp < CHALLENGER_RP:
        next_rp, next_tier, next_division = CHALLENGER_RP, "CHALLENGER", None
    else:
        next_rp = next_tier = next_division = None
    return {
        "total_rp": total_rp,
        "tier": tier,
        "division": division,
        "next_tier": next_tier,
        "next_division": next_division,
        "rp_to_next": None if next_rp is None else next_rp - total_rp,
        "ranking_position": position,
    }


def apply_placement(rp_before: int, placement: int) -> tuple[int, int, int]:
    placement_rp_change = PLACEMENT_RP[placement]
    rp_after = max(0, rp_before + placement_rp_change)
    return placement_rp_change, rp_after - rp_before, rp_after


def ranked_placements(results: list[dict]) -> dict[int, int] | None:
    if len(results) != RANKED_PLAYERS:
        return None
    user_ids = [r.get("user_id") for r in results]
    if any(type(user_id) is not int for user_id in user_ids) or len(set(user_ids)) != RANKED_PLAYERS:
        return None
    if [r.get("rank") for r in results] != list(range(1, RANKED_PLAYERS + 1)):
        return None
    return {r["user_id"]: r["rank"] for r in results}


def settle_match(db: Session, match_id, results: list[dict]) -> list[dict]:
    placements = ranked_placements(results)
    if placements is None:
        return []
    user_ids = sorted(placements)
    db.execute(insert(UserRank).values([{"user_id": user_id} for user_id in user_ids]).on_conflict_do_nothing())
    rows = {
        row.user_id: row
        for row in db.scalars(select(UserRank).where(UserRank.user_id.in_(user_ids)).order_by(UserRank.user_id).with_for_update())
    }
    before = {user_id: tier_of(rows[user_id].total_rp, ranking_position(db, user_id)) for user_id in user_ids}
    changes = []
    for user_id in user_ids:
        row = rows[user_id]
        rp_before = row.total_rp
        placement_rp_change, rp_change, rp_after = apply_placement(rp_before, placements[user_id])
        if rp_change:
            row.total_rp = rp_after
            row.rp_reached_at = func.now()
        row.updated_at = func.now()
        changes.append({
            "match_id": match_id,
            "user_id": user_id,
            "placement": placements[user_id],
            "rp_before": rp_before,
            "placement_rp_change": placement_rp_change,
            "rp_change": rp_change,
            "rp_after": rp_after,
            "tier_before": before[user_id][0],
            "division_before": before[user_id][1],
        })
    db.flush()
    for change in changes:
        change["tier_after"], change["division_after"] = tier_of(change["rp_after"], ranking_position(db, change["user_id"]))
        db.add(RankHistory(**change))
    db.flush()
    return changes


def load_settlements(db: Session, match_id, results: list[dict]) -> dict[int, dict]:
    base = {"type": "rank_settlement", "room_id": str(match_id), "match_id": str(match_id)}
    user_ids = [r["user_id"] for r in results]
    if ranked_placements(results) is None:
        reason = "unranked_match" if len(results) != RANKED_PLAYERS else "invalid_result"
        return {user_id: {**base, "applied": False, "reason": reason} for user_id in user_ids}
    rows = {row.user_id: row for row in db.scalars(select(RankHistory).where(RankHistory.match_id == match_id))}
    settlements = {}
    for user_id in user_ids:
        row = rows.get(user_id)
        if row is None:
            settlements[user_id] = {**base, "applied": False, "reason": "not_settled"}
            continue
        settlements[user_id] = {
            **base,
            "applied": True,
            "reason": None,
            **{key: getattr(row, key) for key in SETTLEMENT_FIELDS},
        }
    return settlements


def ranking_position(db: Session, user_id: int) -> int | None:
    me = db.get(UserRank, user_id)
    if me is None or me.total_rp < MASTER_RP:
        return None
    ahead = db.scalar(
        select(func.count()).select_from(UserRank).where(
            UserRank.total_rp >= MASTER_RP,
            or_(
                UserRank.total_rp > me.total_rp,
                and_(UserRank.total_rp == me.total_rp, UserRank.rp_reached_at < me.rp_reached_at),
                and_(
                    UserRank.total_rp == me.total_rp,
                    UserRank.rp_reached_at == me.rp_reached_at,
                    UserRank.user_id < me.user_id,
                ),
            ),
        )
    )
    return ahead + 1


def read_rank(db: Session, user_id: int) -> dict:
    total_rp = db.scalar(select(UserRank.total_rp).where(UserRank.user_id == user_id)) or 0
    return {"user_id": user_id, **rank_info(total_rp, ranking_position(db, user_id))}


def read_history(db: Session, user_id: int, limit: int) -> list[dict]:
    rows = db.scalars(
        select(RankHistory)
        .where(RankHistory.user_id == user_id)
        .order_by(RankHistory.created_at.desc(), RankHistory.id.desc())
        .limit(limit)
    )
    return [
        {
            "match_id": str(row.match_id),
            "placement": row.placement,
            "rp_before": row.rp_before,
            "placement_rp_change": row.placement_rp_change,
            "rp_change": row.rp_change,
            "rp_after": row.rp_after,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def read_ranking(db: Session, limit: int, offset: int = 0, min_rp: int = MASTER_RP) -> dict:
    position = func.row_number().over(order_by=RANKING_ORDER).label("position")
    ranked = (
        select(
            UserRank.user_id,
            UserRank.total_rp,
            User.username,
            func.coalesce(UserProfile.display_name, User.username).label("display_name"),
            position,
        )
        .join(User, User.id == UserRank.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == UserRank.user_id)
        .where(UserRank.total_rp >= MASTER_RP)
        .subquery()
    )
    condition = and_(ranked.c.total_rp >= min_rp, ranked.c.position <= offset + limit, ranked.c.position > offset)
    rows = db.execute(select(ranked).where(condition).order_by(ranked.c.position)).all()
    total = db.scalar(select(func.count()).select_from(UserRank).where(UserRank.total_rp >= min_rp))
    return {
        "total": total,
        "rankings": [
            {
                "position": row.position,
                "user_id": row.user_id,
                "username": row.username,
                "display_name": row.display_name,
                "total_rp": row.total_rp,
                "tier": tier_of(row.total_rp, row.position)[0],
            }
            for row in rows
        ],
    }


def read_challengers(db: Session) -> dict:
    ranking = read_ranking(db, CHALLENGER_LIMIT, min_rp=CHALLENGER_RP)
    return {"total": len(ranking["rankings"]), "rankings": ranking["rankings"]}


def db_error(message: str) -> HTTPException:
    return HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, message)


@me_router.get("/rank", response_model=MyRank)
def get_my_rank(user_id: UserId, db: DB):
    try:
        return read_rank(db, user_id)
    except SQLAlchemyError:
        raise db_error("랭크 정보를 불러오지 못했습니다.")


@me_router.get("/rank/history", response_model=list[RankChange])
def get_my_rank_history(user_id: UserId, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    try:
        return read_history(db, user_id, limit)
    except SQLAlchemyError:
        raise db_error("RP 변동 내역을 불러오지 못했습니다.")


@router.get("", response_model=Ranking)
def get_ranking(
    user_id: UserId,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    try:
        return read_ranking(db, limit, offset)
    except SQLAlchemyError:
        raise db_error("랭킹을 불러오지 못했습니다.")


@router.get("/challengers", response_model=Ranking)
def get_challengers(user_id: UserId, db: DB):
    try:
        return read_challengers(db)
    except SQLAlchemyError:
        raise db_error("챌린저 순위를 불러오지 못했습니다.")
