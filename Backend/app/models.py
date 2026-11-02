## 모델 스키마

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
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
