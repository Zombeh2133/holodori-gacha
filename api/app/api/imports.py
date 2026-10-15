from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session
from ..importing import (
    NormalizedPull,
    datetime_from_draw_time,
    get_or_create_identity,
    infer_banner_type,
    infer_rarity,
    save_normalized_import,
    synthetic_banner_key,
)
from ..models import DiscordIdentity
from ..schemas import (
    SiteImportRequest,
    SiteImportResponse,
)


router = APIRouter(
    prefix="/api/import",
    tags=["import"],
)

LOCAL_IMPORT_TOKEN_TTL_SECONDS = 15 * 60
LOCAL_IMPORT_TOKEN_VERSION = 1


def _session_identity(
    request: Request,
) -> tuple[
    uuid.UUID,
    str,
]:
    raw_user_id = request.session.get(
        "user_id"
    )
    discord_user_id = (
        request.session.get(
            "discord_user_id"
        )
    )

    if (
        not raw_user_id
        or not discord_user_id
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail="Not signed in",
        )

    try:
        user_id = uuid.UUID(
            str(
                raw_user_id
            )
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ):
        request.session.clear()
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid login session"
            ),
        )

    return (
        user_id,
        str(
            discord_user_id
        ),
    )


def _b64url_encode(
    value: bytes,
) -> str:
    return (
        base64.urlsafe_b64encode(
            value
        )
        .decode("ascii")
        .rstrip("=")
    )


def _b64url_decode(
    value: str,
) -> bytes:
    value = str(
        value
        or ""
    ).strip()

    value += "=" * (
        -len(value)
        % 4
    )

    return base64.urlsafe_b64decode(
        value.encode(
            "ascii"
        )
    )


def _local_token_secret() -> bytes:
    secret = str(
        get_settings()
        .session_secret
        or ""
    ).encode(
        "utf-8"
    )

    if not secret:
        raise RuntimeError(
            "SESSION_SECRET is not configured"
        )

    return secret


def _issue_local_import_token(
    *,
    user_id: uuid.UUID,
    discord_user_id: str,
) -> tuple[
    str,
    int,
]:
    now = int(
        time.time()
    )
    expires_at = (
        now
        + LOCAL_IMPORT_TOKEN_TTL_SECONDS
    )

    payload = {
        "v": LOCAL_IMPORT_TOKEN_VERSION,
        "typ": "local_import",
        "uid": str(
            user_id
        ),
        "did": str(
            discord_user_id
        ),
        "iat": now,
        "exp": expires_at,
        "nonce": secrets.token_urlsafe(
            12
        ),
    }

    body = _b64url_encode(
        json.dumps(
            payload,
            separators=(
                ",",
                ":",
            ),
            sort_keys=True,
        ).encode(
            "utf-8"
        )
    )

    signature = _b64url_encode(
        hmac.new(
            _local_token_secret(),
            body.encode(
                "ascii"
            ),
            hashlib.sha256,
        ).digest()
    )

    return (
        body
        + "."
        + signature,
        expires_at,
    )


def _verify_local_import_token(
    token: str,
) -> tuple[
    uuid.UUID,
    str,
]:
    token = str(
        token
        or ""
    ).strip()

    try:
        body, signature = (
            token.split(
                ".",
                1,
            )
        )
    except ValueError:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid local import token"
            ),
        )

    expected = _b64url_encode(
        hmac.new(
            _local_token_secret(),
            body.encode(
                "ascii"
            ),
            hashlib.sha256,
        ).digest()
    )

    if not hmac.compare_digest(
        signature,
        expected,
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid local import token"
            ),
        )

    try:
        payload = json.loads(
            _b64url_decode(
                body
            ).decode(
                "utf-8"
            )
        )
    except Exception:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid local import token"
            ),
        )

    if (
        payload.get("typ")
        != "local_import"
        or int(
            payload.get(
                "v",
                0,
            )
            or 0
        )
        != LOCAL_IMPORT_TOKEN_VERSION
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid local import token"
            ),
        )

    expires_at = int(
        payload.get(
            "exp",
            0,
        )
        or 0
    )

    if (
        expires_at <= int(
            time.time()
        )
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Local import token expired"
            ),
        )

    try:
        user_id = uuid.UUID(
            str(
                payload.get(
                    "uid",
                    "",
                )
            )
        )
    except (
        ValueError,
        TypeError,
        AttributeError,
    ):
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid local import token"
            ),
        )

    discord_user_id = str(
        payload.get(
            "did",
            "",
        )
        or ""
    ).strip()

    if not discord_user_id:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Invalid local import token"
            ),
        )

    return (
        user_id,
        discord_user_id,
    )


def _bearer_token(
    authorization: str,
) -> str:
    value = str(
        authorization
        or ""
    ).strip()

    if not value:
        return ""

    parts = value.split(
        None,
        1,
    )

    if (
        len(parts) != 2
        or parts[0].casefold()
        != "bearer"
    ):
        return ""

    return parts[1].strip()


