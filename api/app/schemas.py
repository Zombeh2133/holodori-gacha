from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Region = Literal["US", "ASIA", "JP"]


class PullInput(BaseModel):
    game_banner_id: str = Field(min_length=1, max_length=128)
    banner_name: str = Field(min_length=1, max_length=200)
    banner_type: str = Field(default="unknown", min_length=1, max_length=64)

    item_id: str = Field(min_length=1, max_length=128)
    item_name: str = Field(min_length=1, max_length=200)
    rarity: int = Field(ge=1, le=10)

    pull_order: int = Field(ge=1)
    occurrence_index: int = Field(default=1, ge=1)
    pulled_at: datetime | None = None
    is_featured: bool | None = None
    raw_source_id: str | None = Field(default=None, max_length=500)


class GiiMurinImportRequest(BaseModel):
    discord_user_id: str = Field(min_length=5, max_length=32)
    region: Region
    game_account_id: str = Field(min_length=1, max_length=128)
    account_nickname: str | None = Field(default=None, max_length=100)
    importer_version: str | None = Field(default=None, max_length=64)
    pulls: list[PullInput] = Field(min_length=1, max_length=10000)

    @field_validator("discord_user_id")
    @classmethod
    def validate_discord_id(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("discord_user_id must contain digits only")
        return value

    @field_validator("game_account_id")
    @classmethod
    def normalize_game_account_id(cls, value: str) -> str:
        return value.strip()


class ImportResponse(BaseModel):
    import_id: str
    discord_user_id: str
    game_account_id: str
    region: Region
    total_pulls: int
    new_pulls: int
    duplicate_pulls: int



class SitePullInput(BaseModel):
    gacha_name: str = Field(
        min_length=1,
        max_length=200,
    )
    game_banner_id: str | None = Field(
        default=None,
        max_length=128,
    )
    banner_type: str | None = Field(
        default=None,
        max_length=64,
    )

    card_id: str = Field(
        min_length=1,
        max_length=128,
    )
    item_name: str | None = Field(
        default=None,
        max_length=200,
    )
    rarity: int | None = Field(
        default=None,
        ge=1,
        le=10,
    )

    draw_time: int | None = None
    pulled_at: datetime | None = None
    occurrence_index: int | None = Field(
        default=None,
        ge=1,
    )
    is_featured: bool | None = None
    raw_source_id: str | None = Field(
        default=None,
        max_length=500,
    )


class SiteImportRequest(BaseModel):
    region: Region
    game_account_id: str = Field(
        min_length=1,
        max_length=128,
    )
    account_nickname: str | None = Field(
        default=None,
        max_length=100,
    )
    importer_version: str | None = Field(
        default=None,
        max_length=64,
    )
    pulls: list[SitePullInput] = Field(
        min_length=1,
        max_length=10000,
    )

    @field_validator("game_account_id")
    @classmethod
    def normalize_site_game_account_id(
        cls,
        value: str,
    ) -> str:
        return value.strip()


class SiteImportResponse(BaseModel):
    import_id: str
    account_id: str
    game_account_id: str
    region: Region
    total_pulls: int
    new_pulls: int
    duplicate_pulls: int
