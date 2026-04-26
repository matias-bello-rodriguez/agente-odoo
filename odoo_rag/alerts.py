"""Alertas proactivas sobre el estado de Odoo.

Se exponen tres alertas listas para usar:
- Bajo stock (productos `qty_available` por debajo del umbral configurable).
- Facturas vencidas y todavía pendientes de cobro.
- Facturas en borrador antiguas (>= 7 días).

Cada `check_*` devuelve un dict normalizado con `count`, `severity`, `items`,
y `summary`. `run_all_alerts` agrupa todo y cachea el resultado por
`alert_cache_ttl` segundos para no consultar Odoo en cada refresh.
"""

from __future__ import annotations

import xmlrpc.client
from datetime import date, timedelta
from typing import Any

from odoo_rag.cache import get_cache, make_key
from odoo_rag.config import Settings as AppSettings
from odoo_rag.observability import log_event, time_block
from odoo_rag.odoo_client import OdooXmlRpc
from odoo_rag.odoo_urls import link_record


SEVERITY_OK = "ok"
SEVERITY_WARN = "warning"
SEVERITY_ERROR = "error"


def _safe_search_read(
    client: OdooXmlRpc,
    model: str,
    domain: list,
    fields: list[str],
    *,
    limit: int,
    order: str | None = None,
) -> list[dict[str, Any]]:
    try:
        return client.search_read(model, domain, fields, limit=limit, order=order)
    except xmlrpc.client.Fault:
        return []