async def _attached_identity(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    discord_user_id: str,
) -> DiscordIdentity:
    identity = await session.scalar(
        select(
            DiscordIdentity
        ).where(
            DiscordIdentity.discord_user_id
            == discord_user_id
        )
    )

    if identity is None:
        identity = await get_or_create_identity(
            session,
            discord_user_id,
        )

    if identity.user_id is None:
        identity.user_id = (
            user_id
        )
        await session.flush()

    elif identity.user_id != user_id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Discord identity is already "
                "attached to another site user"
            ),
        )

    return identity


async def _perform_site_import(
    payload: SiteImportRequest,
    *,
    user_id: uuid.UUID,
    discord_user_id: str,
    source: str,
    default_importer_version: str,
    session: AsyncSession,
) -> SiteImportResponse:
    identity = await _attached_identity(
        session,
        user_id=user_id,
        discord_user_id=discord_user_id,
    )

    occurrence_counts = defaultdict(
        int
    )
    normalized = []

    for (
        source_index,
        pull,
    ) in enumerate(
        payload.pulls
    ):
        pulled_at = (
            pull.pulled_at
        )

        if (
            pulled_at is None
            and pull.draw_time
            is not None
        ):
            pulled_at = (
                datetime_from_draw_time(
                    pull.draw_time
                )
            )

        if pulled_at is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Every pull must include "
                    "pulled_at or a valid draw_time"
                ),
            )

        if pulled_at.tzinfo is None:
            pulled_at = pulled_at.replace(
                tzinfo=timezone.utc
            )
        else:
            pulled_at = (
                pulled_at.astimezone(
                    timezone.utc
                )
            )

        banner_name = str(
            pull.gacha_name
            or ""
        ).strip()

        banner_key = str(
            pull.game_banner_id
            or ""
        ).strip()

        if not banner_key:
            banner_key = (
                synthetic_banner_key(
                    region=payload.region,
                    banner_name=(
                        banner_name
                    ),
                )
            )

        rarity = infer_rarity(
            pull.card_id,
            pull.rarity,
        )

        if rarity <= 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Could not determine rarity "
                    f"for card {pull.card_id}. "
                    "Include rarity explicitly."
                ),
            )

        occurrence_key = (
            banner_key,
            str(
                pull.card_id
            ),
            pulled_at.isoformat(),
        )

        if (
            pull.occurrence_index
            is not None
        ):
            occurrence_index = int(
                pull.occurrence_index
            )
        else:
            occurrence_counts[
                occurrence_key
            ] += 1
            occurrence_index = (
                occurrence_counts[
                    occurrence_key
                ]
            )

        normalized.append(
            NormalizedPull(
                banner_key=(
                    banner_key
                ),
                banner_name=(
                    banner_name
                ),
                banner_type=(
                    infer_banner_type(
                        banner_key,
                        pull.banner_type,
                        banner_name,
                    )
                ),
                item_id=str(
                    pull.card_id
                ).strip(),
                item_name=(
                    str(
                        pull.item_name
                        or pull.card_id
                    ).strip()
                ),
                rarity=rarity,
                pulled_at=pulled_at,
                occurrence_index=(
                    occurrence_index
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
        region=payload.region,
        game_account_id=(
            payload.game_account_id
        ),
        account_nickname=(
            payload.account_nickname
        ),
        source=source,
        importer_version=(
            payload.importer_version
            or default_importer_version
        ),
        pulls=normalized,
    )

    return SiteImportResponse(
        **result
    )


@router.get(
    "/local-token",
)
async def local_import_token(
    request: Request,
) -> dict:
    (
        user_id,
        discord_user_id,
    ) = _session_identity(
        request
    )

    token, expires_at = (
        _issue_local_import_token(
            user_id=user_id,
            discord_user_id=(
                discord_user_id
            ),
        )
    )

    return {
        "token": token,
        "expires_at": (
            datetime.fromtimestamp(
                expires_at,
                tz=timezone.utc,
            ).isoformat()
        ),
        "expires_in_seconds": (
            LOCAL_IMPORT_TOKEN_TTL_SECONDS
        ),
    }


@router.post(
    "/local",
    response_model=SiteImportResponse,
)
async def import_local_game_pulls(
    payload: SiteImportRequest,
    authorization: str = Header(
        default="",
        alias="Authorization",
    ),
    session: AsyncSession = Depends(
        get_session
    ),
) -> SiteImportResponse:
    token = _bearer_token(
        authorization
    )

    if not token:
        raise HTTPException(
            status_code=(
                status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                "Missing local import token"
            ),
        )

    (
        user_id,
        discord_user_id,
    ) = _verify_local_import_token(
        token
    )

    return await _perform_site_import(
        payload,
        user_id=user_id,
        discord_user_id=(
            discord_user_id
        ),
        source="local_game",
        default_importer_version=(
            "local-game-v1"
        ),
        session=session,
    )


@router.post(
    "/pulls",
    response_model=SiteImportResponse,
)
async def import_site_pulls(
    payload: SiteImportRequest,
    request: Request,
    session: AsyncSession = Depends(
        get_session
    ),
) -> SiteImportResponse:
    (
        user_id,
        discord_user_id,
    ) = _session_identity(
        request
    )

    return await _perform_site_import(
        payload,
        user_id=user_id,
        discord_user_id=(
            discord_user_id
        ),
        source="website_json",
        default_importer_version=(
            "site-json-v1"
        ),
        session=session,
    )

