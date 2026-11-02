## 에디터에서 만든 맵에 대한 스키마와 예외처리 등 상태관리
## 쁠라스 맵 로드 및 저장기능까지
## 사실 에디터로 만들때 오류걸리면 에러뜨긴하는데 혹시 모를상황을 대비한 예외처리도 해놈
## 형식은 json만 되게 해놨ㅇㅡㅁ

import json
import os
import re
import tempfile
from pathlib import Path

from app.game_room import DEFAULT_MAP_ID, MAP_SIZE

MAPS_DIR = Path(__file__).resolve().parents[1] / "maps"
TILE_TYPES = ("NORMAL", "GLASS", "BLOCKED")
MAP_ID_PATTERN = re.compile(r"[a-z0-9_-]{1,50}")


def map_path(map_id: str) -> Path:
    if not isinstance(map_id, str) or not MAP_ID_PATTERN.fullmatch(map_id):
        raise ValueError(f"잘못된 맵 ID입니다: {map_id!r} (소문자, 숫자, _, - 만 사용)")
    return MAPS_DIR / f"{map_id}.json"


def check_position(map_data: dict, x: int, y: int) -> None:
    if not (0 <= x < map_data["width"] and 0 <= y < map_data["height"]):
        raise ValueError(f"좌표 ({x}, {y})가 맵 범위를 벗어났습니다.")


def validate_map(map_data: dict) -> None:
    if not isinstance(map_data, dict):
        raise ValueError("맵 데이터는 JSON 객체여야 합니다.")
    for key, value_type in [("map_id", str), ("name", str), ("width", int), ("height", int), ("tiles", list)]:
        if not isinstance(map_data.get(key), value_type):
            raise ValueError(f"'{key}' 필드가 없거나 형식이 잘못되었습니다.")
    map_path(map_data["map_id"])

    width, height, tiles = map_data["width"], map_data["height"], map_data["tiles"]
    if width <= 0 or height <= 0:
        raise ValueError("width와 height는 1 이상이어야 합니다.")
    if len(tiles) != height or any(not isinstance(row, list) or len(row) != width for row in tiles):
        raise ValueError(f"tiles 배열 크기가 {width}x{height}와 일치하지 않습니다.")
    for row in tiles:
        for tile in row:
            if tile not in TILE_TYPES:
                raise ValueError(f"허용되지 않은 타일 종류입니다: {tile!r}")

    for x, y in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]:
        if tiles[y][x] == "BLOCKED":
            raise ValueError(f"스폰 지점 ({x}, {y})은 이동 가능한 타일이어야 합니다.")


def set_tile(map_data: dict, x: int, y: int, tile: str) -> None:
    if tile not in TILE_TYPES:
        raise ValueError(f"허용되지 않은 타일 종류입니다: {tile!r}")
    check_position(map_data, x, y)
    map_data["tiles"][y][x] = tile


def create_default_map(map_id: str = DEFAULT_MAP_ID, name: str = "Default Map") -> dict:
    map_data = {
        "map_id": map_id,
        "name": name,
        "width": MAP_SIZE,
        "height": MAP_SIZE,
        "tiles": [["NORMAL"] * MAP_SIZE for _ in range(MAP_SIZE)],
    }
    validate_map(map_data)
    return map_data


def load_map(map_id: str) -> dict:
    path = map_path(map_id)
    if not path.exists():
        raise FileNotFoundError(f"맵을 찾을 수 없습니다: {map_id}")
    try:
        map_data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"맵 JSON 형식이 잘못되었습니다: {map_id} ({e})") from e
    validate_map(map_data)
    if map_data["map_id"] != map_id:
        raise ValueError(f"파일명과 map_id가 일치하지 않습니다: {map_id}")
    return map_data


def save_map(map_data: dict, overwrite: bool = False) -> Path:
    validate_map(map_data)
    path = map_path(map_data["map_id"])
    if path.exists() and not overwrite:
        raise ValueError(f"이미 존재하는 맵 ID입니다: {map_data['map_id']}")

    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=MAPS_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(map_data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except BaseException:
        os.remove(temp_path)
        raise
    return path


def list_maps() -> list[str]:
    if not MAPS_DIR.exists():
        return []
    return sorted(path.stem for path in MAPS_DIR.glob("*.json"))
