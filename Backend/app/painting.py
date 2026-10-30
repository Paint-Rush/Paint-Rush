## 타일 채색 로직
## NNOMAL 타일만 채색이 가능하고 다른 타일들은 채색 X
## 실제 게임할 때 서버에 패킷손실 시 덮어쓰기에서 문제가 생길수도 있는데 그건 프론트랑 다 해서 테스트 해봐야 알듯

from app import game_engine
from app.game_room import GameRoom
from app.maps import check_position, validate_map


def paint_tile(room: GameRoom, user_id: int, x: int, y: int, map_data: dict, now: float | None = None) -> bool:
    if type(user_id) is not int or user_id not in room.players:
        raise ValueError(f"룸 참가자가 아닙니다: {user_id!r}")
    if type(x) is not int or type(y) is not int:
        raise ValueError(f"좌표는 정수여야 합니다: ({x!r}, {y!r})")
    validate_map(map_data)
    if map_data["map_id"] != room.map_id:
        raise ValueError(f"룸의 맵과 일치하지 않습니다: {map_data['map_id']}")
    if len(room.tile_owners) != map_data["height"] or any(len(row) != map_data["width"] for row in room.tile_owners):
        raise ValueError("맵 크기와 타일 소유권 크기가 일치하지 않습니다.")
    check_position(map_data, x, y)
    if not game_engine.is_active(room, game_engine.clock() if now is None else now):
        raise ValueError("게임 진행 중에만 채색할 수 있습니다.")

    owner = room.tile_owners[y][x]
    if map_data["tiles"][y][x] != "NORMAL" or owner == user_id:
        return False
    if owner is not None:
        room.tile_counts[owner] -= 1
    room.tile_owners[y][x] = user_id
    room.tile_counts[user_id] += 1
    room.players[user_id].total_painted += 1
    return True
