## 모델 스키마

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(20), nullable=False)
    preferred_character_id: Mapped[str] = mapped_column(String(50), nullable=False)


class UserLevel(Base):
    __tablename__ = "user_levels"
    __table_args__ = (
        CheckConstraint("level >= 1", name="ck_user_levels_level"),
        CheckConstraint("total_experience >= 0", name="ck_user_levels_total_experience"),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    level: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    total_experience: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")


class UserDeck(Base):
    __tablename__ = "user_decks"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    cards: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    map_id: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    winner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)


class MatchPlayerResult(Base):
    __tablename__ = "match_player_results"

    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    rank: Mapped[int] = mapped_column(nullable=False)
    matchmaking_order: Mapped[int] = mapped_column(nullable=False)
    painted_tiles: Mapped[int] = mapped_column(nullable=False)
    total_painted: Mapped[int] = mapped_column(nullable=False)
    kills: Mapped[int] = mapped_column(nullable=False)
    deaths: Mapped[int] = mapped_column(nullable=False)
    cards_used: Mapped[int] = mapped_column(nullable=False)
    cost_used: Mapped[int] = mapped_column(nullable=False)


class UserRank(Base):
    __tablename__ = "user_ranks"
    __table_args__ = (CheckConstraint("total_rp >= 0", name="ck_user_ranks_total_rp"),)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    total_rp: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    rp_reached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RankHistory(Base):
    __tablename__ = "rank_history"
    __table_args__ = (
        UniqueConstraint("match_id", "user_id", name="uq_rank_history_match_user"),
        CheckConstraint("placement BETWEEN 1 AND 4", name="ck_rank_history_placement"),
        CheckConstraint("rp_before >= 0 AND rp_after >= 0", name="ck_rank_history_rp"),
        CheckConstraint("rp_after = rp_before + rp_change", name="ck_rank_history_change"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    match_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("matches.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    placement: Mapped[int] = mapped_column(nullable=False)
    rp_before: Mapped[int] = mapped_column(nullable=False)
    placement_rp_change: Mapped[int] = mapped_column(nullable=False)
    rp_change: Mapped[int] = mapped_column(nullable=False)
    rp_after: Mapped[int] = mapped_column(nullable=False)
    tier_before: Mapped[str | None] = mapped_column(String(20), nullable=True)
    division_before: Mapped[int | None] = mapped_column(nullable=True)
    tier_after: Mapped[str | None] = mapped_column(String(20), nullable=True)
    division_after: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
