from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from odoo_rag.config import Settings as AppSettings
from odoo_rag.erp_bridge import execute_erp_action
from odoo_rag.tools.base import Tool
from odoo_rag.tools.registry import registry


class ListQueryInput(BaseModel):
    query: str = Field(..., min_length=1, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)


class CreateInput(BaseModel):
    model: str = Field(..., min_length=1, max_length=120)
    values: dict[str, Any] = Field(default_factory=dict)


class EmailInput(BaseModel):
    target: str = Field(..., min_length=1, max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)


class WorkflowInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    params: dict[str, Any] = Field(default_factory=dict)


class ErpToolInput(BaseModel):
    kind: Literal["read", "write", "archive", "unlink"]
    spec: dict[str, Any] = Field(default_factory=dict)


class ListQueryTool(Tool[ListQueryInput]):
    def run(self, app: AppSettings, inp: ListQueryInput) -> dict[str, Any]:
        from odoo_rag.actions import _execute_list_query_impl  # noqa: WPS433

        return _execute_list_query_impl(app, inp.query, inp.params)


class CreateTool(Tool[CreateInput]):
    def run(self, app: AppSettings, inp: CreateInput) -> dict[str, Any]:
        from odoo_rag.actions import _execute_create_impl  # noqa: WPS433

        new_id = _execute_create_impl(app, inp.model, inp.values)
        return {"id": int(new_id), "model": inp.model}


class EmailTool(Tool[EmailInput]):
    def run(self, app: AppSettings, inp: EmailInput) -> dict[str, Any]:
        from odoo_rag.actions import _execute_email_action_impl  # noqa: WPS433

        return _execute_email_action_impl(app, inp.target, inp.params)


class WorkflowTool(Tool[WorkflowInput]):
    def run(self, app: AppSettings, inp: WorkflowInput) -> dict[str, Any]:
        from odoo_rag.actions import _execute_workflow_impl  # noqa: WPS433

        return _execute_workflow_impl(app, inp.name, inp.params)


class ErpTool(Tool[ErpToolInput]):
    def run(self, app: AppSettings, inp: ErpToolInput) -> dict[str, Any]:
        return execute_erp_action(app, inp.kind, inp.spec)


def register_default_tools() -> None:
    r = registry()
    # Ids elegidos para mapear 1:1 a los endpoints actuales
    r.register(
        ListQueryTool(
            name="list.query",
            description="Ejecuta una consulta predefinida (list queries).",
            input_model=ListQueryInput,
        )
    )
    r.register(
        CreateTool(
            name="odoo.create",
            description="Crea registros en Odoo (lista blanca).",
            input_model=CreateInput,
        )
    )
    r.register(
        EmailTool(
            name="odoo.email",
            description="Envía correo vía Odoo (lista blanca).",
            input_model=EmailInput,
        )
    )
    r.register(
        WorkflowTool(
            name="odoo.workflow",
            description="Ejecuta workflows permitidos (lista blanca).",
            input_model=WorkflowInput,
        )
    )
    r.register(
        ErpTool(
            name="odoo.erp",
            description="Acción ERP genérica restringida (read/write/archive/unlink).",
            input_model=ErpToolInput,
        )
    )

