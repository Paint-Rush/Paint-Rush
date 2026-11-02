## 플레이어 이동 및 타일 채색 모듈
## 덮어쓰기 제대로 되는지 프론트 개발 후 검증 필요
## pytest는 통과긴한데, ㄹㅇ 인겜에서 될지는 모름(얘도 채색될때 서버쪽에 딜레이생기면 중첩되는식의 버그 가능성잇음)

from app import game_engine
from app.algorithms.bfs import find_path, is_possible
from app.game_room import GameRoom
from app.maps import validate_map
from app.painting import paint_tile


def check_move(
    room: GameRoom, user_id: int, target_x: int, target_y: int, map_data: dict, max_steps: int, now: float,
) -> list[tuple[int, int]]:
    if type(user_id) is not int or user_id not in room.players:
        raise ValueError("not_in_room")
    if not game_engine.is_active(room, now):
        raise ValueError("game_not_playing")
    player = room.players[user_id]
    if not player.alive:
        raise ValueError("player_dead")
    if type(target_x) is not int or type(target_y) is not int:
        raise ValueError("invalid_target")
    try:
        validate_map(map_data)
    except ValueError:
        raise ValueError("map_unavailable") from None
    if (
        map_data["map_id"] != room.map_id
        or len(room.tile_owners) != map_data["height"]
        or any(len(row) != map_data["width"] for row in room.tile_owners)
    ):
        raise ValueError("map_unavailable")
    if not (0 <= target_x < map_data["width"] and 0 <= target_y < map_data["height"]):
        raise ValueError("invalid_target")
    if player.position == (target_x, target_y):
        raise ValueError("invalid_target")
    if not is_possible(map_data, target_x, target_y):
        raise ValueError("blocked_target")
    path = find_path(map_data, player.position, (target_x, target_y), max_steps)
    if path is None or len(path) - 1 > max_steps:
        raise ValueError("unreachable_target")
    return path


def move_player(
    room: GameRoom, user_id: int, target_x: int, target_y: int, map_data: dict, max_steps: int, now: float,
) -> list[tuple[int, int]]:
    path = check_move(room, user_id, target_x, target_y, map_data, max_steps, now)
    room.players[user_id].position = path[-1]
    return path


def paint_path(room: GameRoom, user_id: int, path: list[tuple[int, int]], map_data: dict, now: float) -> list[dict]:
    tiles = []
    for x, y in path[1:]:
        previous = room.tile_owners[y][x]
        if paint_tile(room, user_id, x, y, map_data, now):
            tiles.append({"x": x, "y": y, "previous_owner": previous, "new_owner": user_id})
    return tiles
