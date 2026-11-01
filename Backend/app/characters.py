## 플레이어블 캐릭터 관련 모듈
## 아직 플블캐 기획이 안되어있어서 그냥 테스트 캐릭터로 2개 넣어둠ㅁ

from typing import NamedTuple


class Character(NamedTuple):
    character_id: str
    name: str
    ultimate_id: str
    ultimate_name: str


CHARACTERS = {
    character.character_id: character
    for character in [
        Character("character_01", "테스트 캐릭터 1", "ultimate_01", "테스트 궁극기 1"),
        Character("character_02", "테스트 캐릭터 2", "ultimate_02", "테스트 궁극기 2"),
    ]
}


def get_character(character_id: str) -> Character | None:
    return CHARACTERS.get(character_id) if isinstance(character_id, str) else None


def character_info(character_id: str) -> dict:
    character = get_character(character_id)
    return {
        "character_id": character_id,
        "character_name": character.name if character else None,
        "ultimate_id": character.ultimate_id if character else None,
        "ultimate_name": character.ultimate_name if character else None,
    }


def list_characters() -> list[dict]:
    return [character._asdict() for character in CHARACTERS.values()]
