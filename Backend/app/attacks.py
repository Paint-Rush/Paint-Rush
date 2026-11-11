## 공격카드 이벤트 따로 구분해놈
## 카드 추가되면 여따가 추가하면 댐

from app import event_log, game_engine
from app.game_room import MAX_HP, GameRoom
from app.maps import validate_map
from app.painting import paint_tile

SLASH = "attack_slash"
BOMB = "attack_bomb"
ATTACK_RANGE = {SLASH: 1, BOMB: 3}
BOMB_RADIUS = 1


def distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def bomb_area(map_data: dict, x: int, y: int) -> list[tuple[int, int]]:
    return [
        (x + dx, y + dy)
        for dy in range(-BOMB_RADIUS, BOMB_RADIUS + 1)
        for dx in range(-BOMB_RADIUS, BOMB_RADIUS + 1)
        if 0 <= x + dx < map_data["width"] and 0 <= y + dy < map_data["height"]
    ]


def check_attack(
    room: GameRoom, user_id: int, card_id: str, target_x: int, target_y: int, map_data: dict, now: float,
) -> list[tuple[int, int]]:
    if type(user_id) is not int or user_id not in room.players:
        raise ValueError("not_in_room")
    if not game_engine.is_active(room, now):
        raise ValueError("game_not_playing")
    player = room.players[user_id]
    if not player.alive:
        raise ValueError("player_dead")
    if card_id not in ATTACK_RANGE:
        raise ValueError("unknown_card")
    if type(target_x) is not int or type(target_y) is not int:
        raise ValueError("invalid_attack_target")
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
        raise ValueError("invalid_attack_target")
    reach = distance(player.position, (target_x, target_y))
    if card_id == SLASH:
        if reach != ATTACK_RANGE[SLASH]:
            raise ValueError("attack_out_of_range")
        if map_data["tiles"][target_y][target_x] != "NORMAL":
            raise ValueError("unattackable_tile")
        return [(target_x, target_y)]
    if reach > ATTACK_RANGE[BOMB]:
        raise ValueError("attack_out_of_range")
    return bomb_area(map_data, target_x, target_y)


def clear_tile(room: GameRoom, user_id: int, x: int, y: int, map_data: dict) -> int | None:
    owner = room.tile_owners[y][x]
    if map_data["tiles"][y][x] != "NORMAL" or owner is None or owner == user_id:
        return None
    room.tile_owners[y][x] = None
    room.tile_counts[owner] -= 1
    event_log.log_event(room.room_id, "tile_cleared", {
        "user_id": user_id,
        "x": x,
        "y": y,
        "previous_owner": owner,
        "new_owner": None,
    })
    return owner


def resolve_attack(
    room: GameRoom, user_id: int, card_id: str, area: list[tuple[int, int]], map_data: dict, now: float,
) -> list[dict]:
    tiles = []
    for x, y in area:
        previous = room.tile_owners[y][x]
        if card_id == SLASH:
            if paint_tile(room, user_id, x, y, map_data, now):
                tiles.append({"x": x, "y": y, "previous_owner": previous, "new_owner": user_id})
        elif clear_tile(room, user_id, x, y, map_data) is not None:
            tiles.append({"x": x, "y": y, "previous_owner": previous, "new_owner": None})
    return tiles


def hit_players(room: GameRoom, user_id: int, area: list[tuple[int, int]], now: float) -> list[dict]:
    targets = set(area)
    messages = []
    for player in map(room.players.get, room.player_order):
        if player.user_id == user_id or not player.alive or player.knocked_out_at is not None:
            continue
        if player.position not in targets:
            continue
        player.hp = max(0, player.hp - 1)
        damaged = {
            "type": "player_damaged",
            "room_id": str(room.room_id),
            "user_id": player.user_id,
            "attacker_id": user_id,
            "hp": player.hp,
            "max_hp": MAX_HP,
        }
        messages.append(damaged)
        event_log.log_event(room.room_id, "player_damaged", {k: v for k, v in damaged.items() if k not in ("type", "room_id")})
        if player.hp == 0:
            player.knocked_out_at = now
            player.alive = False
            player.deaths += 1
            room.players[user_id].kills += 1
            knocked = {
                "type": "player_knocked_out",
                "room_id": str(room.room_id),
                "user_id": player.user_id,
                "attacker_id": user_id,
                "respawn_remaining_seconds": game_engine.RESPAWN_SECONDS,
            }
            messages.append(knocked)
            event_log.log_event(room.room_id, "player_knocked_out", {k: v for k, v in knocked.items() if k not in ("type", "room_id")})
    return messages
