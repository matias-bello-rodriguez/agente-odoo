"""Sugerencias de autocompletar (modo copiloto) para el frontend.

Tres tipos:
- partner   → clientes (`customer_rank > 0`).
- vendor    → proveedores (`supplier_rank > 0`).
- product   → productos activos.

Resultados cacheados por consulta (`prefix`) durante `cache_default_ttl`
para que tipear no bombardee a Odoo.
"""

from __future__ import annotations

import xmlrpc.client
from typing import Any

from odoo_rag.cache import get_cache, make_key
from odoo_rag.config import Settings as AppSettings
from odoo_rag.observability import time_block
from odoo_rag.odoo_client import OdooXmlRpc


SUGGEST_KINDS = ("partner", "vendor", "product")
_DEFAULT_LIMIT = 8
_MAX_LIMIT = 20


def _normalize_query(query: str) -> str:
    return (query or "").strip()


def suggest(
    app: AppSettings,
    *,
    kind: str,
    query: str,
    limit: int = _DEFAULT_LIMIT,
) -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind not in SUGGEST_KINDS:
        raise ValueError(f"kind inválido (use {', '.join(SUGGEST_KINDS)}).")
    q = _normalize_query(query)
    if len(q) < 2:
        return {"ok": True, "kind": kind, "query": q, "items": []}
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = _DEFAULT_LIMIT
    n = max(1, min(_MAX_LIMIT, n))

    cache = get_cache(app)
    cache_key = f"suggest:{kind}:{make_key(q.lower(), n)}"
    hit = cache.get(cache_key)
    if hit is not None:
        return hit

    client = OdooXmlRpc(app)
    items: list[dict[str, Any]] = []
    with time_block("suggest", kind=kind, query=q, limit=n):
        if kind == "partner":
            items = _suggest_partner(client, q, n, customer=True)
        elif kind == "vendor":
            items = _suggest_partner(client, q, n, customer=False)
        elif kind == "product":
            items = _suggest_product(client, q, n)

    payload = {"ok": True, "kind": kind, "query": q, "items": items}
    cache.set(cache_key, payload, ttl=int(app.cache_default_ttl))
    return payload


def _suggest_partner(
    client: OdooXmlRpc, query: str, limit: int, *, customer: bool
) -> list[dict[str, Any]]:
    rank_field = "customer_rank" if customer else "supplier_rank"
    domain = [
        [rank_field, ">", 0],
        ["active", "=", True],
        "|",
        ["name", "ilike", query],
        ["email", "ilike", query],
    ]
    try:
        rows = client.execute_kw(
            "res.partner",
            "search_read",
            [domain],
            {
                "fields": ["id", "name", "email", "phone", "city"],
                "limit": limit,
                "order": "name asc",
            },
        )
    except xmlrpc.client.Fault:
        rows = []
    return [
        {
            "id": int(r.get("id") or 0),
            "label": str(r.get("name") or ""),
            "subtitle": " · ".join(
                x for x in (str(r.get("email") or ""), str(r.get("city") or "")) if x
            ),
            "phone": str(r.get("phone") or ""),
        }
        for r in rows
    ]


def _suggest_product(
    client: OdooXmlRpc, query: str, limit: int
) -> list[dict[str, Any]]:
    domain = [
        ["active", "=", True],
        "|",
        ["name", "ilike", query],
        ["default_code", "ilike", query],
    ]
    try:
        rows = client.execute_kw(
            "product.product",
            "search_read",
            [domain],
            {
                "fields": ["id", "name", "default_code", "list_price", "qty_available"],
                "limit": limit,
                "order": "name asc",
            },
        )
    except xmlrpc.client.Fault:
        rows = []
    return [
        {
            "id": int(r.get("id") or 0),
            "label": str(r.get("name") or ""),
            "subtitle": " · ".join(
                x
                for x in (
                    f"#{r.get('default_code')}" if r.get("default_code") else "",
                    f"${float(r.get('list_price') or 0.0):,.0f}" if r.get("list_price") is not None else "",
                    f"stock {float(r.get('qty_available') or 0.0):.0f}" if r.get("qty_available") is not None else "",
                )
                if x
            ),
        }
        for r in rows
    ]
