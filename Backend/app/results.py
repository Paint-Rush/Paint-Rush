## 게임 승패 판정 및 결과 모듈
## 판정 기준
## 1. 채색한 타일 수
## 2. 총 채색한 타일 수
## 3. 킬 수
## 4. 사용한 카드가 적은 순
## 5. 사용한 코스트가 적은 순
## 6. player_order 순서 (매칭큐에 먼저 등록된순)
## 아마 인게임 내에서는 3이상으로 갈 일은 없을듯?

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app import rewards
from app.auth import DB, current_user_id
from app.algorithms.quick_sort import quick_sort
from app.game_engine import GAME_SECONDS
from app.game_room import MAX_PLAYERS, GameRoom, RoomStatus
from app.models import Match, MatchPlayerResult

STAT_FIELDS = ("rank", "painted_tiles", "total_painted", "kills", "deaths", "cards_used", "cost_used")
HISTORY_STATS = ("painted_tiles", "total_painted", "kills", "deaths", "cards_used", "cost_used")
MAX_HISTORY = 50
MAX_OFFSET = 2**31 - 1
OFFICIAL_PLAYERS = MAX_PLAYERS
DECIMAL_STATS = ("win_rate", "average_placement", "kd_ratio", "average_painted_tiles")

router = APIRouter(prefix="/users/me", tags=["match"])
UserId = Annotated[int, Depends(current_user_id)]


class MatchHistoryEntry(BaseModel):
    match_id: str
    map_id: str
    started_at: str
    ended_at: str
    player_count: int
    placement: int
    is_winner: bool
    painted_tiles: int
    total_painted: int
    kills: int
    deaths: int
    cards_used: int
    cost_used: int


class MatchHistory(BaseModel):
    total: int
    matches: list[MatchHistoryEntry]


class PlayerStats(BaseModel):
    total_matches: int
    wins: int
    win_rate: float
    average_placement: float | None
    total_kills: int
    total_deaths: int
    kd_ratio: float | None
    total_painted: int
    average_painted_tiles: float | None
    best_painted_tiles: int
    total_cards_used: int
    total_cost_used: int


def calculate_results(room: GameRoom) -> dict:
    if room.status is not RoomStatus.FINISHED:
        raise ValueError("게임이 종료된 룸만 결과를 계산할 수 있습니다.")
    order = room.player_order
    if not order:
        raise ValueError("참가 플레이어가 없습니다.")
    if len(set(order)) != len(order) or set(order) != set(room.players) or set(order) != set(room.tile_counts):
        raise ValueError("참가자 정보가 player_order와 일치하지 않습니다.")
    if any(room.players[user_id].user_id != user_id for user_id in order):
        raise ValueError("참가자 정보가 player_order와 일치하지 않습니다.")

    def rank_key(item):
        index, user_id = item
        player = room.players[user_id]
        return (
            -room.tile_counts[user_id],
            -player.total_painted,
            -player.kills,
            player.cards_used,
            player.cost_used,
            index,
        )

    keys = [rank_key(item) for item in enumerate(order)]
    quick_sort(keys, 0, len(keys) - 1)

    results = []
    for rank, key in enumerate(keys, 1):
        user_id = order[key[-1]]
        player = room.players[user_id]
        results.append({
            "user_id": user_id,
            "rank": rank,
            "is_winner": rank == 1,
            "painted_tiles": room.tile_counts[user_id],
            "total_painted": player.total_painted,
            "kills": player.kills,
            "deaths": player.deaths,
            "cards_used": player.cards_used,
            "cost_used": player.cost_used,
        })
    return {"room_id": str(room.room_id), "winner_id": results[0]["user_id"], "results": results}


def match_record(room: GameRoom) -> tuple[dict, list[dict]]:
    if room.status is not RoomStatus.FINISHED:
        raise ValueError("게임이 종료된 룸만 결과를 저장할 수 있습니다.")
    result = room.result
    if result is None or result["room_id"] != str(room.room_id) or room.started_at is None:
        raise ValueError("확정된 경기 결과가 없습니다.")
    match = {
        "id": room.room_id,
        "map_id": room.map_id,
        "started_at": room.started_at,
        "ended_at": room.started_at + timedelta(seconds=GAME_SECONDS),
        "winner_id": result["winner_id"],
    }
    players = [
        {
            "match_id": room.room_id,
            "user_id": r["user_id"],
            "matchmaking_order": room.player_order.index(r["user_id"]),
            **{key: r[key] for key in STAT_FIELDS},
        }
        for r in result["results"]
    ]
    return match, players


