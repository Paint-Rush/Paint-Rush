## 인게임의 시스템들을 관리하는 엔진
## 딱히 뭐 특별한건 업음

import math
import time
from datetime import datetime, timedelta, timezone

from app.game_room import MAX_COST, GameRoom, PlayerState, RoomStatus

READY_SECONDS = 10
GAME_SECONDS = 300
DEATHMATCH_AT = 240
COST_INTERVAL = 0.86
DEATHMATCH_COST_INTERVAL = 0.45
EPSILON = 1e-9

clock = time.monotonic


def game_time(room: GameRoom, now: float) -> float:
    return now - room.ready_at - READY_SECONDS


def status_at(t: float) -> RoomStatus:
    if t < 0:
        return RoomStatus.READY
    if t < DEATHMATCH_AT:
        return RoomStatus.PLAYING
    if t < GAME_SECONDS:
        return RoomStatus.DEATHMATCH
    return RoomStatus.FINISHED


def charge_at(t: float) -> float:
    t = min(max(t, 0), GAME_SECONDS)
    if t <= DEATHMATCH_AT:
        return t / COST_INTERVAL
    return DEATHMATCH_AT / COST_INTERVAL + (t - DEATHMATCH_AT) / DEATHMATCH_COST_INTERVAL


def ready_remaining(room: GameRoom, now: float) -> int:
    return max(0, math.ceil(-game_time(room, now) - EPSILON))


def remaining(room: GameRoom, now: float) -> int:
    return min(GAME_SECONDS, max(0, math.ceil(GAME_SECONDS - game_time(room, now) - EPSILON)))


def cost_message(player: PlayerState) -> dict:
    return {"type": "cost_updated", "cost": player.cost, "max_cost": MAX_COST}


def update_cost(player: PlayerState, charge: float) -> bool:
    if player.cost >= MAX_COST:
        player.cost_mark = charge
        return False
    gained = math.floor(charge - player.cost_mark + EPSILON)
    if gained <= 0:
        return False
    player.cost = min(MAX_COST, player.cost + gained)
    player.cost_mark = charge if player.cost == MAX_COST else player.cost_mark + gained
    return True


def spend_cost(room: GameRoom, user_id: int, amount: int, now: float) -> bool:
    player = room.players[user_id]
    if room.status not in (RoomStatus.PLAYING, RoomStatus.DEATHMATCH) or not 0 < amount <= player.cost:
        return False
    if player.cost >= MAX_COST:
        player.cost_mark = charge_at(game_time(room, now))
    player.cost -= amount
    return True


def start(room: GameRoom, now: float) -> dict | None:
    if room.ready_at is not None:
        return None
    room.ready_at = now
    room.last_tick = READY_SECONDS
    return {"type": "game_ready", "room_id": str(room.room_id), "remaining_seconds": READY_SECONDS}


def advance(room: GameRoom, now: float) -> list[tuple[int | None, dict]]:
    if room.ready_at is None or room.status is RoomStatus.FINISHED:
        return []
    t = game_time(room, now)
    status = status_at(t)
    events = []
    if room.status is RoomStatus.READY and status is not RoomStatus.READY:
        room.started_at = datetime.now(timezone.utc) - timedelta(seconds=t)
        events.append((None, {"type": "game_started", "room_id": str(room.room_id), "duration_seconds": GAME_SECONDS}))
    if room.status in (RoomStatus.READY, RoomStatus.PLAYING) and status in (RoomStatus.DEATHMATCH, RoomStatus.FINISHED):
        events.append((None, {
            "type": "deathmatch_started",
            "remaining_seconds": remaining(room, now),
            "cost_interval": DEATHMATCH_COST_INTERVAL,
        }))
    if status is not RoomStatus.READY:
        charge = charge_at(t)
        for player in map(room.players.get, room.player_order):
            if update_cost(player, charge):
                events.append((player.user_id, cost_message(player)))
    room.status = status

    if status is RoomStatus.FINISHED:
        events.append((None, {"type": "game_finished", "room_id": str(room.room_id)}))
        return events
    seconds = ready_remaining(room, now) if status is RoomStatus.READY else remaining(room, now)
    if seconds != room.last_tick:
        room.last_tick = seconds
        events.append((None, {"type": "game_tick", "status": status.value, "remaining_seconds": seconds}))
    return events


def timer_state(room: GameRoom, now: float) -> dict:
    return {
        "ready_remaining_seconds": ready_remaining(room, now),
        "remaining_seconds": remaining(room, now),
        "max_cost": MAX_COST,
    }
