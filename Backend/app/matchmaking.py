## 매치메이킹시스템
## 플레이어1이 매칭을 시작하면 그거 웨이팅큐에 박아넣고 다른플레이어가 큐에 들어올떄까지 대기합니다
## 그외에도 예외 처리를 해놨긴했는데 작동할지는 잘 모르겠고요 작동안하면 이걸보는당신이 고치시면 됩니당
## 큐는 백엔드 폴더안에 queue.py에서 불러오는거

import random

from app import cards, characters, game_engine, maps
from app.algorithms.queue import LinkedQueue
from app.game_room import (
    DEFAULT_CHARACTER_ID,
    DEFAULT_MAP_ID,
    MAP_SIZE,
    MAX_PLAYERS,
    MIN_PLAYERS,
    GameRoom,
    create_room,
    get_player_room,
    remove_room,
)

MATCH_SIZE = MIN_PLAYERS
MAX_MATCH_SIZE = MAX_PLAYERS
MATCH_WAIT_SECONDS = 10
MAP_IDS: tuple[str, ...] = (DEFAULT_MAP_ID, "crossroads", "glass_garden")

waiting_queue = LinkedQueue()
selections: dict[int, str] = {}
decks: dict[int, list[str]] = {}
waiting_since: float | None = None


def sync_timer(now: float) -> None:
    global waiting_since
    if len(waiting_queue) < MATCH_SIZE:
        waiting_since = None
    elif waiting_since is None:
        waiting_since = now


def match_deadline() -> float | None:
    return None if waiting_since is None else waiting_since + MATCH_WAIT_SECONDS


def available_maps() -> list[str]:
    result = []
    for map_id in MAP_IDS:
        try:
            map_data = maps.load_map(map_id)
        except (FileNotFoundError, ValueError):
            continue
        if map_data["width"] == MAP_SIZE and map_data["height"] == MAP_SIZE:
            result.append(map_id)
    return result


def choose_map() -> str:
    return random.choice(available_maps() or [DEFAULT_MAP_ID])


def join_queue(user_id: int, character_id: str = DEFAULT_CHARACTER_ID, deck: list[str] | None = None) -> None:
    if user_id in waiting_queue:
        raise ValueError("이미 매칭 대기 중인 유저입니다.")
    if get_player_room(user_id):
        raise ValueError("이미 게임 중인 유저입니다.")
    if characters.get_character(character_id) is None:
        raise ValueError("unknown_character")
    if deck is not None:
        try:
            deck = cards.validate_deck(deck)
        except ValueError:
            raise ValueError("invalid_deck") from None
    waiting_queue.enqueue(user_id)
    selections[user_id] = character_id
    if deck is not None:
        decks[user_id] = deck
    sync_timer(game_engine.clock())


def cancel_queue(user_id: int) -> bool:
    if user_id not in waiting_queue:
        return False
    waiting_queue.remove(user_id)
    selections.pop(user_id, None)
    decks.pop(user_id, None)
    sync_timer(game_engine.clock())
    return True


def get_waiting_players() -> list[int]:
    return list(waiting_queue)


def match_candidates(now: float) -> list[int] | None:
    sync_timer(now)
    if len(waiting_queue) >= MAX_MATCH_SIZE:
        return waiting_queue.peek_many(MAX_MATCH_SIZE)
    if len(waiting_queue) >= MATCH_SIZE and now >= match_deadline():
        return waiting_queue.peek_many(MATCH_SIZE)
    return None


def drop_failed_match(user_ids: list[int]) -> list[int]:
    dropped = [user_id for user_id in user_ids if get_player_room(user_id)] or list(user_ids)
    for user_id in dropped:
        cancel_queue(user_id)
    return dropped


def try_match(now: float | None = None) -> GameRoom | None:
    global waiting_since
    now = game_engine.clock() if now is None else now
    user_ids = match_candidates(now)
    if user_ids is None:
        return None
    size = len(user_ids)
    room = create_room(
        user_ids,
        map_id=choose_map(),
        character_ids={user_id: selections.get(user_id, DEFAULT_CHARACTER_ID) for user_id in user_ids},
    )
    try:
        for player in room.players.values():
            cards.initialize_deck(player, decks.get(player.user_id, cards.DEFAULT_DECK))
    except ValueError:
        remove_room(room.room_id)
        raise
    for _ in range(size):
        user_id = waiting_queue.dequeue()
        selections.pop(user_id, None)
        decks.pop(user_id, None)
    waiting_since = None
    sync_timer(now)
    return room
