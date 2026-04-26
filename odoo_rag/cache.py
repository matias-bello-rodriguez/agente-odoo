"""Caché unificado (memoria local + Redis opcional) con TTL.

Uso típico:
    from odoo_rag.cache import get_cache
    cache = get_cache(settings)
    val = cache.get("clave")
    if val is None:
        val = costoso()
        cache.set("clave", val, ttl=120)

Si `REDIS_URL` está definida en config, se usa Redis (DB key/value JSON-serializable).
Si no, se cae a un dict en memoria thread-safe con expiración.
"""

from __future__ import annotations

import functools
import hashlib
import json
import threading
import time
from typing import Any, Callable, Optional, TypeVar

from odoo_rag.config import Settings as AppSettings

_T = TypeVar("_T")


class _MemoryCache:
    """Caché en proceso (dict + lock) con TTL por entrada."""

    def __init__(self, namespace: str) -> None:
        self._ns = namespace
        self._lock = threading.RLock()
        self._data: dict[str, tuple[float, Any]] = {}

    def _full_key(self, key: str) -> str:
        return f"{self._ns}:{key}"

    def get(self, key: str) -> Any | None:
        full = self._full_key(key)
        now = time.time()
        with self._lock:
            entry = self._data.get(full)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at and expires_at < now:
                self._data.pop(full, None)
                return None
            return value

    def set(self, key: str, value: Any, *, ttl: int) -> None:
        full = self._full_key(key)
        expires_at = time.time() + max(1, ttl) if ttl else 0.0
        with self._lock:
            self._data[full] = (expires_at, value)

    def delete(self, key: str) -> None:
        full = self._full_key(key)
        with self._lock:
            self._data.pop(full, None)

    def clear(self, prefix: str | None = None) -> int:
        full_prefix = self._full_key(prefix or "")
        removed = 0
        with self._lock:
            for k in list(self._data.keys()):
                if not prefix or k.startswith(full_prefix):
                    self._data.pop(k, None)
                    removed += 1
        return removed

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"backend": "memory", "namespace": self._ns, "size": len(self._data)}


class _RedisCache:
    """Adaptador delgado sobre redis-py. Serializa cualquier objeto JSON-able."""

    def __init__(self, url: str, namespace: str) -> None:
        try:
            import redis  # type: ignore[import-not-found]
        except ImportError as ex:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "Para usar REDIS_URL instala redis: pip install redis"
            ) from ex
        self._ns = namespace
        self._client = redis.from_url(url, decode_responses=True)

    def _full_key(self, key: str) -> str:
        return f"{self._ns}:{key}"

    def get(self, key: str) -> Any | None:
        try:
            raw = self._client.get(self._full_key(key))
        except Exception:  # noqa: BLE001
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def set(self, key: str, value: Any, *, ttl: int) -> None:
        try:
            payload = json.dumps(value, default=str)
        except (TypeError, ValueError):
            return
        try:
            self._client.set(self._full_key(key), payload, ex=max(1, ttl))
        except Exception:  # noqa: BLE001
            return

    def delete(self, key: str) -> None:
        try:
            self._client.delete(self._full_key(key))
        except Exception:  # noqa: BLE001
            return

    def clear(self, prefix: str | None = None) -> int:
        pattern = self._full_key((prefix or "") + "*")
        removed = 0
        try:
            for k in self._client.scan_iter(match=pattern, count=200):
                self._client.delete(k)
                removed += 1
        except Exception:  # noqa: BLE001
            return removed
        return removed

    def stats(self) -> dict[str, Any]:
        try:
            info = self._client.info(section="memory")
            used = info.get("used_memory_human", "?")
        except Exception:  # noqa: BLE001
            used = "?"
        return {"backend": "redis", "namespace": self._ns, "memory": used}


_cache_singleton: _MemoryCache | _RedisCache | None = None
_cache_lock = threading.Lock()


def get_cache(app: AppSettings) -> _MemoryCache | _RedisCache:
    """Devuelve la instancia única (Redis si REDIS_URL, si no memoria)."""
    global _cache_singleton
    if _cache_singleton is not None:
        return _cache_singleton
    with _cache_lock:
        if _cache_singleton is None:
            ns = app.cache_namespace or "odoo_rag"
            if app.redis_url:
                try:
                    _cache_singleton = _RedisCache(app.redis_url, ns)
                except RuntimeError:
                    _cache_singleton = _MemoryCache(ns)
            else:
                _cache_singleton = _MemoryCache(ns)
    return _cache_singleton


def reset_cache_for_tests() -> None:
    """Reinicia el singleton (útil en tests)."""
    global _cache_singleton
    with _cache_lock:
        _cache_singleton = None


def make_key(*parts: Any) -> str:
    """Genera una clave estable serializando los argumentos."""
    raw = json.dumps(parts, default=str, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def cached(
    *,
    ttl: int | None = None,
    key_prefix: str | None = None,
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorador para funciones puras que reciben `AppSettings` como primer argumento.

    Ejemplo:
        @cached(ttl=120, key_prefix="products")
        def get_products(app, only_active=True): ...
    """

    def deco(fn: Callable[..., _T]) -> Callable[..., _T]:
        prefix = key_prefix or fn.__qualname__

        @functools.wraps(fn)
        def wrapper(app: AppSettings, *args: Any, **kwargs: Any) -> _T:
            cache = get_cache(app)
            key = f"{prefix}:{make_key(args, kwargs)}"
            hit = cache.get(key)
            if hit is not None:
                return hit  # type: ignore[return-value]
            value = fn(app, *args, **kwargs)
            cache.set(key, value, ttl=ttl or app.cache_default_ttl)
            return value

        return wrapper

    return deco


def invalidate_prefix(app: AppSettings, prefix: str) -> int:
    """Borra todas las entradas que empiezan con el prefijo (útil tras escrituras)."""
    cache = get_cache(app)
    return cache.clear(prefix)
