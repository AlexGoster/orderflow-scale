from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "OrderFlow Scale"
    version: str = "2.0.0"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://orderflow:orderflow@localhost:5432/orderflow"

    secret_key: str = "dev-only-secret-key-change-me-in-production-32b"
    access_token_expire_minutes: int = 30
    algorithm: str = "HS256"

    cors_origins: list[str] = ["http://localhost:3000"]

    # --- cache (cache-aside) -------------------------------------------------
    cache_backend: str = "memory"  # memory | redis
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 60
    # версия схемы ключа: инвалидация = бамп версии (старые ключи просто протухают)
    cache_version: str = "v1"

    # --- rate limiting (sliding window) -------------------------------------
    rate_limit_backend: str = "memory"  # memory | redis
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60
    rate_limit_paths: list[str] = ["/api/v1/products"]

    # --- observability -------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    @property
    def cache_key_prefix(self) -> str:
        return f"{self.cache_version}:{self.app_name.lower().replace(' ', '-')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
