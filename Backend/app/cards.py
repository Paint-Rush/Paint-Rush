## 카드 로직
## 덱은 총 6장, 패는 3장으로 구성

import random
from enum import Enum
from typing import NamedTuple

from app.game_room import MAX_COST, PlayerState

DECK_SIZE = 6
HAND_SIZE = 3


class CardType(str, Enum):
    MOVE = "MOVE"
    ATTACK = "ATTACK"


class Card(NamedTuple):
    type: CardType
    cost: int


CARDS = {
    "move_dash": Card(CardType.MOVE, 2),
    "move_jump": Card(CardType.MOVE, 3),
    "attack_slash": Card(CardType.ATTACK, 3),
    "attack_bomb": Card(CardType.ATTACK, 5),
}
DEFAULT_DECK = ["move_dash", "move_jump", "attack_slash", "attack_bomb", "move_dash", "attack_slash"]
MOVE_STEPS = {
    "move_dash": 1,
    "move_jump": 1,
}
ACTION_EVENTS = {
    CardType.MOVE: "move_requested",
    CardType.ATTACK: "attack_requested",
}


def card_cost(card_id: str) -> int | None:
    card = CARDS.get(card_id)
    if card is None or type(card.cost) is not int or not 1 <= card.cost <= MAX_COST:
        return None
    return card.cost


def list_cards() -> list[dict]:
    return [{"card_id": card_id, "type": card.type.value, "cost": card.cost} for card_id, card in CARDS.items()]


def validate_deck(card_ids: list[str]) -> list[str]:
    if not isinstance(card_ids, (list, tuple)) or len(card_ids) != DECK_SIZE:
        raise ValueError(f"덱은 카드 {DECK_SIZE}장으로 구성해야 합니다.")
    if not all(isinstance(card_id, str) and card_cost(card_id) is not None for card_id in card_ids):
        raise ValueError("등록되지 않은 카드가 있습니다.")
    return list(card_ids)


def initialize_deck(player: PlayerState, card_ids: list[str], rng: random.Random = random) -> None:
    if player.deck:
        raise ValueError("이미 덱이 초기화되었습니다.")
    if not isinstance(card_ids, (list, tuple)) or len(card_ids) != DECK_SIZE:
        raise ValueError(f"덱은 카드 {DECK_SIZE}장으로 구성해야 합니다.")
    if not all(isinstance(card_id, str) and card_id.strip() for card_id in card_ids):
        raise ValueError("잘못된 카드 ID가 있습니다.")
    deck = list(card_ids)
    rng.shuffle(deck)
    player.deck = deck
    player.hand = deck[:HAND_SIZE]
    player.draw_queue.clear()
    for card_id in deck[HAND_SIZE:]:
        player.draw_queue.enqueue(card_id)


def rotate_card(player: PlayerState, hand_index: int) -> str:
    if not player.deck:
        raise ValueError("덱이 초기화되지 않았습니다.")
    if type(hand_index) is not int or not 0 <= hand_index < len(player.hand):
        raise ValueError("잘못된 손패 인덱스입니다.")
    used = player.hand[hand_index]
    player.draw_queue.enqueue(used)
    player.hand[hand_index] = player.draw_queue.dequeue()
    return used
