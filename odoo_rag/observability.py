"""Observabilidad: logger estructurado JSON + medición de latencia.

Cada evento se persiste como una línea JSON en `storage/logs/events-YYYY-MM-DD.jsonl`
y se imprime también por consola para inspección rápida.

Uso típico:
    from odoo_rag.observability import log_event, time_block
    with time_block("chat_query") as t:
        ...
        log_event("chat", message=user_msg, user_id=uid, ms=t.ms, ok=True)
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from odoo_rag.config import Settings as AppSettings, load_settings

_LOGGER_NAME = "odoo_rag.events"
_logger_singleton: logging.Logger | None = None
_logger_lock = threading.Lock()


def _file_handler(log_dir: Path) -> logging.Handler | None:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = log_dir / f"events-{today}.jsonl"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def _get_logger(app: AppSettings | None = None) -> logging.Logger:
    global _logger_singleton
    if _logger_singleton is not None:
        return _logger_singleton
    with _logger_lock:
        if _logger_singleton is not None:
            return _logger_singleton
        cfg = app or load_settings()
        log = logging.getLogger(_LOGGER_NAME)
        log.setLevel(logging.INFO)
        log.propagate = False
        if not log.handlers:
            console = logging.StreamHandler()
            console.setFormatter(logging.Formatter("%(message)s"))
            log.addHandler(console)
            if cfg.log_to_file:
                fh = _file_handler(cfg.log_dir)
                if fh is not None:
                    log.addHandler(fh)
        _logger_singleton = log
    return _logger_singleton


def log_event(event: str, **fields: Any) -> dict[str, Any]:
    """Registra un evento estructurado y devuelve el payload (útil para tests)."""
    payload: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
    }
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            payload[k] = v
        else:
            try:
                payload[k] = json.loads(json.dumps(v, default=str))
            except (TypeError, ValueError):
                payload[k] = str(v)
    try:
        _get_logger().info(json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass
    return payload


class _Timer:
    __slots__ = ("start", "ms", "request_id")

    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.ms: float = 0.0
        self.request_id: str = uuid.uuid4().hex[:12]


@contextmanager
def time_block(label: str, **extra: Any) -> Iterator[_Timer]:
    """Mide una sección y emite un evento `<label>` al finalizar."""
    timer = _Timer()
    ok = True
    err: str | None = None
    try:
        yield timer
    except Exception as ex:  # noqa: BLE001
        ok = False
        err = str(ex)
        raise
    finally:
        timer.ms = round((time.perf_counter() - timer.start) * 1000.0, 2)
        log_event(label, ms=timer.ms, ok=ok, error=err, request_id=timer.request_id, **extra)


def tail_recent_events(app: AppSettings, *, limit: int = 100) -> list[dict[str, Any]]:
    """Lee los últimos N eventos del archivo de log de hoy (best-effort)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = app.log_dir / f"events-{today}.jsonl"
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
