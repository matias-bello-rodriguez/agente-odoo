"""Interfaz web (FastAPI) para consultar el RAG y reindexar Odoo."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from odoo_rag.actions import (
    build_missing_partner_suggestion,
    execute_create,
    structured_chat_reply,
)
from odoo_rag.odoo_urls import odoo_links_after_create, odoo_links_after_product_setup
from odoo_rag.product_setup import run_product_setup
from odoo_rag.config import load_settings
from odoo_rag.rag import build_or_rebuild_index

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Odoo RAG", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    top_k: int = Field(default=6, ge=1, le=20)


class RebuildBody(BaseModel):
    confirm: bool = False


class ActionExecuteBody(BaseModel):
    model: str = Field(..., min_length=1, max_length=120)
    operation: str = Field(default="create", max_length=20)
    values: dict = Field(default_factory=dict)


class ProductSetupBody(BaseModel):
    plan: dict = Field(default_factory=dict)


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
    }


@app.post("/api/chat")
async def api_chat(body: ChatBody) -> dict:
    settings = load_settings()

    def run() -> dict:
        return structured_chat_reply(
            settings, body.message.strip(), top_k=body.top_k
        )

    try:
        return await asyncio.to_thread(run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/action/execute")
async def api_action_execute(body: ActionExecuteBody) -> dict:
    if body.operation != "create":
        raise HTTPException(
            status_code=400, detail="Solo se admite operation=create en esta versión."
        )
    settings = load_settings()

    def run() -> int:
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
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Odoo: {e!s}" if str(e) else "Error al crear en Odoo"
        ) from e
    return {
        "ok": True,
        "id": new_id,
        "model": body.model,
        "odoo_links": odoo_links_after_create(settings.odoo_url, body.model, new_id),
    }


@app.post("/api/action/product-setup")
async def api_product_setup(body: ProductSetupBody) -> dict:
    settings = load_settings()

    def run() -> dict:
        return run_product_setup(settings, body.plan)

    try:
        result = await asyncio.to_thread(run)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    links = odoo_links_after_product_setup(
        settings.odoo_url,
        product_tmpl_id=int(result["product_tmpl_id"]),
        product_product_id=int(result["product_product_id"]),
        orderpoint_id=result.get("orderpoint_id"),
    )
    return {"ok": True, **result, "odoo_links": links}


@app.post("/api/index/rebuild")
async def api_rebuild(body: RebuildBody) -> dict:
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Envía confirm: true para reconstruir el índice (operación costosa).",
        )

    settings = load_settings()

    def run() -> None:
        build_or_rebuild_index(settings, rebuild=True)

    try:
        await asyncio.to_thread(run)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "storage": str(settings.odoo_rag_storage_dir.resolve())}


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
