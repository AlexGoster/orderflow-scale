"""Абстракция кэша + cache-aside для каталога товаров.

Два бэкенда:

* :class:`InMemoryCacheBackend` — для dev/тестов (Docker и Redis-сервер недоступны);
* :class:`RedisCacheBackend` — для прода.

Оба реализуют один интерфейс :class:`CacheBackend`, поэтому весь код выше ними
(routes / сервисы) не знает, где именно лежит кэш.
"""

import time
from collections.abc import Callable
from typing import Protocol

from fastapi import Depends
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.metrics import record_cache_hit, record_cache_miss


class CacheBackend(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl: int | None = None) -> None: ...

    async def delete(self, *keys: str) -> int: ...

    async def delete_prefix(self, prefix: str) -> int: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


class InMemoryCacheBackend:
    """Словарь с TTL. Эмулирует Redis для тестов и локальной разработки."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._data: dict[str, tuple[str, float | None]] = {}
        self._clock = clock

    def _fresh(self, expires_at: float | None) -> bool:
        return expires_at is None or expires_at > self._clock()

    def _alive(self, key: str) -> str | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if not self._fresh(expires_at):
            del self._data[key]
            return None
        return value

    async def get(self, key: str) -> str | None:
        return self._alive(key)

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        expires_at = None if ttl is None else self._clock() + ttl
        self._data[key] = (value, expires_at)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self._data.pop(key, None) is not None:
                removed += 1
        return removed

    async def delete_prefix(self, prefix: str) -> int:
        stale = [key for key in list(self._data) if key.startswith(prefix)]
        return await self.delete(*stale)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        self._data.clear()

    @property
    def stored_keys(self) -> list[str]:
        return sorted(self._data)


class RedisCacheBackend:
    """Redis (cache-aside, единый TTL, префиксная инвалидация через SCAN)."""

    def __init__(self, url: str, client: Redis | None = None) -> None:
        self._redis: Redis = (
            client if client is not None else Redis.from_url(url, decode_responses=True)
        )

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str, ttl: int | None = None) -> None:
        await self._redis.set(key, value, ex=ttl)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return int(await self._redis.delete(*keys))

    async def delete_prefix(self, prefix: str) -> int:
        removed = 0
        async for key in self._redis.scan_iter(match=f"{prefix}*"):
            removed += int(await self._redis.delete(key))
        return removed

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001 — кэш не должен ронять запрос
            return False

    async def close(self) -> None:
        await self._redis.aclose()


class ProductCache:
    """Cache-aside для ``GET /products`` и ``GET /products/{id}``.

    Ключи содержат версию схемы (``settings.cache_version``): бамп версии
    делает все старые ключи «мёртвыми» без какого-либо перебора.
    """

    def __init__(
        self,
        backend: CacheBackend,
        *,
        prefix: str,
        ttl: int,
        cache_name: str = "products",
    ) -> None:
        self._backend = backend
        self._prefix = prefix
        self._ttl = ttl
        self._cache_name = cache_name

    @property
    def namespace(self) -> str:
        return f"{self._prefix}:products"

    @property
    def backend(self) -> CacheBackend:
        return self._backend

    def list_key(self, *, offset: int, limit: int, category: str | None = None) -> str:
        return f"{self.namespace}:list:{category or 'all'}:{offset}:{limit}"

    def item_key(self, product_id: int) -> str:
        return f"{self.namespace}:item:{product_id}"

    async def get(self, key: str) -> str | None:
        value = await self._backend.get(key)
        if value is None:
            record_cache_miss(self._cache_name)
        else:
            record_cache_hit(self._cache_name)
        return value

    async def set(self, key: str, value: str) -> None:
        if self._ttl <= 0:
            # CACHE_TTL_SECONDS <= 0 — кэш выключен (A/B-замеры, отладка).
            return
        await self._backend.set(key, value, self._ttl)

    async def invalidate(self) -> int:
        """Инвалидация по событию: удаляем все ключи каталога одним префиксом."""
        return await self._backend.delete_prefix(self.namespace)


_cache_backend: CacheBackend | None = None


def build_cache_backend(settings: Settings | None = None) -> CacheBackend:
    cfg = settings or get_settings()
    if cfg.cache_backend == "redis":
        return RedisCacheBackend(cfg.redis_url)
    return InMemoryCacheBackend()


def get_cache() -> CacheBackend:
    global _cache_backend
    if _cache_backend is None:
        _cache_backend = build_cache_backend()
    return _cache_backend


def get_product_cache(cache: CacheBackend = Depends(get_cache)) -> ProductCache:
    cfg = get_settings()
    return ProductCache(cache, prefix=cfg.cache_key_prefix, ttl=cfg.cache_ttl_seconds)


async def dispose_cache() -> None:
    global _cache_backend
    if _cache_backend is not None:
        await _cache_backend.close()
        _cache_backend = None
