# 캐릭터 궁 모듈
# 임시캐릭 2명 궁 임시로 만들어둠
# 1번 궁은 3x3 노말 타일을 자기꺼로 칠하고 2번은 거리 3이내의 지점에 3x3범위공격, 상대 점령 타일 자기꺼로 바꾸기

from app.attacks import bomb_area, distance
from app.game_room import GameRoom
from app.maps import validate_map
from app.painting import paint_tile

COLOR_BURST = "ultimate_01"
COLOR_STEAL = "ultimate_02"
STEAL_RANGE = 3


def check_ultimate(
    room: GameRoom, user_id: int, ultimate_id: str, target_x, target_y, map_data: dict | None,
) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    if ultimate_id not in (COLOR_BURST, COLOR_STEAL):
        raise ValueError("unknown_ultimate")
    if map_data is None:
        raise ValueError("map_unavailable")
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
    position = room.players[user_id].position
    if ultimate_id == COLOR_BURST or (target_x is None and target_y is None):
        target = position
    else:
        if type(target_x) is not int or type(target_y) is not int:
            raise ValueError("invalid_ultimate_target")
        if not (0 <= target_x < map_data["width"] and 0 <= target_y < map_data["height"]):
            raise ValueError("invalid_ultimate_target")
        if distance(position, (target_x, target_y)) > STEAL_RANGE:
            raise ValueError("ultimate_out_of_range")
        target = (target_x, target_y)
    return target, bomb_area(map_data, *target)


def resolve_ultimate(
    room: GameRoom, user_id: int, ultimate_id: str, area: list[tuple[int, int]], map_data: dict, now: float,
) -> list[dict]:
    tiles = []
    for x, y in area:
        previous = room.tile_owners[y][x]
        if ultimate_id == COLOR_STEAL and previous is None:
            continue
        if paint_tile(room, user_id, x, y, map_data, now):
            tiles.append({"x": x, "y": y, "previous_owner": previous, "new_owner": user_id})
    return tiles
