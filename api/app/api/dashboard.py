from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import GameAccount, GachaBanner, GachaImport, GachaPull


router = APIRouter(prefix="/api", tags=["dashboard"])


def _session_user_id(request: Request) -> uuid.UUID:
    raw_user_id = request.session.get("user_id")
    if not raw_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in",
        )
    try:
        return uuid.UUID(str(raw_user_id))
    except (TypeError, ValueError, AttributeError):
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login session",
        )


async def _owned_account(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    account_id: str,
) -> GameAccount:
    try:
        parsed_account_id = uuid.UUID(str(account_id))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game account not found",
        )

    account = await session.scalar(
        select(GameAccount).where(
            GameAccount.id == parsed_account_id,
            GameAccount.user_id == user_id,
        )
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Game account not found",
        )
    return account


def _parse_banner_id(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Banner not found",
        )


def _parse_rarities(value: str | None) -> list[int]:
    if value is None:
        return [4, 5]

    result = []
    for part in str(value).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            rarity = int(part)
        except ValueError:
            continue
        if rarity in (3, 4, 5) and rarity not in result:
            result.append(rarity)

    return result


def _display_banner_type(
    banner: GachaBanner | None,
) -> str:
    if banner is None:
        return "unknown"

    value = str(
        banner.game_banner_id
        or ""
    ).strip().casefold()
    name = str(
        banner.name
        or ""
    ).strip().casefold()
    stored = str(
        banner.banner_type
        or ""
    ).strip().casefold()

    if (
        "new area celebration" in name
        or (
            "area" in value
            and "select" in value
        )
    ):
        return "select"

    if (
        "take your pick" in name
        or "pickup-select" in value
        or stored == "select"
    ):
        return "select"

    if (
        "guaranteed" in name
        or value.startswith("gacha-fixed-")
        or stored == "guaranteed"
    ):
        return "guaranteed"

    if (
        name == "holodori gacha"
        or "common" in value
        or stored == "standard"
    ):
        return "standard"

    if (
        "pickup-normal" in value
        or (
            "pickup" in value
            and "select" not in value
        )
        or stored == "pickup"
    ):
        return "pickup"

    if (
        "collab" in value
        or stored == "collaboration"
    ):
        return "collaboration"

    if (
        "beginner" in value
        or stored == "beginner"
    ):
        return "beginner"

    if (
        value.startswith("history-name-")
        and name.endswith("gacha")
    ):
        return "pickup"

    if stored and stored != "unknown":
        return stored

    return "gacha"

def _pity_summary(
    pulls: list[GachaPull],
    banners: dict[uuid.UUID, GachaBanner],
):
    by_banner: dict[uuid.UUID, list[GachaPull]] = defaultdict(list)
    for pull in pulls:
        by_banner[pull.banner_id].append(pull)

    five_star_distances: list[int] = []
    banner_rows = []

    for banner_id, banner_pulls in by_banner.items():
        ordered = sorted(
            banner_pulls,
            key=lambda item: (
                item.pull_order,
                item.occurrence_index,
                str(item.id),
            ),
        )
        pity = 0
        five_star_count = 0
        four_star_count = 0
        last_five_pity = None

        for pull in ordered:
            pity += 1
            if pull.rarity >= 5:
                five_star_count += 1
                five_star_distances.append(pity)
                last_five_pity = pity
                pity = 0
            elif pull.rarity == 4:
                four_star_count += 1

        banner = banners.get(banner_id)
        banner_rows.append(
            {
                "id": str(banner_id),
                "game_banner_id": (
                    banner.game_banner_id
                    if banner
                    else str(banner_id)
                ),
                "name": (
                    banner.name
                    if banner
                    else "Unknown Banner"
                ),
                "banner_type": _display_banner_type(banner),
                "total_pulls": len(ordered),
                "five_star_count": five_star_count,
                "four_star_count": four_star_count,
                "current_five_star_pity": pity,
                "last_five_star_pity": last_five_pity,
            }
        )

    banner_rows.sort(
        key=lambda row: (
            -row["total_pulls"],
            row["name"].casefold(),
        )
    )

    average = (
        round(
            sum(five_star_distances)
            / len(five_star_distances),
            2,
        )
        if five_star_distances
        else None
    )
    return average, banner_rows


def _pull_row(
    pull: GachaPull,
    banners: dict[uuid.UUID, GachaBanner],
) -> dict:
    banner = banners.get(pull.banner_id)
    return {
        "id": str(pull.id),
        "item_id": pull.item_id,
        "item_name": pull.item_name,
        "rarity": int(pull.rarity),
        "pulled_at": (
            pull.pulled_at.isoformat()
            if pull.pulled_at
            else None
        ),
        "pull_order": int(pull.pull_order),
        "occurrence_index": int(
            pull.occurrence_index
        ),
        "is_featured": pull.is_featured,
        "banner": {
            "id": str(pull.banner_id),
            "game_banner_id": (
                banner.game_banner_id
                if banner
                else None
            ),
            "name": (
                banner.name
                if banner
                else "Unknown Banner"
            ),
            "banner_type": _display_banner_type(banner),
        },
    }


