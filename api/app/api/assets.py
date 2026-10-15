from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ..assets import asset_service


router = APIRouter(
    prefix="/api/assets",
    tags=["assets"],
)


@router.get("/status")
async def asset_status() -> dict:
    return await asset_service.status()


@router.post("/refresh")
async def refresh_asset_catalog() -> dict:
    try:
        return await asset_service.refresh_catalog()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc


@router.get("/search")
async def search_assets(
    query: str = Query(
        min_length=1,
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=200,
    ),
) -> dict:
    try:
        rows = await asset_service.search(
            query,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

    return {
        "query": query,
        "count": len(rows),
        "results": rows,
    }


@router.get("/card/{card_id}/matches")
async def card_asset_matches(
    card_id: str,
    limit: int = Query(
        default=30,
        ge=1,
        le=100,
    ),
) -> dict:
    try:
        rows = await asset_service.card_matches(
            card_id,
            limit=limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

    return {
        "card_id": card_id,
        "count": len(rows),
        "results": rows,
    }


@router.get("/card/{card_id}")
async def card_art(
    card_id: str,
):
    try:
        path = await asset_service.get_card_image(
            card_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to resolve/extract card art: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

    if path is None:
        raise HTTPException(
            status_code=404,
            detail="Card artwork was not found in the Octo catalog",
        )

    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": (
                "public, max-age=86400"
            )
        },
    )


@router.get("/banner/{game_banner_id}")
async def banner_art(
    game_banner_id: str,
):
    try:
        path = await asset_service.get_banner_image(
            game_banner_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to resolve/extract banner art: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

    if path is None:
        raise HTTPException(
            status_code=404,
            detail="Banner artwork was not found in the Octo catalog",
        )

    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": (
                "public, max-age=86400"
            )
        },
    )
