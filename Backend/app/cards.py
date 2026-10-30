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
    DEFENSE = "DEFENSE"


class Card(NamedTuple):
    type: CardType
    cost: int


CARDS = {
    "move_dash": Card(CardType.MOVE, 2),
    "move_jump": Card(CardType.MOVE, 3),
    "attack_slash": Card(CardType.ATTACK, 3),
    "attack_bomb": Card(CardType.ATTACK, 5),
    "defense_shield": Card(CardType.DEFENSE, 2),
    "defense_wall": Card(CardType.DEFENSE, 4),
}
DEFAULT_DECK = list(CARDS)


def card_cost(card_id: str) -> int | None:
    card = CARDS.get(card_id)
    if card is None or type(card.cost) is not int or not 1 <= card.cost <= MAX_COST:
        return None
    return card.cost


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