def check_low_stock(app: AppSettings, *, client: OdooXmlRpc | None = None) -> dict[str, Any]:
    threshold = float(app.alert_low_stock_threshold)
    odoo = client or OdooXmlRpc(app)
    rows = _safe_search_read(
        odoo,
        "product.product",
        [["active", "=", True], ["type", "=", "consu"]],
        ["id", "name", "default_code", "qty_available", "list_price"],
        limit=400,
        # Odoo 19 no permite ORDER BY en campos no almacenados como qty_available.
        # Ordenamos luego en Python para evitar "Cannot convert ... to SQL".
        order="id desc",
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        try:
            qty = float(r.get("qty_available") or 0.0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= threshold:
            items.append(
                {
                    "id": int(r.get("id") or 0),
                    "name": str(r.get("name") or ""),
                    "default_code": str(r.get("default_code") or ""),
                    "qty_available": qty,
                    "list_price": float(r.get("list_price") or 0.0),
                    "url": link_record(
                        app.odoo_url,
                        "product.product",
                        int(r.get("id") or 0),
                        "Producto en Odoo",
                    )["url"],
                }
            )
    items.sort(key=lambda x: (x["qty_available"], x["name"].lower()))
    severity = SEVERITY_WARN if items else SEVERITY_OK
    if any(it["qty_available"] <= 0 for it in items):
        severity = SEVERITY_ERROR
    return {
        "id": "low_stock",
        "title": "Productos bajo stock",
        "severity": severity,
        "count": len(items),
        "items": items[:50],
        "meta": {"threshold": threshold, "total_below": len(items)},
        "summary": (
            f"{len(items)} productos por debajo de {int(threshold)} unidades."
            if items
            else "Inventario por encima del umbral configurado."
        ),
    }


def check_overdue_invoices(app: AppSettings, *, client: OdooXmlRpc | None = None) -> dict[str, Any]:
    odoo = client or OdooXmlRpc(app)
    today_iso = (date.today() - timedelta(days=int(app.alert_overdue_days))).isoformat()
    rows = _safe_search_read(
        odoo,
        "account.move",
        [
            ["move_type", "=", "out_invoice"],
            ["state", "=", "posted"],
            ["payment_state", "in", ["not_paid", "partial"]],
            ["invoice_date_due", "<", today_iso],
        ],
        ["id", "name", "partner_id", "amount_total", "amount_residual", "invoice_date_due"],
        limit=120,
        order="invoice_date_due asc, id desc",
    )
    items: list[dict[str, Any]] = []
    today = date.today()
    total_residual = 0.0
    for r in rows:
        partner = r.get("partner_id")
        partner_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else ""
        due_raw = str(r.get("invoice_date_due") or "")[:10]
        try:
            due_date = date.fromisoformat(due_raw) if due_raw else None
        except ValueError:
            due_date = None
        days_overdue = (today - due_date).days if due_date else 0
        residual = float(r.get("amount_residual") or 0.0)
        total_residual += residual
        items.append(
            {
                "id": int(r.get("id") or 0),
                "name": str(r.get("name") or ""),
                "partner": partner_name,
                "amount_total": float(r.get("amount_total") or 0.0),
                "amount_residual": residual,
                "invoice_date_due": due_raw,
                "days_overdue": days_overdue,
                "url": link_record(
                    app.odoo_url,
                    "account.move",
                    int(r.get("id") or 0),
                    "Factura en Odoo",
                )["url"],
            }
        )
    if not items:
        severity = SEVERITY_OK
    elif any(it["days_overdue"] >= 30 for it in items):
        severity = SEVERITY_ERROR
    else:
        severity = SEVERITY_WARN
    return {
        "id": "overdue_invoices",
        "title": "Facturas vencidas",
        "severity": severity,
        "count": len(items),
        "items": items[:50],
        "meta": {"total_residual": round(total_residual, 2)},
        "summary": (
            f"{len(items)} facturas vencidas por un total de {total_residual:,.0f}."
            if items
            else "Sin facturas vencidas pendientes de cobro."
        ),
    }


def check_stale_drafts(app: AppSettings, *, client: OdooXmlRpc | None = None) -> dict[str, Any]:
    odoo = client or OdooXmlRpc(app)
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    rows = _safe_search_read(
        odoo,
        "account.move",
        [
            ["move_type", "in", ["out_invoice", "in_invoice"]],
            ["state", "=", "draft"],
            ["create_date", "<", cutoff],
        ],
        ["id", "name", "partner_id", "amount_total", "create_date", "move_type"],
        limit=80,
        order="create_date asc",
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        partner = r.get("partner_id")
        partner_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else ""
        items.append(
            {
                "id": int(r.get("id") or 0),
                "name": str(r.get("name") or "(borrador)"),
                "partner": partner_name,
                "amount_total": float(r.get("amount_total") or 0.0),
                "create_date": str(r.get("create_date") or "")[:10],
                "move_type": str(r.get("move_type") or ""),
                "url": link_record(
                    app.odoo_url,
                    "account.move",
                    int(r.get("id") or 0),
                    "Factura en Odoo",
                )["url"],
            }
        )
    severity = SEVERITY_WARN if items else SEVERITY_OK
    return {
        "id": "stale_drafts",
        "title": "Facturas en borrador antiguas",
        "severity": severity,
        "count": len(items),
        "items": items[:50],
        "meta": {"older_than_days": 7},
        "summary": (
            f"{len(items)} facturas llevan más de 7 días en borrador."
            if items
            else "Sin facturas en borrador antiguas."
        ),
    }


_ALERT_FUNCS = {
    "low_stock": check_low_stock,
    "overdue_invoices": check_overdue_invoices,
    "stale_drafts": check_stale_drafts,
}


def list_alert_ids() -> list[str]:
    return list(_ALERT_FUNCS.keys())


def run_all_alerts(
    app: AppSettings,
    *,
    use_cache: bool = True,
    only: list[str] | None = None,
) -> dict[str, Any]:
    """Ejecuta los chequeos (cacheado) y devuelve el agregado para frontend/CLI."""
    selected = [a for a in (only or list_alert_ids()) if a in _ALERT_FUNCS]
    if not selected:
        selected = list_alert_ids()
    cache = get_cache(app)
    cache_key = f"alerts:{make_key(selected)}"
    if use_cache:
        hit = cache.get(cache_key)
        if hit is not None:
            return hit
    client = OdooXmlRpc(app)
    alerts: list[dict[str, Any]] = []
    with time_block("alerts.run", count=len(selected)):
        for aid in selected:
            fn = _ALERT_FUNCS[aid]
            try:
                alerts.append(fn(app, client=client))
            except Exception as ex:  # noqa: BLE001
                log_event("alerts.error", alert=aid, error=str(ex))
                alerts.append(
                    {
                        "id": aid,
                        "title": aid,
                        "severity": SEVERITY_ERROR,
                        "count": 0,
                        "items": [],
                        "meta": {"error": str(ex)},
                        "summary": f"Error al ejecutar alerta {aid}: {ex}",
                    }
                )
    severity = SEVERITY_OK
    for a in alerts:
        if a["severity"] == SEVERITY_ERROR:
            severity = SEVERITY_ERROR
            break
        if a["severity"] == SEVERITY_WARN and severity == SEVERITY_OK:
            severity = SEVERITY_WARN
    payload = {
        "ok": True,
        "severity": severity,
        "count": sum(a["count"] for a in alerts),
        "alerts": alerts,
    }
    cache.set(cache_key, payload, ttl=int(app.alert_cache_ttl))
    return payload