@router.get("/accounts/{account_id}/dashboard")
async def account_dashboard(
    account_id: str,
    request: Request,
    banner_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = _session_user_id(request)
    account = await _owned_account(
        session,
        user_id=user_id,
        account_id=account_id,
    )

    pulls = (
        await session.scalars(
            select(GachaPull)
            .where(
                GachaPull.game_account_id
                == account.id
            )
            .order_by(
                GachaPull.pulled_at.desc().nullslast(),
                GachaPull.created_at.desc(),
                GachaPull.pull_order.desc(),
            )
        )
    ).all()

    banner_ids = {
        pull.banner_id
        for pull in pulls
    }
    banners: dict[uuid.UUID, GachaBanner] = {}

    if banner_ids:
        banner_rows = (
            await session.scalars(
                select(GachaBanner).where(
                    GachaBanner.id.in_(banner_ids)
                )
            )
        ).all()
        banners = {
            banner.id: banner
            for banner in banner_rows
        }

    # Suppress only fully mirrored historical banners. Shared names alone
    # are insufficient: separate releases must keep their own pull history.
    grouped_for_mirrors = defaultdict(list)
    for pull in pulls:
        grouped_for_mirrors[pull.banner_id].append(pull)
    seen_mirrors = set()
    mirrored_ids = set()
    for candidate_id, candidate_pulls in grouped_for_mirrors.items():
        candidate = banners.get(candidate_id)
        if candidate is None:
            continue
        signature = (
            str(candidate.name or "").strip().casefold(),
            _display_banner_type(candidate),
            tuple(sorted(
                (
                    str(p.item_id),
                    str(p.pulled_at),
                    int(p.occurrence_index or 1),
                    int(p.rarity),
                )
                for p in candidate_pulls
            )),
        )
        if signature in seen_mirrors:
            mirrored_ids.add(candidate_id)
        else:
            seen_mirrors.add(signature)
    if mirrored_ids:
        pulls = [p for p in pulls if p.banner_id not in mirrored_ids]
        banner_ids.difference_update(mirrored_ids)
        banners = {k: v for k, v in banners.items() if k not in mirrored_ids}

    _all_average, all_banner_summary = (
        _pity_summary(
            pulls,
            banners,
        )
    )

    selected_banner_uuid = _parse_banner_id(
        banner_id
    )
    selected_banner = None

    if selected_banner_uuid is not None:
        if selected_banner_uuid not in banner_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Banner not found for this account",
            )
        selected_banner = banners.get(
            selected_banner_uuid
        )
        scoped_pulls = [
            pull
            for pull in pulls
            if pull.banner_id
            == selected_banner_uuid
        ]
    else:
        scoped_pulls = list(pulls)

    rarity_counts = defaultdict(int)
    for pull in scoped_pulls:
        rarity_counts[
            int(pull.rarity)
        ] += 1

    pulled_cards = []

    if selected_banner is not None:
        pulled_card_counts: dict[
            tuple[str, int],
            dict,
        ] = {}

        for pull in scoped_pulls:
            item_id = str(pull.item_id)
            rarity = int(pull.rarity)
            key = (item_id, rarity)
            row = pulled_card_counts.get(key)

            if row is None:
                row = {
                    "item_id": item_id,
                    "item_name": pull.item_name,
                    "rarity": rarity,
                    "count": 0,
                }
                pulled_card_counts[key] = row

            row["count"] += 1

        pulled_cards = sorted(
            pulled_card_counts.values(),
            key=lambda row: (
                -int(row["rarity"]),
                -int(row["count"]),
                str(row["item_name"]).casefold(),
            ),
        )

    average_five_star_pity, scoped_summary = (
        _pity_summary(
            scoped_pulls,
            banners,
        )
    )

    latest_import = await session.scalar(
        select(GachaImport)
        .where(
            GachaImport.game_account_id
            == account.id
        )
        .order_by(
            GachaImport.imported_at.desc()
        )
        .limit(1)
    )
    # Gii Murin can upload one logical sync in several HTTP chunks.
    # Group adjacent chunks so "Last synchronized" reports the whole sync,
    # rather than only the final request.
    if (
        latest_import is not None
        and latest_import.source == "gii_murin"
    ):
        recent_sync_chunks = (
            await session.scalars(
                select(GachaImport)
                .where(
                    GachaImport.game_account_id
                    == account.id,
                    GachaImport.source
                    == latest_import.source,
                    GachaImport.importer_version
                    == latest_import.importer_version,
                    GachaImport.imported_at
                    <= latest_import.imported_at,
                )
                .order_by(
                    GachaImport.imported_at.desc()
                )
                .limit(100)
            )
        ).all()

        sync_chunks = []
        previous_time = None

        for chunk in recent_sync_chunks:
            if previous_time is not None:
                gap_seconds = (
                    previous_time
                    - chunk.imported_at
                ).total_seconds()

                # Individual chunks are sent back-to-back. A larger gap
                # indicates a separate import/sync run.
                if gap_seconds > 5:
                    break

            sync_chunks.append(chunk)
            previous_time = chunk.imported_at

        if len(sync_chunks) > 1:
            latest_import.total_pulls = sum(
                int(chunk.total_pulls or 0)
                for chunk in sync_chunks
            )
            latest_import.new_pulls = sum(
                int(chunk.new_pulls or 0)
                for chunk in sync_chunks
            )
            latest_import.duplicate_pulls = sum(
                int(chunk.duplicate_pulls or 0)
                for chunk in sync_chunks
            )

    recent_five = [
        pull
        for pull in scoped_pulls
        if pull.rarity >= 5
    ]

    recent_history = scoped_pulls[:30]

    current_pity = max(
        (
            row["current_five_star_pity"]
            for row in scoped_summary
        ),
        default=0,
    )

    return {
        "account": {
            "id": str(account.id),
            "region": account.region,
            "game_account_id": (
                account.public_user_id
            ),
            "nickname": account.nickname,
        },
        "selected_banner": (
            {
                "id": str(selected_banner.id),
                "game_banner_id": (
                    selected_banner.game_banner_id
                ),
                "name": selected_banner.name,
                "banner_type": (
                    _display_banner_type(
                        selected_banner
                    )
                ),
            }
            if selected_banner
            else None
        ),
        "stats": {
            "total_pulls": len(scoped_pulls),
            "five_star_pulls": sum(
                count
                for rarity, count
                in rarity_counts.items()
                if rarity >= 5
            ),
            "four_star_pulls": (
                rarity_counts.get(4, 0)
            ),
            "three_star_pulls": (
                rarity_counts.get(3, 0)
            ),
            "average_five_star_pity": (
                average_five_star_pity
            ),
            "current_five_star_pity": (
                current_pity
            ),
            "banner_count": (
                len(scoped_summary)
            ),
        },
        # Always return every banner so the sidebar remains usable
        # while one banner is selected.
        "banners": all_banner_summary,
        "pulled_cards": pulled_cards,
        "recent_five_star_pulls": [
            _pull_row(pull, banners)
            for pull in recent_five
        ],
        "recent_pulls": [
            _pull_row(pull, banners)
            for pull in recent_history
        ],
        "latest_import": (
            {
                "source": latest_import.source,
                "importer_version": (
                    latest_import.importer_version
                ),
                "total_pulls": (
                    latest_import.total_pulls
                ),
                "new_pulls": (
                    latest_import.new_pulls
                ),
                "duplicate_pulls": (
                    latest_import.duplicate_pulls
                ),
                "imported_at": (
                    latest_import.imported_at
                    .isoformat()
                ),
            }
            if latest_import
            else None
        ),
    }


