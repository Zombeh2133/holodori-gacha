from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from sqlalchemy import select

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

        banner_ids = list(
            (
                await session.scalars(
                    select(
                        GachaBanner.game_banner_id
                    ).distinct()
                )
            ).all()
        )

    return await warm_asset_ids(
        card_ids=card_ids,
        banner_ids=banner_ids,
    )


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
