from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DiscordIdentity(Base):
    __tablename__ = "discord_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    discord_user_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GameAccount(Base):
    __tablename__ = "game_accounts"
    __table_args__ = (
        UniqueConstraint(
            "owner_identity_id", "region", "public_user_id",
            name="uq_game_account_identity_region_public_id",
        ),
        Index("ix_game_accounts_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("discord_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    public_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GachaBanner(Base):
    __tablename__ = "gacha_banners"
    __table_args__ = (
        UniqueConstraint(
            "region", "game_banner_id", name="uq_gacha_banner_region_game_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    game_banner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    banner_type: Mapped[str] = mapped_column(String(64), default="unknown", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GachaImport(Base):
    __tablename__ = "gacha_imports"
    __table_args__ = (
        Index("ix_gacha_imports_account_imported_at", "game_account_id", "imported_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    game_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    importer_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_pulls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_pulls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_pulls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class GachaPull(Base):
    __tablename__ = "gacha_pulls"
    __table_args__ = (
        UniqueConstraint(
            "game_account_id", "pull_identity",
            name="uq_gacha_pull_account_identity",
        ),
        Index("ix_gacha_pulls_account_pulled_at", "game_account_id", "pulled_at"),
        Index(
            "ix_gacha_pulls_account_banner_order",
            "game_account_id", "banner_id", "pull_order",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    game_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("game_accounts.id", ondelete="CASCADE"), nullable=False
    )
    banner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gacha_banners.id", ondelete="RESTRICT"), nullable=False
    )
    pull_identity: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    rarity: Mapped[int] = mapped_column(Integer, nullable=False)
    pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pull_order: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurrence_index: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_featured: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw_source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
