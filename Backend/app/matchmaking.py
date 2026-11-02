## 매치메이킹시스템
## 플레이어1이 매칭을 시작하면 그거 웨이팅큐에 박아넣고 다른플레이어가 큐에 들어올떄까지 대기합니다
## 그외에도 예외 처리를 해놨긴했는데 작동할지는 잘 모르겠고요 작동안하면 이걸보는당신이 고치시면 됩니당
## 큐는 백엔드 폴더안에 queue.py에서 불러오는거

from app import cards, characters
from app.algorithms.queue import LinkedQueue
from app.game_room import DEFAULT_CHARACTER_ID, GameRoom, create_room, get_player_room, remove_room

MATCH_SIZE = 2

waiting_queue = LinkedQueue()
selections: dict[int, str] = {}


def join_queue(user_id: int, character_id: str = DEFAULT_CHARACTER_ID) -> None:
    if user_id in waiting_queue:
        raise ValueError("이미 매칭 대기 중인 유저입니다.")
    if get_player_room(user_id):
        raise ValueError("이미 게임 중인 유저입니다.")
    if characters.get_character(character_id) is None:
        raise ValueError("unknown_character")
    waiting_queue.enqueue(user_id)
    selections[user_id] = character_id


def cancel_queue(user_id: int) -> bool:
    if user_id not in waiting_queue:
        return False
    waiting_queue.remove(user_id)
    selections.pop(user_id, None)
    return True


def get_waiting_players() -> list[int]:
    return list(waiting_queue)


def try_match() -> GameRoom | None:
    if len(waiting_queue) < MATCH_SIZE:
        return None
    user_ids = waiting_queue.peek_many(MATCH_SIZE)
    room = create_room(user_ids, character_ids={user_id: selections.get(user_id, DEFAULT_CHARACTER_ID) for user_id in user_ids})
    try:
        for player in room.players.values():
            cards.initialize_deck(player, cards.DEFAULT_DECK)
    except ValueError:
        remove_room(room.room_id)
        raise
    for _ in range(MATCH_SIZE):
        selections.pop(waiting_queue.dequeue(), None)
    return room
