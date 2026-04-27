from __future__ import annotations

from typing import Any

from odoo_rag.config import Settings as AppSettings
from odoo_rag.tools.definitions import register_default_tools
from odoo_rag.tools.registry import registry

_TOOLS_READY = False


def _ensure_tools_registered() -> None:
    global _TOOLS_READY
    if _TOOLS_READY:
        return
    register_default_tools()
    _TOOLS_READY = True


def execute_tool(app: AppSettings, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Valida y ejecuta una tool registrada. Lanza ValueError en input inválido."""
    _ensure_tools_registered()
    return registry().execute(app, name, payload)

