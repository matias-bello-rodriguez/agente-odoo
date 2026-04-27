"""Implementación modular de acciones.

Este paquete existe para reducir el tamaño de `odoo_rag/actions.py` manteniendo
compatibilidad total: el módulo raíz re-exporta la API pública.
"""

from __future__ import annotations

# Re-exports para compatibilidad con imports históricos:
# `from odoo_rag.actions import ...`

from odoo_rag.actions.chat import structured_chat_reply
from odoo_rag.actions.compat import (  # noqa: F401
    build_missing_partner_suggestion,
    build_missing_vendor_suggestion,
    execute_create,
    execute_email_action,
    execute_list_query,
    execute_workflow,
)
from odoo_rag.actions.legacy_loader import (
    _execute_create_impl,
    _execute_email_action_impl,
    _execute_list_query_impl,
    _execute_workflow_impl,
)

__all__ = [
    "structured_chat_reply",
    "execute_create",
    "execute_list_query",
    "execute_email_action",
    "execute_workflow",
    "build_missing_partner_suggestion",
    "build_missing_vendor_suggestion",
    "_execute_create_impl",
    "_execute_list_query_impl",
    "_execute_email_action_impl",
    "_execute_workflow_impl",
]
