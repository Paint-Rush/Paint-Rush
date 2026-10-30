## 게임 룸 시스템
## 게임룸과플레이어에대한데이터타입및스키마를정의하고잇으며룸생성가입탈퇴등이구현되어있는코드
## 반 갑 대 호

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.algorithms.queue import LinkedQueue

MAP_SIZE = 20
MIN_PLAYERS = 2
MAX_PLAYERS = 4
START_COST = 6
MAX_COST = 10
DEFAULT_MAP_ID = "default"
DEFAULT_CHARACTER_ID = "default"


class RoomStatus(str, Enum):
    READY = "READY"
    PLAYING = "PLAYING"
    DEATHMATCH = "DEATHMATCH"
    FINISHED = "FINISHED"


class Color(str, Enum):
    RED = "RED"
    BLUE = "BLUE"
    GREEN = "GREEN"
    YELLOW = "YELLOW"


SPAWN_POINTS = [
    (0, 0),
    (0, MAP_SIZE - 1),
    (MAP_SIZE - 1, 0),
    (MAP_SIZE - 1, MAP_SIZE - 1),
]


@dataclass
class PlayerState:
    user_id: int
    character_id: str
    color: Color
    spawn: tuple[int, int]
    position: tuple[int, int]
    connected: bool = True
    alive: bool = True
    deck: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    draw_queue: LinkedQueue = field(default_factory=LinkedQueue)
    cost: int = START_COST
    cost_used: int = 0
    cards_used: int = 0
    kills: int = 0
    deaths: int = 0
    total_painted: int = 0
    cost_mark: float = 0.0


@dataclass
class GameRoom:
    map_id: str
    players: dict[int, PlayerState]
    player_order: list[int]
    room_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: RoomStatus = RoomStatus.READY
    tile_owners: list[list[int | None]] = field(
        default_factory=lambda: [[None] * MAP_SIZE for _ in range(MAP_SIZE)]
    )
    tile_counts: dict[int, int] = field(default_factory=dict)
    started_at: datetime | None = None
    ready_at: float | None = None
    last_tick: int | None = None
    result: dict | None = None
    result_saved: bool = False


rooms: dict[uuid.UUID, GameRoom] = {}
player_rooms: dict[int, uuid.UUID] = {}


def create_room(
    user_ids: list[int],
    map_id: str = DEFAULT_MAP_ID,
    character_ids: dict[int, str] | None = None,
) -> GameRoom:
    if not MIN_PLAYERS <= len(user_ids) <= MAX_PLAYERS:
        raise ValueError(f"방에는 {MIN_PLAYERS}~{MAX_PLAYERS}명까지 입장할 수 있습니다.")
    if len(set(user_ids)) != len(user_ids):
        raise ValueError("동일한 유저가 방에 존재합니다!")
    if any(user_id in player_rooms for user_id in user_ids):
        raise ValueError("유저가 이미 다른 방에 존재합니다.")

    character_ids = character_ids or {}
    spawns = random.sample(SPAWN_POINTS, len(user_ids))
    players = {
        user_id: PlayerState(
            user_id=user_id,
            character_id=character_ids.get(user_id, DEFAULT_CHARACTER_ID),
            color=color,
            spawn=spawn,
            position=spawn,
        )
        for user_id, color, spawn in zip(user_ids, Color, spawns)
    }
    room = GameRoom(
        map_id=map_id,
        players=players,
        player_order=list(user_ids),
        tile_counts={user_id: 0 for user_id in user_ids},
    )

    rooms[room.room_id] = room
    for user_id in user_ids:
        player_rooms[user_id] = room.room_id
    return room


def get_room(room_id: uuid.UUID) -> GameRoom | None:
    return rooms.get(room_id)


def remove_room(room_id: uuid.UUID) -> GameRoom | None:
    room = rooms.pop(room_id, None)
    if room:
        for user_id in room.player_order:
            player_rooms.pop(user_id, None)
    return room


def get_player_room(user_id: int) -> GameRoom | None:
    room_id = player_rooms.get(user_id)
    return rooms.get(room_id) if room_id else None


def set_player_connected(user_id: int, connected: bool) -> PlayerState | None:
    room = get_player_room(user_id)
    if not room:
        return None
    player = room.players[user_id]
    player.connected = connected
    return player
