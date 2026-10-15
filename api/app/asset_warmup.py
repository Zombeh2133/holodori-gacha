from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from sqlalchemy import func, select

from .assets import asset_service
from .database import SessionLocal
from .models import GachaBanner, GachaPull


logger = logging.getLogger(
    "holodori.asset_warmup"
)

_BACKGROUND_TASKS: set[
    asyncio.Task
] = set()


def _clean_ids(
    values: Iterable[str],
    *,
    prefix: str,
) -> list[str]:
    result = {
        str(value or "").strip()
        for value in values
        if str(value or "").strip().casefold().startswith(
            prefix.casefold()
        )
    }
    return sorted(result)


async def warm_asset_ids(
    *,
    card_ids: Iterable[str] = (),
    banner_ids: Iterable[str] = (),
    concurrency: int = 6,
) -> dict:
    cards = _clean_ids(
        card_ids,
        prefix="card-",
    )
    banners = _clean_ids(
        banner_ids,
        prefix="gacha-",
    )

    before = 0
    try:
        before = int(
            (
                await asset_service.status()
            ).get(
                "cached_images",
                0,
            )
            or 0
        )
    except Exception:
        pass

    semaphore = asyncio.Semaphore(
        max(
            1,
            min(
                int(concurrency),
                12,
            ),
        )
    )

    ready = 0
    missing = 0
    failed = 0

    async def warm_one(
        kind: str,
        value: str,
    ) -> None:
        nonlocal ready, missing, failed

        async with semaphore:
            try:
                if kind == "card":
                    path = (
                        await asset_service
                        .get_card_image(
                            value
                        )
                    )
                else:
                    path = (
                        await asset_service
                        .get_banner_image(
                            value
                        )
                    )

                if path is None:
                    missing += 1
                    logger.info(
                        "Artwork not found while warming | kind=%s | id=%s",
                        kind,
                        value,
                    )
                    return

                ready += 1

            except Exception as exc:
                failed += 1
                logger.warning(
                    "Artwork warm failed | kind=%s | id=%s | error=%s: %s",
                    kind,
                    value,
                    type(exc).__name__,
                    exc,
                )

    jobs = [
        warm_one(
            "card",
            card_id,
        )
        for card_id in cards
    ]
    jobs.extend(
        warm_one(
            "banner",
            banner_id,
        )
        for banner_id in banners
    )

    if jobs:
        await asyncio.gather(
            *jobs
        )

    after = before
    try:
        after = int(
            (
                await asset_service.status()
            ).get(
                "cached_images",
                before,
            )
            or before
        )
    except Exception:
        pass

    result = {
        "cards": len(cards),
        "banners": len(banners),
        "requested": (
            len(cards)
            + len(banners)
        ),
        "ready": ready,
        "missing": missing,
        "failed": failed,
        "new_cached_images": max(
            0,
            after - before,
        ),
        "cached_images": after,
    }

    logger.info(
        "Artwork warm complete | cards=%s | banners=%s | ready=%s | missing=%s | failed=%s | new_cached=%s | cached=%s",
        result["cards"],
        result["banners"],
        result["ready"],
        result["missing"],
        result["failed"],
        result["new_cached_images"],
        result["cached_images"],
    )

    return result


async def warm_database_assets() -> dict:
    async with SessionLocal() as session:
        card_ids = list(
            (
                await session.scalars(
                    select(
                        GachaPull.item_id
                    ).distinct()
                )
            ).all()
        )

        banner_rows = (
            await session.execute(
                select(
                    GachaBanner,
                    func.min(
                        GachaPull.pulled_at
                    ).label(
                        "first_pulled_at"
                    ),
                )
                .outerjoin(
                    GachaPull,
                    GachaPull.banner_id
                    == GachaBanner.id,
                )
                .group_by(
                    GachaBanner.id
                )
            )
        ).all()

    result = await warm_asset_ids(
        card_ids=card_ids,
        banner_ids=(),
    )

    semaphore = asyncio.Semaphore(
        4
    )
    banner_ready = 0
    banner_missing = 0
    banner_failed = 0

    async def warm_banner(
        banner,
        first_pulled_at,
    ) -> None:
        nonlocal banner_ready, banner_missing, banner_failed

        async with semaphore:
            try:
                path = (
                    await asset_service
                    .get_banner_image_hinted(
                        game_banner_id=(
                            banner.game_banner_id
                        ),
                        banner_name=(
                            banner.name
                        ),
                        banner_type=(
                            banner.banner_type
                        ),
                        first_pulled_at=(
                            first_pulled_at
                        ),
                    )
                )

                if path is None:
                    banner_missing += 1
                else:
                    banner_ready += 1

            except Exception as exc:
                banner_failed += 1
                logger.warning(
                    "Historical banner artwork warm failed | "
                    "banner=%s | error=%s: %s",
                    banner.name,
                    type(exc).__name__,
                    exc,
                )

    if banner_rows:
        await asyncio.gather(
            *(
                warm_banner(
                    banner,
                    first_pulled_at,
                )
                for (
                    banner,
                    first_pulled_at,
                ) in banner_rows
            )
        )

    try:
        status = await asset_service.status()
        result["cached_images"] = int(
            status.get(
                "cached_images",
                result.get(
                    "cached_images",
                    0,
                ),
            )
            or 0
        )
    except Exception:
        pass

    result["banners"] = len(
        banner_rows
    )
    result["requested"] = (
        int(
            result.get(
                "cards",
                0,
            )
            or 0
        )
        + len(
            banner_rows
        )
    )
    result["banner_ready"] = (
        banner_ready
    )
    result["banner_missing"] = (
        banner_missing
    )
    result["banner_failed"] = (
        banner_failed
    )

    logger.info(
        "Database artwork warm complete | "
        "cards=%s | banners=%s | "
        "banner_ready=%s | banner_missing=%s | "
        "banner_failed=%s | cached=%s",
        result.get(
            "cards",
            0,
        ),
        len(
            banner_rows
        ),
        banner_ready,
        banner_missing,
        banner_failed,
        result.get(
            "cached_images",
            0,
        ),
    )

    return result

def _schedule(
    coroutine,
) -> None:
    try:
        task = asyncio.create_task(
            coroutine
        )
    except RuntimeError:
        coroutine.close()
        return

    _BACKGROUND_TASKS.add(
        task
    )

    def done(
        finished: asyncio.Task,
    ) -> None:
        _BACKGROUND_TASKS.discard(
            finished
        )

        try:
            finished.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "Background artwork warm task failed"
            )

    task.add_done_callback(
        done
    )


def schedule_asset_warm(
    *,
    card_ids: Iterable[str] = (),
    banner_ids: Iterable[str] = (),
) -> None:
    _schedule(
        warm_asset_ids(
            card_ids=tuple(
                card_ids
            ),
            banner_ids=tuple(
                banner_ids
            ),
        )
    )


def schedule_database_asset_warm() -> None:
    _schedule(
        warm_database_assets()
    )
