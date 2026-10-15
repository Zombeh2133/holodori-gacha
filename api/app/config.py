from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    postgres_db: str = "holodori_gacha"
    postgres_user: str = "holodori"
    postgres_password: str = "change-me-local-only"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    api_reload: bool = True

    gii_murin_integration_key: str = "local-dev-change-me"

    # Signed browser session cookie. Use a long random value in production.
    session_secret: str = "local-dev-session-secret-change-me"
    session_https_only: bool = False

    # Discord OAuth uses only the `identify` scope.
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = (
        "http://localhost:8000/auth/discord/callback"
    )

    # Local Next.js frontend. OAuth redirects here after verification.
    frontend_url: str = "http://127.0.0.1:3000"

    # Local artwork cache. The holodori-asset-tools package itself is
    # installed editable from the sibling checkout by setup-local.ps1.
    asset_cache_dir: str = "data/asset-cache"
    asset_catalog_path: str = "data/asset-cache/octo_list.json"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        return (
            "postgresql+asyncpg://"
            f"{self.postgres_user}:"
            f"{self.postgres_password}@"
            f"{self.postgres_host}:"
            f"{self.postgres_port}/"
            f"{self.postgres_db}"
        )

    @property
    def discord_oauth_configured(self) -> bool:
        return bool(
            self.discord_client_id.strip()
            and self.discord_client_secret.strip()
            and self.discord_redirect_uri.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
