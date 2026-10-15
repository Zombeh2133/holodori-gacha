from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .asset_warmup import schedule_asset_warm
from .models import (
    DiscordIdentity,
    GameAccount,
    GachaBanner,
    GachaImport,
    GachaPull,
)


_CARD_RARITY_RE = re.compile(
    r"^card-[^-]+-(\d+)-",
    re.IGNORECASE,
)


@dataclass(slots=True)
class NormalizedPull:
    banner_key: str
    banner_name: str
    banner_type: str
    item_id: str
    item_name: str
    rarity: int
    pulled_at: datetime
    occurrence_index: int
    is_featured: bool | None = None
    raw_source_id: str | None = None
    source_index: int = 0


def unix_seconds(
    value,
) -> int | None:
    try:
        parsed = int(
            value or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if parsed <= 0:
        return None

    # Gacha/ListHistory exposes int64 draw_time without documenting the unit.
    # Accept seconds, milliseconds, microseconds, or nanoseconds.
    while parsed > 4_102_444_800:
        parsed //= 1000

    if not (
        946_684_800
        <= parsed
        <= 4_102_444_800
    ):
        return None

    return parsed


def datetime_from_draw_time(
    value,
) -> datetime | None:
    seconds = unix_seconds(
        value
    )
    if seconds is None:
        return None

    return datetime.fromtimestamp(
        seconds,
        tz=timezone.utc,
    )


def infer_rarity(
    card_id: str,
    fallback: int | None = None,
) -> int:
    if fallback is not None:
        try:
            rarity = int(
                fallback
            )
        except (
            TypeError,
            ValueError,
        ):
            rarity = 0

        if 1 <= rarity <= 10:
            return rarity

    match = _CARD_RARITY_RE.match(
        str(
            card_id
            or ""
        ).strip()
    )

    if match:
        try:
            rarity = int(
                match.group(1)
            )
        except ValueError:
            rarity = 0

        if 1 <= rarity <= 10:
            return rarity

    # Holodori Gacha currently uses 3★/4★/5★ cards, but do not silently
    # classify an unknown ID as one of them.
    return 0


def synthetic_banner_key(
    *,
    region: str,
    banner_name: str,
) -> str:
    payload = (
        f"{str(region).upper()}|"
        f"{str(banner_name).strip().casefold()}"
    )
    digest = hashlib.sha256(
        payload.encode(
            "utf-8"
        )
    ).hexdigest()[:24]

    return (
        "history-name-"
        + digest
    )


def infer_banner_type(
    game_banner_id: str,
    fallback: str | None = None,
) -> str:
    fallback_value = str(
        fallback
        or ""
    ).strip()

    if (
        fallback_value
        and fallback_value.casefold()
        != "unknown"
    ):
        return fallback_value

    value = str(
        game_banner_id
        or ""
    ).casefold()

    if "collab" in value:
        return "collaboration"
    if "beginner" in value:
        return "beginner"
    if "common" in value:
        return "standard"
    if "pickup" in value:
        return "pickup"

    return "gacha"


def stable_pull_identity(
    *,
    region: str,
    game_account_id: str,
    banner_key: str,
    item_id: str,
    pulled_at: datetime,
    occurrence_index: int,
) -> str:
    # Deliberately excludes pull_order. The game's latest-history window moves
    # over time, so the same pull must remain identical even if its displayed
    # relative position changes on a later import.
    timestamp = (
        pulled_at
        .astimezone(
            timezone.utc
        )
        .isoformat()
    )

    canonical = "|".join(
        (
            str(region).upper(),
            str(game_account_id),
            str(banner_key),
            str(item_id),
            timestamp,
            str(
                int(
                    occurrence_index
                )
            ),
        )
    )

    return hashlib.sha256(
        canonical.encode(
            "utf-8"
        )
    ).hexdigest()


async def get_or_create_identity(
    session: AsyncSession,
    discord_user_id: str,
) -> DiscordIdentity:
    identity = await session.scalar(
        select(
            DiscordIdentity
        ).where(
            DiscordIdentity.discord_user_id
            == str(
                discord_user_id
            )
        )
    )

    if identity is not None:
        return identity

    identity = DiscordIdentity(
        discord_user_id=str(
            discord_user_id
        )
    )
    session.add(
        identity
    )
    await session.flush()

    return identity


async def get_or_create_game_account(
    session: AsyncSession,
    *,
    identity: DiscordIdentity,
    region: str,
    public_user_id: str,
    nickname: str | None,
) -> GameAccount:
    account = await session.scalar(
        select(
            GameAccount
        ).where(
            GameAccount.owner_identity_id
            == identity.id,
            GameAccount.region
            == str(
                region
            ).upper(),
            GameAccount.public_user_id
            == str(
                public_user_id
            ),
        )
    )

    if account is not None:
        if (
            nickname
            and nickname
            != account.nickname
        ):
            account.nickname = (
                nickname
            )

        if (
            identity.user_id
            is not None
            and account.user_id
            is None
        ):
            account.user_id = (
                identity.user_id
            )

        return account

    account = GameAccount(
        owner_identity_id=(
            identity.id
        ),
        user_id=identity.user_id,
        region=str(
            region
        ).upper(),
        public_user_id=str(
            public_user_id
        ),
        nickname=nickname,
    )

    session.add(
        account
    )
    await session.flush()

    return account


async def get_or_create_banner(
    session: AsyncSession,
    *,
    region: str,
    banner_key: str,
    banner_name: str,
    banner_type: str,
) -> GachaBanner:
    banner = await session.scalar(
        select(
            GachaBanner
        ).where(
            GachaBanner.region
            == str(
                region
            ).upper(),
            GachaBanner.game_banner_id
            == str(
                banner_key
            ),
        )
    )

    if banner is not None:
        if banner_name:
            banner.name = (
                banner_name
            )

        if banner_type:
            banner.banner_type = (
                banner_type
            )

        return banner

    banner = GachaBanner(
        game_banner_id=str(
            banner_key
        ),
        region=str(
            region
        ).upper(),
        name=(
            str(
                banner_name
            ).strip()
            or str(
                banner_key
            )
        ),
        banner_type=(
            str(
                banner_type
            ).strip()
            or "gacha"
        ),
    )

    session.add(
        banner
    )
    await session.flush()

    return banner


async def save_normalized_import(
    session: AsyncSession,
    *,
    identity: DiscordIdentity,
    region: str,
    game_account_id: str,
    account_nickname: str | None,
    source: str,
    importer_version: str | None,
    pulls: Iterable[NormalizedPull],
) -> dict:
    region = str(
        region
    ).upper()
    game_account_id = str(
        game_account_id
    ).strip()

    account = await get_or_create_game_account(
        session,
        identity=identity,
        region=region,
        public_user_id=game_account_id,
        nickname=account_nickname,
    )

    pulls = list(
        pulls
    )

    banner_cache: dict[
        str,
        GachaBanner,
    ] = {}

    grouped: dict[
        str,
        list[
            NormalizedPull
        ],
    ] = defaultdict(
        list
    )

    for pull in pulls:
        grouped[
            pull.banner_key
        ].append(
            pull
        )

    new_pulls = 0
    duplicate_pulls = 0

    for (
        banner_key,
        banner_pulls,
    ) in grouped.items():
        first = banner_pulls[0]

        banner = await get_or_create_banner(
            session,
            region=region,
            banner_key=banner_key,
            banner_name=(
                first.banner_name
            ),
            banner_type=(
                first.banner_type
            ),
        )
        banner_cache[
            banner_key
        ] = banner

        max_order = int(
            (
                await session.scalar(
                    select(
                        func.max(
                            GachaPull.pull_order
                        )
                    ).where(
                        GachaPull.game_account_id
                        == account.id,
                        GachaPull.banner_id
                        == banner.id,
                    )
                )
            )
            or 0
        )

        existing_rows = (
            await session.scalars(
                select(
                    GachaPull
                ).where(
                    GachaPull.game_account_id
                    == account.id,
                    GachaPull.banner_id
                    == banner.id,
                )
            )
        ).all()

        # Stable tuple fallback protects rows written by early local builds
        # whose stored pull_identity included pull_order.
        existing_keys = {
            (
                str(
                    row.item_id
                ),
                (
                    row.pulled_at
                    .astimezone(
                        timezone.utc
                    )
                    .isoformat()
                    if row.pulled_at
                    else ""
                ),
                int(
                    row.occurrence_index
                    or 1
                ),
            )
            for row in existing_rows
        }

        ordered_new = sorted(
            banner_pulls,
            key=lambda item: (
                item.pulled_at,
                item.source_index,
            ),
        )

        for pull in ordered_new:
            timestamp = (
                pull.pulled_at
                .astimezone(
                    timezone.utc
                )
                .isoformat()
            )

            stable_key = (
                pull.item_id,
                timestamp,
                int(
                    pull.occurrence_index
                ),
            )

            if (
                stable_key
                in existing_keys
            ):
                duplicate_pulls += 1
                continue

            pull_identity = (
                stable_pull_identity(
                    region=region,
                    game_account_id=(
                        game_account_id
                    ),
                    banner_key=(
                        banner_key
                    ),
                    item_id=(
                        pull.item_id
                    ),
                    pulled_at=(
                        pull.pulled_at
                    ),
                    occurrence_index=(
                        pull.occurrence_index
                    ),
                )
            )

            hash_exists = await session.scalar(
                select(
                    GachaPull.id
                ).where(
                    GachaPull.game_account_id
                    == account.id,
                    GachaPull.pull_identity
                    == pull_identity,
                )
            )

            if hash_exists is not None:
                existing_keys.add(
                    stable_key
                )
                duplicate_pulls += 1
                continue

            max_order += 1

            session.add(
                GachaPull(
                    id=uuid.uuid4(),
                    game_account_id=(
                        account.id
                    ),
                    banner_id=(
                        banner.id
                    ),
                    pull_identity=(
                        pull_identity
                    ),
                    item_id=(
                        pull.item_id
                    ),
                    item_name=(
                        pull.item_name
                    ),
                    rarity=int(
                        pull.rarity
                    ),
                    pulled_at=(
                        pull.pulled_at
                    ),
                    pull_order=(
                        max_order
                    ),
                    occurrence_index=(
                        int(
                            pull.occurrence_index
                        )
                    ),
                    is_featured=(
                        pull.is_featured
                    ),
                    raw_source_id=(
                        pull.raw_source_id
                    ),
                )
            )

            await session.flush()

            existing_keys.add(
                stable_key
            )
            new_pulls += 1

    total_pulls = len(
        pulls
    )

    # Duplicates discovered by grouped processing should equal total-new, but
    # calculate from the final count so the response remains correct if future
    # validation introduces skipped rows.
    duplicate_pulls = max(
        duplicate_pulls,
        total_pulls
        - new_pulls,
    )

    import_id = uuid.uuid4()

    session.add(
        GachaImport(
            id=import_id,
            game_account_id=(
                account.id
            ),
            source=str(
                source
            ),
            importer_version=(
                importer_version
            ),
            total_pulls=(
                total_pulls
            ),
            new_pulls=(
                new_pulls
            ),
            duplicate_pulls=(
                duplicate_pulls
            ),
        )
    )
    await session.commit()

    # Warm artwork in the persistent production cache after every import.
    # This is deliberately background work so import responses stay fast.
    schedule_asset_warm(
        card_ids=(
            pull.item_id
            for pull in pulls
        ),
        banner_ids=(
            pull.banner_key
            for pull in pulls
        ),
    )

    return {
        "import_id": str(
            import_id
        ),
        "account_id": str(
            account.id
        ),
        "game_account_id": (
            game_account_id
        ),
        "region": region,
        "total_pulls": (
            total_pulls
        ),
        "new_pulls": (
            new_pulls
        ),
        "duplicate_pulls": (
            duplicate_pulls
        ),
    }

