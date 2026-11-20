## 게임 종류 후 보상(재화) 지급 모듈
## 재환는 등수에 따른 차등지급이며 2인이랑 4인이랑은 지급량에 차이가 잇음

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ranks
from app.levels import add_experience
from app.models import MatchReward
from app.wallets import add_gold

REWARD_FIELDS = (
    "placement", "gold_earned", "gold_before", "gold_after",
    "experience_earned", "experience_before", "experience_after", "level_before", "level_after",
)
PLACEMENT_REWARDS = {
    4: {1: 100, 2: 80, 3: 60, 4: 40},
    2: {1: 50, 2: 40},
}


def reward_placements(results: list[dict]) -> dict[int, int] | None:
    size = len(results)
    if size not in PLACEMENT_REWARDS:
        return None
    user_ids = [r.get("user_id") for r in results]
    if any(type(user_id) is not int for user_id in user_ids) or len(set(user_ids)) != size:
        return None
    if [r.get("rank") for r in results] != list(range(1, size + 1)):
        return None
    return {r["user_id"]: r["rank"] for r in results}


def reward_amounts(results: list[dict]) -> dict[int, dict]:
    placements = reward_placements(results)
    if placements is None:
        return {}
    table = PLACEMENT_REWARDS[len(results)]
    return {
        user_id: {"placement": placement, "gold": table[placement], "experience": table[placement]}
        for user_id, placement in placements.items()
    }


def settle_rewards(db: Session, match_id, results: list[dict]) -> list[dict]:
    amounts = reward_amounts(results)
    if not amounts:
        return []
    if len(results) == ranks.RANKED_PLAYERS:
        ranks.settle_match(db, match_id, results)
    rewards = []
    for user_id in sorted(amounts):
        amount = amounts[user_id]
        gold = add_gold(db, user_id, amount["gold"])
        experience = add_experience(db, user_id, amount["experience"])
        reward = {
            "match_id": match_id,
            "user_id": user_id,
            "placement": amount["placement"],
            "gold_earned": gold["gold_earned"],
            "gold_before": gold["gold_before"],
            "gold_after": gold["gold_after"],
            "experience_earned": experience["experience_earned"],
            "experience_before": experience["experience_before"],
            "experience_after": experience["experience_after"],
            "level_before": experience["level_before"],
            "level_after": experience["level_after"],
        }
        db.add(MatchReward(**reward))
        rewards.append(reward)
    db.flush()
    return rewards


def load_reward_settlements(db: Session, match_id, results: list[dict]) -> dict[int, dict]:
    base = {"type": "reward_settlement", "room_id": str(match_id), "match_id": str(match_id)}
    user_ids = [r["user_id"] for r in results]
    if reward_placements(results) is None:
        return {user_id: {**base, "applied": False, "reason": "unrewarded_match"} for user_id in user_ids}
    rows = {row.user_id: row for row in db.scalars(select(MatchReward).where(MatchReward.match_id == match_id))}
    settlements = {}
    for user_id in user_ids:
        row = rows.get(user_id)
        if row is None:
            settlements[user_id] = {**base, "applied": False, "reason": "not_settled"}
            continue
        settlements[user_id] = {
            **base,
            "applied": True,
            "reason": None,
            **{key: getattr(row, key) for key in REWARD_FIELDS},
            "leveled_up": row.level_after > row.level_before,
        }
    return settlements
