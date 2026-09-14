from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FLUXUS_", extra="ignore")

    database_url: str = Field(
        default="postgresql+asyncpg://fluxus:change-me@localhost:5433/fluxus",
        alias="DATABASE_URL",
    )
    secret_key: str = "dev-secret-change-me-please-32-chars"
    browser_url: str = "http://browser:3000"
    static_dir: str = "/app/static"
    access_token_hours: int = 24 * 7
    default_fetch_interval_min: int = 30
    min_fetch_interval_min: int = 10
    max_fetch_interval_min: int = 24 * 60
    worker_poll_seconds: float = 2.0
    worker_concurrency: int = 6
    http_timeout_seconds: float = 20.0
    user_agent: str = "Fluxus/0.1 (+https://github.com/fluxus-rss)"
    log_level: str = "INFO"


PLACEHOLDER_SECRETS = {"dev-secret-change-me-please-32-chars", "change-me-to-a-long-random-string", "change-me", "changeme"}


def validate_runtime_config(settings: "Settings") -> None:
    """Refuse de démarrer avec un secret d'exemple : il signerait les sessions et chiffrerait les clés LLM."""
    secret = settings.secret_key
    if secret in PLACEHOLDER_SECRETS or len(secret) < 32:
        raise RuntimeError(
            "FLUXUS_SECRET_KEY doit être une valeur aléatoire d'au moins 32 caractères "
            "(ex. `python -c 'import secrets; print(secrets.token_urlsafe(48))'`)."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
