"""Reportes ejecutivos + resúmenes automáticos con LLM.

- `summarize_data(app, data, intent)` → toma cualquier resultado de query y
  pide al LLM un análisis ejecutivo (3–6 puntos, en español).
- `monthly_sales_report(app, year, month)` → arma un reporte completo de
  ventas y lo redacta. Útil para `“hazme un reporte de ventas del mes”`.

Todo respeta `OPENAI_API_KEY`; si no está configurada se devuelve un
fallback no-LLM (sólo datos crudos).
"""

from __future__ import annotations

import json
import xmlrpc.client
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

from openai import OpenAI

from odoo_rag.cache import get_cache, make_key
from odoo_rag.config import Settings as AppSettings
from odoo_rag.observability import log_event, time_block
from odoo_rag.odoo_client import OdooXmlRpc


_SUMMARY_SYSTEM = (
    "Eres un analista de negocio. Recibes datos en JSON provenientes de Odoo y "
    "devuelves un análisis breve, accionable, en español neutro. "
    "Estructura: 1) titular en una frase, 2) 3 a 5 bullets con cifras, "
    "3) una sección 'Acciones sugeridas' con 1 a 3 ítems. "
    "No inventes datos: si faltan, dilo. No uses markdown excesivo (sin tablas)."
)


def _llm_client(app: AppSettings) -> OpenAI:
    if not app.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY no configurada.")
    return OpenAI(api_key=app.openai_api_key)


def _truncate_for_llm(payload: Any, *, max_items: int = 30) -> Any:
    """Recorta listas para no agotar tokens (mantiene primeros N + meta)."""
    if isinstance(payload, dict):
        out = {k: _truncate_for_llm(v, max_items=max_items) for k, v in payload.items()}
        return out
    if isinstance(payload, list):
        if len(payload) > max_items:
            return payload[:max_items] + [{"_truncated": len(payload) - max_items}]
        return [_truncate_for_llm(it, max_items=max_items) for it in payload]
    return payload


