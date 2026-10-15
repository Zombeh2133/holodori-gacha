from __future__ import annotations

import secrets
from datetime import timezone

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session
from ..importing import (
    NormalizedPull,
    get_or_create_identity,
    infer_banner_type,
    save_normalized_import,
)
from ..schemas import (
    GiiMurinImportRequest,
    ImportResponse,
)


router = APIRouter(
    prefix="/api/v1/integrations",
    tags=["integrations"],
)


def _require_integration_key(
    x_integration_key: str = Header(
        default="",
        alias="X-Integration-Key",
    ),
) -> None:
    expected = (
        get_settings()
        .gii_murin_integration_key
    )

    if (
        not expected
        or not x_integration_key
        or not secrets.compare_digest(
            x_integration_key,
            expected,
        )
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid integration key"
            ),
        )


@router.post(
    "/gii-murin/import",
    response_model=ImportResponse,
    dependencies=[
        Depends(
            _require_integration_key
        )
    ],
)
async def import_from_gii_murin(
    request: GiiMurinImportRequest,
    session: AsyncSession = Depends(
        get_session
    ),
) -> ImportResponse:
    identity = await get_or_create_identity(
        session,
        request.discord_user_id,
    )

    normalized = []

    for (
        source_index,
        pull,
    ) in enumerate(
        request.pulls
    ):
        if pull.pulled_at is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Gii Murin integration pulls "
                    "must include pulled_at"
                ),
            )

        pulled_at = pull.pulled_at

        if pulled_at.tzinfo is None:
            pulled_at = pulled_at.replace(
                tzinfo=timezone.utc
            )
        else:
            pulled_at = pulled_at.astimezone(
                timezone.utc
            )

        normalized.append(
            NormalizedPull(
                banner_key=(
                    pull.game_banner_id
                ),
                banner_name=(
                    pull.banner_name
                ),
                banner_type=(
                    infer_banner_type(
                        pull.game_banner_id,
                        pull.banner_type,
                    )
                ),
                item_id=pull.item_id,
                item_name=pull.item_name,
                rarity=int(
                    pull.rarity
                ),
                pulled_at=pulled_at,
                occurrence_index=int(
                    pull.occurrence_index
                ),
                is_featured=(
                    pull.is_featured
                ),
                raw_source_id=(
                    pull.raw_source_id
                ),
                source_index=(
                    source_index
                ),
            )
        )

    result = await save_normalized_import(
        session,
        identity=identity,
        region=request.region,
        game_account_id=(
            request.game_account_id
        ),
        account_nickname=(
            request.account_nickname
        ),
        source="gii_murin",
        importer_version=(
            request.importer_version
        ),
        pulls=normalized,
    )

    return ImportResponse(
        import_id=result[
            "import_id"
        ],
        discord_user_id=(
            request.discord_user_id
        ),
        game_account_id=result[
            "game_account_id"
        ],
        region=result["region"],
        total_pulls=result[
            "total_pulls"
        ],
        new_pulls=result[
            "new_pulls"
        ],
        duplicate_pulls=result[
            "duplicate_pulls"
        ],
    )
