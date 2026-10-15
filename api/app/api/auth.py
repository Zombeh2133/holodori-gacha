from __future__ import annotations

import html
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session
from ..models import DiscordIdentity, GameAccount, User


router = APIRouter(tags=["auth"])

DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/v10/oauth2/token"
DISCORD_ME_URL = "https://discord.com/api/v10/users/@me"


def _require_oauth_config() -> None:
    settings = get_settings()
    if not settings.discord_oauth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Discord OAuth is not configured. Set DISCORD_CLIENT_ID, "
                "DISCORD_CLIENT_SECRET, and DISCORD_REDIRECT_URI in .env."
            ),
        )


async def _claim_discord_identity(
    session: AsyncSession,
    *,
    discord_user_id: str,
) -> tuple[User, DiscordIdentity, int]:
    identity = await session.scalar(
        select(DiscordIdentity).where(
            DiscordIdentity.discord_user_id == discord_user_id
        )
    )

    if identity is None:
        user = User()
        session.add(user)
        await session.flush()

        identity = DiscordIdentity(
            discord_user_id=discord_user_id,
            user_id=user.id,
        )
        session.add(identity)
        await session.flush()
    else:
        if identity.user_id is None:
            user = User()
            session.add(user)
            await session.flush()
            identity.user_id = user.id
        else:
            user = await session.get(User, identity.user_id)
            if user is None:
                # Defensive repair for a broken local reference.
                user = User()
                session.add(user)
                await session.flush()
                identity.user_id = user.id

    # Any game-account rows created by Gii Murin before this first website
    # login are claimed solely because Discord verified this exact identity.
    result = await session.execute(
        update(GameAccount)
        .where(
            GameAccount.owner_identity_id == identity.id,
            GameAccount.user_id.is_(None),
        )
        .values(user_id=user.id)
        .returning(GameAccount.id)
    )
    claimed_accounts = len(result.scalars().all())

    await session.commit()
    return user, identity, claimed_accounts


@router.get("/auth/discord/login")
async def discord_login(request: Request):
    _require_oauth_config()
    settings = get_settings()

    state = secrets.token_urlsafe(32)
    request.session["discord_oauth_state"] = state

    query = urlencode(
        {
            "client_id": settings.discord_client_id,
            "response_type": "code",
            "redirect_uri": settings.discord_redirect_uri,
            "scope": "identify",
            "state": state,
            "prompt": "consent",
        }
    )
    return RedirectResponse(
        f"{DISCORD_AUTHORIZE_URL}?{query}",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/auth/discord/callback")
async def discord_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    _require_oauth_config()
    settings = get_settings()

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Discord OAuth returned: {error}",
        )

    expected_state = request.session.pop(
        "discord_oauth_state",
        None,
    )
    if (
        not code
        or not state
        or not expected_state
        or not secrets.compare_digest(state, expected_state)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired Discord OAuth state",
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_response = await client.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        if token_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Discord token exchange failed",
            )

        token_data = token_response.json()
        access_token = str(token_data.get("access_token") or "")
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Discord did not return an access token",
            )

        me_response = await client.get(
            DISCORD_ME_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
            },
        )
        if me_response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Discord identity lookup failed",
            )

        discord_profile = me_response.json()

    # We intentionally do not store Discord's OAuth access/refresh token.
    discord_user_id = str(discord_profile.get("id") or "").strip()
    if not discord_user_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Discord returned an invalid user ID",
        )

    user, identity, claimed_accounts = await _claim_discord_identity(
        session,
        discord_user_id=discord_user_id,
    )

    request.session.clear()
    request.session["user_id"] = str(user.id)
    request.session["discord_user_id"] = identity.discord_user_id
    request.session["discord_username"] = str(
        discord_profile.get("global_name")
        or discord_profile.get("username")
        or "Discord User"
    )[:100]
    request.session["claimed_accounts"] = claimed_accounts

    return RedirectResponse(
        f"{settings.frontend_url.rstrip('/')}/?login=success",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/auth/logout")
async def logout(request: Request):
    settings = get_settings()
    request.session.clear()
    return RedirectResponse(
        settings.frontend_url.rstrip("/"),
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/auth-test", response_class=HTMLResponse)
async def auth_test(request: Request) -> HTMLResponse:
    discord_user_id = request.session.get("discord_user_id")
    username = request.session.get("discord_username")
    claimed_accounts = int(request.session.get("claimed_accounts") or 0)

    if not discord_user_id:
        body = """
        <h1>Holodori Gacha — Discord OAuth Test</h1>
        <p>You are not signed in.</p>
        <p><a href="/auth/discord/login">Login with Discord</a></p>
        <p>This local test requests only Discord's <code>identify</code> scope.</p>
        """
    else:
        safe_name = html.escape(str(username or "Discord User"))
        safe_id = html.escape(str(discord_user_id))
        body = f"""
        <h1>Discord identity verified</h1>
        <p><strong>{safe_name}</strong></p>
        <p>Discord ID: <code>{safe_id}</code></p>
        <p>Game accounts auto-claimed this login: <strong>{claimed_accounts}</strong></p>
        <p><a href="/api/me">View /api/me</a></p>
        <p><a href="/auth/logout">Log out</a></p>
        <p>No Discord OAuth token is stored by this application.</p>
        """

    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Holodori Gacha OAuth Test</title>"
        "<style>body{font-family:system-ui;max-width:760px;margin:60px auto;"
        "padding:0 20px;line-height:1.6}code{background:#eee;padding:2px 5px}"
        "a{color:#5865F2}</style></head><body>"
        + body
        + "</body></html>"
    )