def summarize_data(
    app: AppSettings,
    data: dict[str, Any] | list[Any],
    *,
    intent: str = "Resumen",
    max_items: int = 30,
) -> dict[str, Any]:
    """Genera un análisis breve a partir de un resultado tabular.

    Devuelve `{ok, summary, intent, used_llm, items_count}`.
    """
    items_count = 0
    if isinstance(data, dict):
        items_count = int(data.get("count") or len(data.get("items") or []))
    elif isinstance(data, list):
        items_count = len(data)

    if not app.openai_api_key:
        return {
            "ok": True,
            "used_llm": False,
            "intent": intent,
            "items_count": items_count,
            "summary": (
                f"{items_count} resultados para «{intent}». "
                "Configura OPENAI_API_KEY para obtener un análisis automático."
            ),
        }

    payload = _truncate_for_llm(data, max_items=max_items)
    user_msg = (
        f"Pregunta o intención del usuario: {intent}\n\n"
        f"Datos en JSON:\n{json.dumps(payload, ensure_ascii=False, default=str)[:14000]}"
    )

    cache = get_cache(app)
    cache_key = f"summary:{make_key(intent, payload)}"
    hit = cache.get(cache_key)
    if hit is not None:
        return hit

    with time_block("summary.llm", intent=intent, items=items_count):
        client = _llm_client(app)
        try:
            completion = client.chat.completions.create(
                model=app.openai_llm_model,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": _SUMMARY_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = (completion.choices[0].message.content or "").strip()
        except Exception as ex:  # noqa: BLE001
            log_event("summary.error", intent=intent, error=str(ex))
            return {
                "ok": False,
                "used_llm": False,
                "intent": intent,
                "items_count": items_count,
                "summary": f"No pude generar el análisis automático: {ex}",
            }

    out = {
        "ok": True,
        "used_llm": True,
        "intent": intent,
        "items_count": items_count,
        "summary": text or "Sin análisis disponible.",
    }
    cache.set(cache_key, out, ttl=app.cache_default_ttl)
    return out


# ---------------------------------------------------------------------------
# Reporte mensual de ventas
# ---------------------------------------------------------------------------


def _safe_read_group(client: OdooXmlRpc, model: str, domain: list, fields: list[str], groupby: list[str]) -> list:
    try:
        return client.execute_kw(model, "read_group", [domain, fields, groupby], {"lazy": False})
    except (xmlrpc.client.Fault, ValueError, TypeError):
        return []


def _safe_search_count(client: OdooXmlRpc, model: str, domain: list) -> int:
    try:
        return int(client.execute_kw(model, "search_count", [domain]))
    except (xmlrpc.client.Fault, ValueError, TypeError):
        return 0


def monthly_sales_report(
    app: AppSettings,
    *,
    year: int | None = None,
    month: int | None = None,
    write_summary: bool = True,
) -> dict[str, Any]:
    """Reporte de ventas del mes indicado (por defecto el actual)."""
    today = date.today()
    y = int(year or today.year)
    m = int(month or today.month)
    if not (1 <= m <= 12):
        raise ValueError("Mes inválido (1-12).")
    last_day = monthrange(y, m)[1]
    period_start = date(y, m, 1)
    period_end = date(y, m, last_day)

    prev_month = m - 1 or 12
    prev_year = y if m > 1 else y - 1
    prev_last = monthrange(prev_year, prev_month)[1]
    prev_start = date(prev_year, prev_month, 1)
    prev_end = date(prev_year, prev_month, prev_last)

    client = OdooXmlRpc(app)

    # Totales
    sales_total_rows = _safe_read_group(
        client,
        "sale.order",
        [["state", "in", ["sale", "done"]], ["date_order", ">=", period_start.isoformat()], ["date_order", "<=", period_end.isoformat()]],
        ["amount_total:sum"],
        [],
    )
    sales_total = float(sales_total_rows[0].get("amount_total") if sales_total_rows else 0.0) if sales_total_rows else 0.0

    prev_total_rows = _safe_read_group(
        client,
        "sale.order",
        [["state", "in", ["sale", "done"]], ["date_order", ">=", prev_start.isoformat()], ["date_order", "<=", prev_end.isoformat()]],
        ["amount_total:sum"],
        [],
    )
    prev_total = float(prev_total_rows[0].get("amount_total") if prev_total_rows else 0.0) if prev_total_rows else 0.0
    growth_pct = ((sales_total - prev_total) / prev_total * 100.0) if prev_total else None

    invoiced_rows = _safe_read_group(
        client,
        "account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"], ["invoice_date", ">=", period_start.isoformat()], ["invoice_date", "<=", period_end.isoformat()]],
        ["amount_total:sum"],
        [],
    )
    invoiced_total = float(invoiced_rows[0].get("amount_total") if invoiced_rows else 0.0) if invoiced_rows else 0.0

    confirmed_count = _safe_search_count(
        client,
        "sale.order",
        [["state", "in", ["sale", "done"]], ["date_order", ">=", period_start.isoformat()], ["date_order", "<=", period_end.isoformat()]],
    )

    # Top clientes (por monto facturado en el mes)
    top_partner_rows = _safe_read_group(
        client,
        "account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"], ["invoice_date", ">=", period_start.isoformat()], ["invoice_date", "<=", period_end.isoformat()]],
        ["partner_id", "amount_total:sum"],
        ["partner_id"],
    )
    top_customers: list[dict[str, Any]] = []
    for r in (top_partner_rows or [])[:10]:
        partner = r.get("partner_id")
        name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "(sin partner)"
        top_customers.append({"name": name, "amount": float(r.get("amount_total") or 0.0)})
    top_customers.sort(key=lambda x: -x["amount"])

    # Top productos por cantidad vendida (sale.order.line)
    top_products: list[dict[str, Any]] = []
    try:
        line_rows = client.execute_kw(
            "sale.order.line",
            "read_group",
            [
                [
                    ["state", "in", ["sale", "done"]],
                    ["order_id.date_order", ">=", period_start.isoformat()],
                    ["order_id.date_order", "<=", period_end.isoformat()],
                ],
                ["product_id", "product_uom_qty:sum", "price_subtotal:sum"],
                ["product_id"],
            ],
            {"lazy": False},
        )
        for r in (line_rows or [])[:10]:
            product = r.get("product_id")
            name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else "(sin producto)"
            top_products.append(
                {
                    "name": name,
                    "qty": float(r.get("product_uom_qty") or 0.0),
                    "amount": float(r.get("price_subtotal") or 0.0),
                }
            )
        top_products.sort(key=lambda x: -x["amount"])
    except (xmlrpc.client.Fault, ValueError, TypeError):
        top_products = []

    payload: dict[str, Any] = {
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "label": f"{y:04d}-{m:02d}",
        },
        "totals": {
            "sales_amount": round(sales_total, 2),
            "previous_month_sales": round(prev_total, 2),
            "growth_pct": round(growth_pct, 2) if growth_pct is not None else None,
            "invoiced_amount": round(invoiced_total, 2),
            "confirmed_orders": confirmed_count,
        },
        "top_customers": top_customers,
        "top_products": top_products,
    }

    summary_block: dict[str, Any] = {"used_llm": False, "summary": ""}
    if write_summary:
        intent = f"Reporte de ventas {payload['period']['label']}"
        summary_block = summarize_data(app, payload, intent=intent, max_items=20)

    return {
        "ok": True,
        "report": "monthly_sales",
        "data": payload,
        "summary": summary_block.get("summary", ""),
        "used_llm": bool(summary_block.get("used_llm")),
    }
