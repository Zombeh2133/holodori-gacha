from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import (
    DiscordIdentity,
    GameAccount,
    GachaPull,
    User,
)


router = APIRouter(prefix="/api", tags=["account"])


class DeleteMyDataRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    confirmation: str = Field(
        min_length=6,
        max_length=6,
    )


@router.get("/me")
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user_id = request.session.get("user_id")
    discord_user_id = request.session.get("discord_user_id")

    if not user_id or not discord_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not signed in",
        )

    try:
        parsed_user_id = uuid.UUID(str(user_id))
    except (TypeError, ValueError, AttributeError):
        request.session.clear()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login session",
        )

    accounts = (
        await session.scalars(
            select(GameAccount)
            .where(GameAccount.user_id == parsed_user_id)
            .order_by(GameAccount.created_at.asc())
        )
    ).all()

    account_rows = []
    for account in accounts:
        pull_count = await session.scalar(
            select(func.count(GachaPull.id)).where(
                GachaPull.game_account_id == account.id
            )
        )
        account_rows.append(
            {
                "id": str(account.id),
                "region": account.region,
                "game_account_id": account.public_user_id,
                "nickname": account.nickname,
                "pull_count": int(pull_count or 0),
            }
        )

    return {
        "authenticated": True,
        "user_id": str(user_id),
        "discord_user_id": str(discord_user_id),
        "discord_username": request.session.get("discord_username"),
        "game_accounts": account_rows,
    }

@router.delete("/me/data")
async def delete_my_data(
    payload: DeleteMyDataRequest,
    request: Request,
    session: AsyncSession = Depends(
        get_session
    ),
) -> dict:
    if payload.confirmation != "DELETE":
        raise HTTPException(
            status_code=(
                status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail=(
                "Type DELETE exactly to confirm."
            ),
        )

    raw_user_id = request.session.get(
        "user_id"
    )
    discord_user_id = str(
        request.session.get(
            "discord_user_id"
        )
        or ""
    ).strip()

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
            detail="Invalid login session",
        )

    identities = (
        await session.scalars(
            select(
                DiscordIdentity
            ).where(
                or_(
                    DiscordIdentity.user_id
                    == user_id,
                    DiscordIdentity.discord_user_id
                    == discord_user_id,
                )
            )
        )
    ).all()

    identity_ids = [
        identity.id
        for identity in identities
    ]

    account_filter = (
        GameAccount.user_id
        == user_id
    )

    if identity_ids:
        account_filter = or_(
            account_filter,
            GameAccount.owner_identity_id.in_(
                identity_ids
            ),
        )

    account_count = int(
        await session.scalar(
            select(
                func.count(
                    GameAccount.id
                )
            ).where(
                account_filter
            )
        )
        or 0
    )

    try:
        # Deleting game accounts cascades to their GachaImport and GachaPull
        # rows. GachaBanner is shared metadata, so it is intentionally kept.
        await session.execute(
            delete(
                GameAccount
            ).where(
                account_filter
            )
        )

        if identity_ids:
            await session.execute(
                delete(
                    DiscordIdentity
                ).where(
                    DiscordIdentity.id.in_(
                        identity_ids
                    )
                )
            )

        await session.execute(
            delete(
                User
            ).where(
                User.id
                == user_id
            )
        )

        await session.commit()
    except Exception:
        await session.rollback()
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=(
                "Your data could not be deleted. "
                "Nothing was intentionally retained by this request; please try again."
            ),
        ) from None

    request.session.clear()

    return {
        "deleted": True,
        "game_accounts_deleted": (
            account_count
        ),
    }

