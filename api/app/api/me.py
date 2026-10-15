from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..models import GameAccount, GachaPull


router = APIRouter(prefix="/api", tags=["account"])


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