def load_match_record(db: Session, match_id) -> tuple[dict, list[dict]] | None:
    match = db.get(Match, match_id)
    if match is None:
        return None
    rows = db.scalars(
        select(MatchPlayerResult).where(MatchPlayerResult.match_id == match_id).order_by(MatchPlayerResult.rank)
    )
    return (
        {key: getattr(match, key) for key in ("id", "map_id", "started_at", "ended_at", "winner_id")},
        [
            {key: getattr(row, key) for key in ("match_id", "user_id", "matchmaking_order", *STAT_FIELDS)}
            for row in rows
        ],
    )


def save_match_result(room: GameRoom, db: Session) -> None:
    match, players = record = match_record(room)
    try:
        if db.get(Match, room.room_id) is None:
            db.add(Match(**match))
            db.flush()
            db.add_all(MatchPlayerResult(**player) for player in players)
            rewards.settle_rewards(db, room.room_id, players)
            db.commit()
            return
    except IntegrityError:
        db.rollback()
        if db.get(Match, room.room_id) is None:
            raise
    except Exception:
        db.rollback()
        raise
    if load_match_record(db, room.room_id) != record:
        raise ValueError(f"이미 다른 결과가 저장된 경기입니다: {room.room_id}")


def read_match_history(db: Session, user_id: int, limit: int, offset: int = 0) -> dict:
    others = aliased(MatchPlayerResult)
    player_count = (
        select(func.count()).select_from(others).where(others.match_id == Match.id).scalar_subquery()
    )
    rows = db.execute(
        select(Match, MatchPlayerResult, player_count.label("player_count"))
        .join(MatchPlayerResult, MatchPlayerResult.match_id == Match.id)
        .where(MatchPlayerResult.user_id == user_id)
        .order_by(Match.ended_at.desc(), Match.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    total = db.scalar(
        select(func.count())
        .select_from(MatchPlayerResult)
        .join(Match, Match.id == MatchPlayerResult.match_id)
        .where(MatchPlayerResult.user_id == user_id)
    )
    return {
        "total": total,
        "matches": [
            {
                "match_id": str(match.id),
                "map_id": match.map_id,
                "started_at": match.started_at.isoformat(),
                "ended_at": match.ended_at.isoformat(),
                "player_count": count,
                "placement": player.rank,
                "is_winner": match.winner_id == user_id,
                **{key: getattr(player, key) for key in HISTORY_STATS},
            }
            for match, player, count in rows
        ],
    }


@router.get("/matches", response_model=MatchHistory)
def get_my_matches(
    user_id: UserId,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=MAX_HISTORY)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
):
    try:
        return read_match_history(db, user_id, limit, offset)
    except SQLAlchemyError:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "경기 기록을 불러오지 못했습니다.")


def read_player_stats(db: Session, user_id: int) -> dict:
    official = (
        select(MatchPlayerResult.match_id)
        .group_by(MatchPlayerResult.match_id)
        .having(func.count() == OFFICIAL_PLAYERS)
    )
    matches = func.count()
    wins = func.count().filter(Match.winner_id == user_id)
    kills = func.coalesce(func.sum(MatchPlayerResult.kills), 0)
    deaths = func.coalesce(func.sum(MatchPlayerResult.deaths), 0)
    row = db.execute(
        select(
            matches.label("total_matches"),
            wins.label("wins"),
            func.coalesce(func.round(cast(wins, Numeric) * 100 / func.nullif(matches, 0), 2), 0).label("win_rate"),
            func.round(func.avg(MatchPlayerResult.rank), 2).label("average_placement"),
            kills.label("total_kills"),
            deaths.label("total_deaths"),
            func.round(cast(kills, Numeric) / func.nullif(deaths, 0), 2).label("kd_ratio"),
            func.coalesce(func.sum(MatchPlayerResult.total_painted), 0).label("total_painted"),
            func.round(func.avg(MatchPlayerResult.painted_tiles), 2).label("average_painted_tiles"),
            func.coalesce(func.max(MatchPlayerResult.painted_tiles), 0).label("best_painted_tiles"),
            func.coalesce(func.sum(MatchPlayerResult.cards_used), 0).label("total_cards_used"),
            func.coalesce(func.sum(MatchPlayerResult.cost_used), 0).label("total_cost_used"),
        )
        .select_from(MatchPlayerResult)
        .join(Match, Match.id == MatchPlayerResult.match_id)
        .where(MatchPlayerResult.user_id == user_id, MatchPlayerResult.match_id.in_(official))
    ).one()
    stats = dict(row._mapping)
    for key in DECIMAL_STATS:
        if stats[key] is not None:
            stats[key] = float(stats[key])
    return stats


@router.get("/stats", response_model=PlayerStats)
def get_my_stats(user_id: UserId, db: DB):
    try:
        return read_player_stats(db, user_id)
    except SQLAlchemyError:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "플레이어 통계를 불러오지 못했습니다.")