@router.get("/accounts/{account_id}/history")
async def account_history(
    account_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=30,
        ge=1,
        le=100,
    ),
    banner_id: str | None = Query(
        default=None
    ),
    rarities: str | None = Query(
        default="4,5"
    ),
    session: AsyncSession = Depends(
        get_session
    ),
) -> dict:
    user_id = _session_user_id(
        request
    )
    account = await _owned_account(
        session,
        user_id=user_id,
        account_id=account_id,
    )

    selected_banner_uuid = (
        _parse_banner_id(
            banner_id
        )
    )
    selected_rarities = (
        _parse_rarities(
            rarities
        )
    )

    conditions = [
        GachaPull.game_account_id
        == account.id
    ]

    if selected_banner_uuid is not None:
        conditions.append(
            GachaPull.banner_id
            == selected_banner_uuid
        )

    if selected_rarities:
        conditions.append(
            GachaPull.rarity.in_(
                selected_rarities
            )
        )
    else:
        # No rarity boxes selected means no rows should be shown.
        conditions.append(
            GachaPull.id.is_(None)
        )

    total = int(
        (
            await session.scalar(
                select(
                    func.count(
                        GachaPull.id
                    )
                ).where(
                    *conditions
                )
            )
        )
        or 0
    )

    total_pages = max(
        1,
        (
            total
            + page_size
            - 1
        )
        // page_size,
    )

    if page > total_pages:
        page = total_pages

    offset = (
        (page - 1)
        * page_size
    )

    rows = (
        await session.scalars(
            select(GachaPull)
            .where(
                *conditions
            )
            .order_by(
                GachaPull.pulled_at
                .desc()
                .nullslast(),
                GachaPull.created_at
                .desc(),
                GachaPull.pull_order
                .desc(),
            )
            .limit(page_size)
            .offset(offset)
        )
    ).all()

    banner_ids = {
        pull.banner_id
        for pull in rows
    }
    banners: dict[
        uuid.UUID,
        GachaBanner,
    ] = {}

    if banner_ids:
        banner_rows = (
            await session.scalars(
                select(GachaBanner).where(
                    GachaBanner.id.in_(
                        banner_ids
                    )
                )
            )
        ).all()
        banners = {
            banner.id: banner
            for banner in banner_rows
        }

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "rarities": selected_rarities,
        "banner_id": (
            str(selected_banner_uuid)
            if selected_banner_uuid
            else None
        ),
        "rows": [
            _pull_row(
                pull,
                banners,
            )
            for pull in rows
        ],
    }


