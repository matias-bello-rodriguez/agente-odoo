"""Interfaz web (FastAPI) para consultar el RAG y reindexar Odoo."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from odoo_rag.actions import (
    build_missing_partner_suggestion,
    build_missing_vendor_suggestion,
    execute_create,
    execute_email_action,
    execute_list_query,
    execute_workflow,
    structured_chat_reply,
)
from odoo_rag.alerts import list_alert_ids, run_all_alerts
from odoo_rag.cache import get_cache, invalidate_prefix
from odoo_rag.erp_bridge import execute_erp_action
from odoo_rag.memory import append_message, clear_history, get_history
from odoo_rag.observability import log_event, tail_recent_events, time_block
from odoo_rag.odoo_urls import odoo_links_after_create, odoo_links_after_product_setup
from odoo_rag.permissions import (
    PermissionError as RolePermissionError,
    describe_role_capabilities,
    require,
    resolve_role_from_request,
)
from odoo_rag.product_setup import run_product_setup
from odoo_rag.config import load_settings
from odoo_rag.rag import build_or_rebuild_index
from odoo_rag.reports import monthly_sales_report, summarize_data
from odoo_rag.suggestions import SUGGEST_KINDS, suggest

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Odoo RAG", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers comunes
# ---------------------------------------------------------------------------


def _resolve_role(request: Request, explicit: str | None = None) -> str:
    settings = load_settings()
    return resolve_role_from_request(settings, dict(request.headers), explicit)


def _resolve_user_id(request: Request, body_user_id: str | None = None) -> str:
    if body_user_id:
        return str(body_user_id).strip()[:80] or "anon"
    headers = request.headers
    raw = headers.get("X-User-Id") or headers.get("x-user-id") or "anon"
    return str(raw).strip()[:80] or "anon"


def _enforce(role: str, action: str) -> None:
    try:
        require(role, action)
    except RolePermissionError as ex:
        raise HTTPException(status_code=403, detail=str(ex)) from ex


# ---------------------------------------------------------------------------
# Modelos de body
# ---------------------------------------------------------------------------


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    top_k: int = Field(default=6, ge=1, le=20)
    user_id: str | None = Field(default=None, max_length=80)


class RebuildBody(BaseModel):
    confirm: bool = False


class ActionExecuteBody(BaseModel):
    model: str = Field(..., min_length=1, max_length=120)
    operation: str = Field(default="create", max_length=20)
    values: dict = Field(default_factory=dict)


class ProductSetupBody(BaseModel):
    plan: dict = Field(default_factory=dict)


class ActionListBody(BaseModel):
    operation: str = Field(default="list", max_length=20)
    query: str = Field(..., min_length=1, max_length=120)
    params: dict = Field(default_factory=dict)
    summarize: bool = Field(default=False)


class ActionEmailBody(BaseModel):
    operation: str = Field(default="email", max_length=20)
    target: str = Field(..., min_length=1, max_length=40)
    params: dict = Field(default_factory=dict)


class ActionWorkflowBody(BaseModel):
    operation: str = Field(default="workflow", max_length=20)
    name: str = Field(..., min_length=1, max_length=80)
    params: dict = Field(default_factory=dict)


class ErpActionBody(BaseModel):
    kind: str = Field(..., min_length=1, max_length=16)
    spec: dict = Field(default_factory=dict)


class SummaryBody(BaseModel):
    intent: str = Field(default="Resumen", min_length=1, max_length=240)
    data: dict | list = Field(default_factory=dict)


class ReportBody(BaseModel):
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    write_summary: bool = Field(default=True)


class AlertsBody(BaseModel):
    only: list[str] | None = None
    use_cache: bool = True


class MemoryClearBody(BaseModel):
    user_id: str | None = Field(default=None, max_length=80)


class CacheClearBody(BaseModel):
    prefix: str = Field(default="", max_length=120)


# ---------------------------------------------------------------------------
# Health / capabilities
# ---------------------------------------------------------------------------


@app.get("/api/health")
def api_health() -> dict:
    s = load_settings()
    store = s.odoo_rag_storage_dir.resolve()
    indexed = store.exists() and any(store.iterdir())
    return {
        "ok": True,
        "indexed": indexed,
        "odoo_url": s.odoo_url,
        "odoo_db": s.odoo_db,
        "openai_configured": bool(s.openai_api_key),
        "cache_backend": "redis" if s.redis_url else "memory",
        "permissions_enforced": s.enforce_permissions,
        "version": "0.3.0",
    }


@app.get("/api/me")
def api_me(request: Request) -> dict:
    role = _resolve_role(request)
    return {"ok": True, "user_id": _resolve_user_id(request), **describe_role_capabilities(role)}


# ---------------------------------------------------------------------------
# Chat con memoria + observabilidad
# ---------------------------------------------------------------------------


@app.post("/api/chat")
async def api_chat(body: ChatBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "chat")
    user_id = _resolve_user_id(request, body.user_id)

    user_message = body.message.strip()

    def run() -> dict:
        with time_block("chat", user_id=user_id, top_k=body.top_k, message=user_message[:240]):
            append_message(settings, user_id, "user", user_message)
            history = get_history(settings, user_id)
            payload = structured_chat_reply(settings, user_message, top_k=body.top_k)
            assistant_text = str(payload.get("reply") or "")
            meta: dict[str, Any] = {}
            draft = payload.get("draft_action")
            if draft:
                meta["draft_action"] = {
                    "operation": draft.get("operation"),
                    "summary": draft.get("summary"),
                }
            append_message(settings, user_id, "assistant", assistant_text, meta=meta)
            payload["history"] = history + [
                {"role": "assistant", "content": assistant_text, "meta": meta}
            ]
            payload["user_id"] = user_id
            return payload

    try:
        return await asyncio.to_thread(run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log_event("chat.error", user_id=user_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/memory")
def api_memory_get(request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "chat")
    uid = _resolve_user_id(request)
    return {"ok": True, "user_id": uid, "history": get_history(settings, uid)}


@app.post("/api/memory/clear")
def api_memory_clear(body: MemoryClearBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "memory.clear")
    uid = _resolve_user_id(request, body.user_id)
    clear_history(settings, uid)
    log_event("memory.clear", user_id=uid)
    return {"ok": True, "user_id": uid}


# ---------------------------------------------------------------------------
# Acciones operativas
# ---------------------------------------------------------------------------


@app.post("/api/action/execute")
async def api_action_execute(body: ActionExecuteBody, request: Request) -> dict:
    if body.operation != "create":
        raise HTTPException(
            status_code=400, detail="Solo se admite operation=create en esta versión."
        )
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "create")

    def run() -> int:
        with time_block("action.create", model=body.model, role=role):
            return execute_create(settings, body.model, body.values)

    try:
        new_id = await asyncio.to_thread(run)
    except ValueError as e:
        msg = str(e)
        if msg.startswith("PARTNER_NOT_FOUND::"):
            partner_name = msg.split("::", 1)[1].strip()
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PARTNER_NOT_FOUND",
                    "message": (
                        f'No encontré un contacto con nombre parecido a «{partner_name}». '
                        "¿Quieres crear el cliente primero?"
                    ),
                    "partner_name": partner_name,
                    "suggested_action": build_missing_partner_suggestion(partner_name),
                },
            ) from e
        if msg.startswith("VENDOR_NOT_FOUND::"):
            vendor_name = msg.split("::", 1)[1].strip()
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "VENDOR_NOT_FOUND",
                    "message": (
                        f'No encontré un proveedor con nombre parecido a «{vendor_name}». '
                        "¿Quieres crearlo primero?"
                    ),
                    "vendor_name": vendor_name,
                    "suggested_action": build_missing_vendor_suggestion(vendor_name),
                },
            ) from e
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log_event("action.create.error", model=body.model, error=str(e))
        raise HTTPException(
            status_code=500, detail=f"Odoo: {e!s}" if str(e) else "Error al crear en Odoo"
        ) from e
    invalidate_prefix(settings, "suggest")
    invalidate_prefix(settings, "alerts")
    return {
        "ok": True,
        "id": new_id,
        "model": body.model,
        "odoo_links": odoo_links_after_create(settings.odoo_url, body.model, new_id),
    }


@app.post("/api/action/product-setup")
async def api_product_setup(body: ProductSetupBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "create.product_setup")

    def run() -> dict:
        with time_block("action.product_setup", role=role):
            return run_product_setup(settings, body.plan)

    try:
        result = await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    invalidate_prefix(settings, "suggest")
    invalidate_prefix(settings, "alerts")
    links = odoo_links_after_product_setup(
        settings.odoo_url,
        product_tmpl_id=int(result["product_tmpl_id"]),
        product_product_id=int(result["product_product_id"]),
        orderpoint_id=result.get("orderpoint_id"),
    )
    return {"ok": True, **result, "odoo_links": links}


@app.post("/api/action/list")
async def api_action_list(body: ActionListBody, request: Request) -> dict:
    if body.operation != "list":
        raise HTTPException(status_code=400, detail="Solo se admite operation=list.")
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "list")

    def run() -> dict:
        with time_block("action.list", query=body.query):
            data = execute_list_query(settings, body.query, body.params)
            if body.summarize:
                summary = summarize_data(settings, data, intent=str(data.get("title") or body.query))
                data["summary"] = summary.get("summary", "")
                data["summary_used_llm"] = summary.get("used_llm", False)
            return data

    try:
        result = await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, **result}


@app.post("/api/action/email")
async def api_action_email(body: ActionEmailBody, request: Request) -> dict:
    if body.operation != "email":
        raise HTTPException(status_code=400, detail="Solo se admite operation=email.")
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "email")

    def run() -> dict:
        with time_block("action.email", target=body.target):
            return execute_email_action(settings, body.target, body.params)

    try:
        result = await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, **result}


@app.post("/api/action/erp")
async def api_action_erp(body: ErpActionBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    kind = (body.kind or "").strip().lower()
    action_key = {
        "read": "erp.read",
        "write": "erp.write",
        "archive": "erp.archive",
        "unlink": "erp.unlink",
    }.get(kind, "erp.read")
    _enforce(role, action_key)

    def run() -> dict:
        with time_block(f"action.erp.{kind}", role=role):
            return execute_erp_action(settings, body.kind, body.spec)

    try:
        result = await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    if kind in {"write", "archive", "unlink"}:
        invalidate_prefix(settings, "suggest")
        invalidate_prefix(settings, "alerts")
    return {"ok": True, **result}


@app.post("/api/action/workflow")
async def api_action_workflow(body: ActionWorkflowBody, request: Request) -> dict:
    if body.operation != "workflow":
        raise HTTPException(status_code=400, detail="Solo se admite operation=workflow.")
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "workflow")

    def run() -> dict:
        with time_block("action.workflow", name=body.name):
            return execute_workflow(settings, body.name, body.params)

    try:
        result = await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    invalidate_prefix(settings, "suggest")
    invalidate_prefix(settings, "alerts")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Alertas / reportes / sugerencias / observabilidad
# ---------------------------------------------------------------------------


@app.get("/api/alerts")
async def api_alerts_get(request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "alerts.read")

    def run() -> dict:
        return run_all_alerts(settings, use_cache=True)

    try:
        return await asyncio.to_thread(run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/alerts/run")
async def api_alerts_run(body: AlertsBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "alerts.run")

    def run() -> dict:
        return run_all_alerts(settings, use_cache=body.use_cache, only=body.only)

    try:
        return await asyncio.to_thread(run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/alerts/ids")
def api_alerts_ids(request: Request) -> dict:
    role = _resolve_role(request)
    _enforce(role, "alerts.read")
    return {"ok": True, "ids": list_alert_ids()}


@app.get("/api/suggest")
async def api_suggest(
    request: Request,
    kind: str,
    q: str = "",
    limit: int = 8,
) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "suggest")
    if kind not in SUGGEST_KINDS:
        raise HTTPException(status_code=400, detail=f"kind debe ser uno de {SUGGEST_KINDS}.")

    def run() -> dict:
        return suggest(settings, kind=kind, query=q, limit=limit)

    try:
        return await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/summary")
async def api_summary(body: SummaryBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "summary")

    def run() -> dict:
        return summarize_data(settings, body.data, intent=body.intent)

    try:
        return await asyncio.to_thread(run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/report/sales")
async def api_report_sales(body: ReportBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "report.read")

    def run() -> dict:
        return monthly_sales_report(
            settings,
            year=body.year,
            month=body.month,
            write_summary=body.write_summary,
        )

    try:
        return await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/observability/recent")
def api_observability_recent(request: Request, limit: int = 100) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "report.read")
    events = tail_recent_events(settings, limit=max(1, min(500, int(limit))))
    return {"ok": True, "count": len(events), "events": events}


@app.get("/api/cache/stats")
def api_cache_stats(request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "report.read")
    return {"ok": True, **get_cache(settings).stats()}


@app.post("/api/cache/clear")
def api_cache_clear(body: CacheClearBody, request: Request) -> dict:
    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "memory.clear")
    removed = invalidate_prefix(settings, body.prefix or "")
    log_event("cache.clear", prefix=body.prefix, removed=removed)
    return {"ok": True, "removed": removed, "prefix": body.prefix}


# ---------------------------------------------------------------------------
# Index rebuild
# ---------------------------------------------------------------------------


@app.post("/api/index/rebuild")
async def api_rebuild(body: RebuildBody, request: Request) -> dict:
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Envía confirm: true para reconstruir el índice (operación costosa).",
        )

    settings = load_settings()
    role = _resolve_role(request)
    _enforce(role, "index.rebuild")

    def run() -> None:
        with time_block("index.rebuild", role=role):
            build_or_rebuild_index(settings, rebuild=True)

    try:
        await asyncio.to_thread(run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "storage": str(settings.odoo_rag_storage_dir.resolve())}


# ---------------------------------------------------------------------------
# Manejo de errores de permiso (defensa en profundidad)
# ---------------------------------------------------------------------------


@app.exception_handler(RolePermissionError)
async def _permission_exception_handler(_: Request, exc: RolePermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Static SPA
# ---------------------------------------------------------------------------


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def spa_root() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=503,
            detail="Interfaz estática no encontrada (falta odoo_rag/static/index.html).",
        )
    return FileResponse(index)
