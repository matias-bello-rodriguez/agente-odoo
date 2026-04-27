from __future__ import annotations

from typing import Any

from odoo_rag.config import Settings as AppSettings
from odoo_rag.tools.executor import execute_tool


def execute_create(app: AppSettings, model: str, values: dict[str, Any]) -> int:
    """Crea registros en Odoo usando la capa de tools (compat API)."""
    out = execute_tool(app, "odoo.create", {"model": model, "values": values})
    return int(out["id"])


def execute_list_query(
    app: AppSettings, query: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Ejecuta consultas de lista (compat API)."""
    return execute_tool(app, "list.query", {"query": query, "params": params or {}})


def execute_email_action(app: AppSettings, target: str, params: dict[str, Any]) -> dict[str, Any]:
    """Envía correos vía Odoo (compat API)."""
    return execute_tool(app, "odoo.email", {"target": target, "params": params})


def execute_workflow(app: AppSettings, name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta workflows permitidos (compat API)."""
    return execute_tool(app, "odoo.workflow", {"name": name, "params": params})


def build_missing_partner_suggestion(partner_name: str) -> dict[str, Any]:
    guessed_name = str(partner_name or "").strip()
    return {
        "operation": "create",
        "model": "res.partner",
        "values": {"name": guessed_name, "is_company": True},
        "summary": f"Crear cliente {guessed_name}" if guessed_name else "Crear cliente",
    }


def build_missing_vendor_suggestion(vendor_name: str) -> dict[str, Any]:
    guessed_name = str(vendor_name or "").strip()
    return {
        "operation": "create",
        "model": "res.partner",
        "values": {"name": guessed_name, "is_company": True},
        "summary": f"Crear proveedor {guessed_name}" if guessed_name else "Crear proveedor",
    }

