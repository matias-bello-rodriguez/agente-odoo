from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from odoo_rag.config import Settings as AppSettings


@lru_cache(maxsize=1)
def _load_legacy_actions_py() -> ModuleType:
    """
    Carga `odoo_rag/actions.py` (archivo legacy) aunque exista el paquete `odoo_rag.actions/`.

    Históricamente, muchas partes del código importaban `odoo_rag.actions` como módulo.
    Al crear el paquete `odoo_rag/actions/`, ese nombre pasó a resolver al paquete, no al archivo.
    Este loader mantiene compatibilidad sin copiar el archivo legacy ni renombrarlo.
    """

    legacy_path = Path(__file__).resolve().parents[1] / "actions.py"
    if not legacy_path.is_file():
        raise ImportError(f"No existe actions.py legacy en {legacy_path}")

    module_name = "odoo_rag._actions_legacy_py"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(module_name, str(legacy_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"No pude cargar spec para {legacy_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _execute_list_query_impl(
    app: AppSettings, query: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    legacy = _load_legacy_actions_py()
    fn = cast(Any, getattr(legacy, "_execute_list_query_impl"))
    return fn(app, query, params)


def _execute_create_impl(app: AppSettings, model: str, values: dict[str, Any]) -> int:
    legacy = _load_legacy_actions_py()
    fn = cast(Any, getattr(legacy, "_execute_create_impl"))
    return int(fn(app, model, values))


def _execute_email_action_impl(
    app: AppSettings, target: str, params: dict[str, Any]
) -> dict[str, Any]:
    legacy = _load_legacy_actions_py()
    fn = cast(Any, getattr(legacy, "_execute_email_action_impl"))
    return fn(app, target, params)


def _execute_workflow_impl(app: AppSettings, name: str, params: dict[str, Any]) -> dict[str, Any]:
    legacy = _load_legacy_actions_py()
    fn = cast(Any, getattr(legacy, "_execute_workflow_impl"))
    return fn(app, name, params)

