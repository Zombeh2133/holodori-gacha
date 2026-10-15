from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .api.assets import router as assets_router
from .api.auth import router as auth_router
from .api.dashboard import router as dashboard_router
from .api.health import router as health_router
from .api.imports import router as import_router
from .api.integrations import router as integrations_router
from .api.me import router as me_router
from .config import get_settings
from .database import create_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_schema()
    yield


settings = get_settings()

app = FastAPI(
    title="Holodori Gacha API",
    version="0.7.0",
    description=(
        "Standalone Holodori gacha-history API — local development build."
    ),
    lifespan=lifespan,
)

# Signed cookie contains only site session identity/state. Discord OAuth tokens
# are never placed in the cookie and are never persisted.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="holodori_gacha_session",
    same_site="none",
    https_only=settings.session_https_only,
    max_age=60 * 60 * 24 * 30,
)

# Prepares the API for the local Next.js frontend in a later milestone.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url.rstrip("/"),
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict:
    return {
        "service": "holodori-gacha-api",
        "version": "0.7.0",
        "status": "ok",
        "oauth_test": "/auth-test",
    }


app.include_router(health_router)
app.include_router(assets_router)
app.include_router(auth_router)
app.include_router(me_router)
app.include_router(dashboard_router)
app.include_router(import_router)
app.include_router(integrations_router)


