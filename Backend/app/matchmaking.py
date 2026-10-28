## 매치메이킹시스템
## 플레이어1이 매칭을 시작하면 그거 웨이팅큐에 박아넣고 다른플레이어가 큐에 들어올떄까지 대기합니다
## 그외에도 예외 처리를 해놨긴했는데 작동할지는 잘 모르겠고요 작동안하면 이걸보는당신이 고치시면 됩니당

from collections import deque
from itertools import islice

from app.game_room import GameRoom, create_room, get_player_room

MATCH_SIZE = 2

waiting_queue: deque[int] = deque()


def join_queue(user_id: int) -> None:
    if user_id in waiting_queue:
        raise ValueError("이미 매칭 대기 중인 유저입니다.")
    if get_player_room(user_id):
        raise ValueError("이미 게임 중인 유저입니다.")
    waiting_queue.append(user_id)


def cancel_queue(user_id: int) -> bool:
    if user_id not in waiting_queue:
        return False
    waiting_queue.remove(user_id)
    return True


def get_waiting_players() -> list[int]:
    return list(waiting_queue)


def try_match() -> GameRoom | None:
    if len(waiting_queue) < MATCH_SIZE:
        return None
    room = create_room(list(islice(waiting_queue, MATCH_SIZE)))
    for _ in range(MATCH_SIZE):
        waiting_queue.popleft()
    return room
