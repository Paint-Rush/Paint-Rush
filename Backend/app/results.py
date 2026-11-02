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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.game_engine import GAME_SECONDS
from app.game_room import GameRoom, RoomStatus
from app.models import Match, MatchPlayerResult

STAT_FIELDS = ("rank", "painted_tiles", "total_painted", "kills", "deaths", "cards_used", "cost_used")


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

    results = []
    for rank, (_, user_id) in enumerate(sorted(enumerate(order), key=rank_key), 1):
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
