"""Control de permisos por rol.

Cada acción del agente declara su tipo (`create`, `erp.write`, `erp.unlink`, ...)
y los roles permitidos. El backend valida ANTES de ejecutar.

Modelo simple — no reemplaza los grupos de Odoo, los complementa: protege
las acciones que el LLM podría proponer.
"""

from __future__ import annotations

from typing import Any

from odoo_rag.config import Settings as AppSettings


# Roles disponibles (de menor a mayor privilegio)
ROLE_VIEWER = "viewer"      # solo consultas (ERP read, list, suggest, alerts)
ROLE_OPERATOR = "operator"  # crea/edita registros, manda correos, workflows
ROLE_ADMIN = "admin"        # archive/unlink, rebuild, reportes ejecutivos


_ROLE_ORDER = {ROLE_VIEWER: 0, ROLE_OPERATOR: 1, ROLE_ADMIN: 2}


# Mapa acción -> nivel mínimo requerido.
ACTION_MIN_ROLE: dict[str, str] = {
    # Lecturas
    "chat": ROLE_VIEWER,
    "suggest": ROLE_VIEWER,
    "alerts.read": ROLE_VIEWER,
    "alerts.run": ROLE_OPERATOR,
    "list": ROLE_VIEWER,
    "report.read": ROLE_VIEWER,
    "summary": ROLE_VIEWER,
    "erp.read": ROLE_VIEWER,
    # Escrituras suaves
    "create": ROLE_OPERATOR,
    "create.product_setup": ROLE_OPERATOR,
    "erp.write": ROLE_OPERATOR,
    "email": ROLE_OPERATOR,
    "workflow": ROLE_OPERATOR,
    # Operaciones destructivas / costosas
    "erp.archive": ROLE_ADMIN,
    "erp.unlink": ROLE_ADMIN,
    "index.rebuild": ROLE_ADMIN,
    "memory.clear": ROLE_OPERATOR,
}


# Lista negra adicional: acciones que ciertos roles NUNCA pueden hacer.
NEVER_ALLOWED: dict[str, set[str]] = {
    ROLE_VIEWER: {"erp.unlink", "erp.archive", "index.rebuild"},
}


class PermissionError(RuntimeError):
    """Se lanza cuando el rol del usuario no puede ejecutar la acción."""


def normalize_role(raw: str | None, *, default: str = ROLE_OPERATOR) -> str:
    if not raw:
        return default
    role = str(raw).strip().lower()
    if role in _ROLE_ORDER:
        return role
    aliases = {
        "read": ROLE_VIEWER,
        "readonly": ROLE_VIEWER,
        "lectura": ROLE_VIEWER,
        "user": ROLE_OPERATOR,
        "vendedor": ROLE_OPERATOR,
        "operador": ROLE_OPERATOR,
        "manager": ROLE_ADMIN,
        "owner": ROLE_ADMIN,
        "root": ROLE_ADMIN,
    }
    return aliases.get(role, default)


def can_execute(role: str, action: str) -> bool:
    role = normalize_role(role)
    needed = ACTION_MIN_ROLE.get(action, ROLE_OPERATOR)
    if action in NEVER_ALLOWED.get(role, set()):
        return False
    return _ROLE_ORDER[role] >= _ROLE_ORDER[needed]


def require(role: str, action: str) -> None:
    """Versión imperativa: lanza PermissionError si no puede."""
    if not can_execute(role, action):
        raise PermissionError(
            f"El rol '{role}' no puede ejecutar la acción '{action}'."
        )


def resolve_role_from_request(
    app: AppSettings,
    headers: dict[str, str] | None = None,
    explicit_role: str | None = None,
) -> str:
    """Determina el rol efectivo a partir de headers/parámetros.

    En desarrollo (`enforce_permissions=False`) siempre devuelve admin
    para no estorbar; en producción exige el header `X-User-Role`.
    """
    if not app.enforce_permissions:
        return ROLE_ADMIN
    if explicit_role:
        return normalize_role(explicit_role)
    headers = headers or {}
    raw = headers.get("X-User-Role") or headers.get("x-user-role")
    return normalize_role(raw, default=app.default_user_role)


def describe_role_capabilities(role: str) -> dict[str, Any]:
    role = normalize_role(role)
    allowed: list[str] = []
    denied: list[str] = []
    for action in ACTION_MIN_ROLE:
        (allowed if can_execute(role, action) else denied).append(action)
    return {
        "role": role,
        "level": _ROLE_ORDER[role],
        "allowed_actions": sorted(allowed),
        "denied_actions": sorted(denied),
    }
