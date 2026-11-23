# 캐릭터 궁 모듈
# 임시캐릭 2명 궁 임시로 만들어둠
# 1번 궁은 3x3 노말 타일을 자기꺼로 칠하고 2번은 거리 3이내의 지점에 3x3범위공격, 상대 점령 타일 자기꺼로 바꾸기

from typing import NamedTuple

from app.attacks import bomb_area, distance
from app.game_room import GameRoom
from app.maps import validate_map
from app.painting import paint_tile

COLOR_BURST = "ultimate_01"
COLOR_STEAL = "ultimate_02"
STEAL_RANGE = 3


class Ultimate(NamedTuple):
    ultimate_id: str
    targeted: bool
    max_range: int
    radius: int
    enemy_only: bool


ULTIMATES = {
    ultimate.ultimate_id: ultimate
    for ultimate in [
        Ultimate(COLOR_BURST, targeted=False, max_range=0, radius=1, enemy_only=False),
        Ultimate(COLOR_STEAL, targeted=True, max_range=STEAL_RANGE, radius=1, enemy_only=True),
    ]
}


def get_ultimate(ultimate_id: str) -> Ultimate | None:
    return ULTIMATES.get(ultimate_id) if isinstance(ultimate_id, str) else None


def check_ultimate(
    room: GameRoom, user_id: int, ultimate_id: str, target_x, target_y, map_data: dict | None,
) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    ultimate = get_ultimate(ultimate_id)
    if ultimate is None:
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
    if not ultimate.targeted or (target_x is None and target_y is None):
        target = position
    else:
        if type(target_x) is not int or type(target_y) is not int:
            raise ValueError("invalid_ultimate_target")
        if not (0 <= target_x < map_data["width"] and 0 <= target_y < map_data["height"]):
            raise ValueError("invalid_ultimate_target")
        if distance(position, (target_x, target_y)) > ultimate.max_range:
            raise ValueError("ultimate_out_of_range")
        target = (target_x, target_y)
    return target, bomb_area(map_data, *target, ultimate.radius)


def resolve_ultimate(
    room: GameRoom, user_id: int, ultimate_id: str, area: list[tuple[int, int]], map_data: dict, now: float,
) -> list[dict]:
    ultimate = get_ultimate(ultimate_id)
    if ultimate is None:
        raise ValueError("unknown_ultimate")
    tiles = []
    for x, y in area:
        previous = room.tile_owners[y][x]
        if ultimate.enemy_only and previous is None:
            continue
        if paint_tile(room, user_id, x, y, map_data, now):
            tiles.append({"x": x, "y": y, "previous_owner": previous, "new_owner": user_id})
    return tiles
