"""Memoria conversacional ligera por usuario.

Persiste los últimos N mensajes (user/assistant) en el caché
(memoria local o Redis) con TTL. Permite armar un prompt con
contexto reciente sin pagar reindexar nada.
"""

from __future__ import annotations

import time
from typing import Any

from odoo_rag.cache import get_cache
from odoo_rag.config import Settings as AppSettings


_VALID_ROLES = frozenset({"user", "assistant", "system"})


def _key(user_id: str) -> str:
    safe = "".join(ch for ch in (user_id or "anon") if ch.isalnum() or ch in "-_")
    return f"memory:{safe or 'anon'}"


def append_message(
    app: AppSettings,
    user_id: str,
    role: str,
    content: str,
    *,
    meta: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    role = role if role in _VALID_ROLES else "user"
    text = (content or "").strip()
    if not text:
        return get_history(app, user_id)
    cache = get_cache(app)
    history = get_history(app, user_id)
    history.append(
        {
            "role": role,
            "content": text[:6000],
            "ts": int(time.time()),
            "meta": meta or {},
        }
    )
    max_n = max(2, int(app.memory_max_messages))
    if len(history) > max_n:
        history = history[-max_n:]
    cache.set(_key(user_id), history, ttl=int(app.memory_ttl_seconds))
    return history


def get_history(app: AppSettings, user_id: str) -> list[dict[str, Any]]:
    cache = get_cache(app)
    raw = cache.get(_key(user_id))
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        role = str(it.get("role") or "user")
        content = str(it.get("content") or "")
        if not content:
            continue
        out.append(
            {
                "role": role if role in _VALID_ROLES else "user",
                "content": content,
                "ts": int(it.get("ts") or 0),
                "meta": it.get("meta") if isinstance(it.get("meta"), dict) else {},
            }
        )
    return out


def clear_history(app: AppSettings, user_id: str) -> bool:
    cache = get_cache(app)
    cache.delete(_key(user_id))
    return True


def to_openai_messages(history: list[dict[str, Any]], *, drop_meta: bool = True) -> list[dict[str, str]]:
    """Convierte el historial al formato esperado por chat.completions."""
    out: list[dict[str, str]] = []
    for it in history:
        role = it.get("role") or "user"
        content = it.get("content") or ""
        if not content:
            continue
        msg = {"role": role, "content": content}
        if not drop_meta and it.get("meta"):
            msg["name"] = "meta"
        out.append(msg)
    return out
