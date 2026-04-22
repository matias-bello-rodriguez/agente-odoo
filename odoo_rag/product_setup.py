"""Alta avanzada de producto (Odoo 19): categoría, plantilla, proveedores, stock, FIFO/tiempo real."""

from __future__ import annotations

import json
import re
from typing import Any

import xmlrpc.client

from openai import OpenAI

from odoo_rag.config import Settings as AppSettings
from odoo_rag.odoo_client import OdooXmlRpc

PRODUCT_SETUP_SYSTEM = """Extrae un plan JSON para crear y configurar un producto en Odoo 19 (Inventario, Compra, Contabilidad).

Responde SOLO un objeto JSON con esta forma:
{
  "reply": "texto breve en español: confirmás que el plan está listo para revisión en el modal",
  "draft_action": {
    "operation": "product_setup",
    "summary": "título corto para el modal",
    "plan": {
      "product_name": "nombre exacto del producto",
      "internal_reference": "código interno / SKU",
      "category_name": "nombre de categoría comercial (ej. equipos electrónicos)",
      "list_price": 120.0,
      "standard_price": 75.0,
      "currency_code": "USD",
      "sale_ok": true,
      "purchase_ok": true,
      "tracking": "lot",
      "weight_kg": 1.5,
      "category_fifo_realtime": true,
      "suppliers": [
        {"name": "Proveedor A", "price": 70.0, "min_qty": 10.0, "lead_days": 5},
        {"name": "Proveedor B", "price": 65.0, "min_qty": 50.0, "lead_days": 10}
      ],
      "reorder_min": 20.0,
      "reorder_max": 100.0,
      "auto_replenishment": true,
      "note_accounts": "texto libre si el usuario pidió cuentas contables (la app usará las de la categoría si existen)"
    }
  }
}

Reglas:
- Usa números JSON válidos (sin comillas en precios).
- tracking solo: "none", "lot", "serial"; para trazabilidad por lote usa "lot".
- suppliers.name deben ser los nombres que dio el usuario (Proveedor A/B si los nombró así).
- reorder_min / reorder_max son cantidades mínima y máxima de inventario para la regla de reorden.
- Si falta algún dato imprescindible (nombre del producto), draft_action debe ser null y reply debe pedirlo.

No incluyas markdown ni texto fuera del JSON.

La clave "reply" es SIEMPRE texto conversacional en español para la persona usuaria.
PROHIBIDO poner en "reply" JSON, llaves con datos del plan, listas de proveedores ni el mismo contenido que "plan".
"""


DEFAULT_PRODUCT_SETUP_REPLY = (
    "Preparé la configuración del producto para Odoo (precios, categoría, proveedores y reglas de stock). "
    "Revisá la ventana de confirmación y pulsá Confirmar cuando los datos estén bien."
)

_MSG_PRODUCT_SETUP_INCOMPLETE = (
    "No alcanzó para armar el plan completo. Indicá el nombre del producto y los datos que falten."
)


def looks_like_leaked_structure_json(text: str) -> bool:
    """True si el texto parece JSON técnico (plan/API) que no debe mostrarse en el chat."""
    s = text.strip()
    if len(s) < 2 or not s.startswith("{"):
        return False
    head = s[:1600]
    if '"draft_action"' in head:
        return True
    if '"product_name"' in head and '"plan"' in head:
        return True
    if '"product_name"' in head and '"internal_reference"' in head:
        return True
    if '"suppliers"' in head and '"reorder_min"' in head:
        return True
    return False


def finalize_product_setup_reply(reply: str, *, valid_plan: bool) -> str:
    r = (reply or "").strip()
    if looks_like_leaked_structure_json(r):
        return DEFAULT_PRODUCT_SETUP_REPLY if valid_plan else _MSG_PRODUCT_SETUP_INCOMPLETE
    if valid_plan and not r:
        return DEFAULT_PRODUCT_SETUP_REPLY
    if not valid_plan and not r:
        return _MSG_PRODUCT_SETUP_INCOMPLETE
    return r


def looks_like_full_product_setup(text: str) -> bool:
    t = text.lower()
    if len(text.strip()) < 180:
        return False
    keywords = (
        "proveedor",
        "fifo",
        "reabaste",
        "orden",
        "compra",
        "valoración",
        "trazabilidad",
        "lote",
        "stock mínimo",
        "contabilidad",
        "cuenta",
        "almacenable",
    )
    hits = sum(1 for k in keywords if k in t)
    return hits >= 3


def extract_product_setup_draft(app: AppSettings, user_message: str) -> dict[str, Any]:
    if not app.openai_api_key:
        raise RuntimeError("Falta OPENAI_API_KEY en .env.")
    client = OpenAI(api_key=app.openai_api_key)
    completion = client.chat.completions.create(
        model=app.openai_llm_model,
        response_format={"type": "json_object"},
        temperature=0.1,
        messages=[
            {"role": "system", "content": PRODUCT_SETUP_SYSTEM},
            {"role": "user", "content": user_message.strip()},
        ],
    )
    raw = completion.choices[0].message.content or "{}"
    data = json.loads(raw)
    reply = str(data.get("reply") or "").strip()
    draft = data.get("draft_action")
    if not isinstance(draft, dict) or draft.get("operation") != "product_setup":
        return {
            "reply": finalize_product_setup_reply(reply, valid_plan=False),
            "draft_action": None,
        }
    plan = draft.get("plan")
    if not isinstance(plan, dict) or not plan.get("product_name"):
        return {
            "reply": finalize_product_setup_reply(reply, valid_plan=False),
            "draft_action": None,
        }
    summary = str(draft.get("summary") or plan.get("product_name"))[:240]
    return {
        "reply": finalize_product_setup_reply(reply, valid_plan=True),
        "draft_action": {"operation": "product_setup", "plan": plan, "summary": summary},
    }


def _format_odoo_fault(exc: xmlrpc.client.Fault) -> str:
    fs = getattr(exc, "faultString", "") or ""
    for pattern in (
        r"UserError:\s*(.+?)(?:\n\n|\Z)",
        r"ValidationError:\s*(.+?)(?:\n\n|\Z)",
    ):
        m = re.search(pattern, fs, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip().split("\n")[0][:600]
    return str(exc)[:600]


def _currency_id(client: OdooXmlRpc, code: str) -> int | None:
    code = (code or "USD").upper().strip()
    ids = client.execute_kw("res.currency", "search", [[["name", "=", code]]], {"limit": 1})
    if ids:
        return int(ids[0])
    ids = client.execute_kw("res.currency", "search", [[["symbol", "=", "$"]]], {"limit": 3})
    return int(ids[0]) if ids else None


def _root_category_id(client: OdooXmlRpc) -> int:
    roots = client.execute_kw(
        "product.category",
        "search",
        [[["parent_id", "=", False]]],
        {"limit": 1},
    )
    if roots:
        return int(roots[0])
    return int(client.execute_kw("product.category", "create", [{"name": "All", "parent_id": False}]))


def _ensure_category(client: OdooXmlRpc, name: str, fifo_rt: bool) -> tuple[int, list[str]]:
    log: list[str] = []
    found = client.execute_kw(
        "product.category",
        "search",
        [[["name", "ilike", name.strip()]]],
        {"limit": 1},
    )
    if found:
        cid = int(found[0])
        log.append(f"Categoría encontrada id={cid}")
    else:
        cid = int(
            client.execute_kw(
                "product.category",
                "create",
                [{"name": name.strip(), "parent_id": _root_category_id(client)}],
            )
        )
        log.append(f"Categoría creada id={cid}")

    if fifo_rt:
        try:
            client.execute_kw(
                "product.category",
                "write",
                [
                    [cid],
                    {
                        "property_cost_method": "fifo",
                        "property_valuation": "real_time",
                    },
                ],
            )
            log.append("Categoría: FIFO + valoración en tiempo real.")
        except xmlrpc.client.Fault as e:
            log.append(f"Aviso categoría FIFO/real_time: {_format_odoo_fault(e)}")
    log.append(
        "Ingresos/costos contables suelen heredarse del plan contable/categoría; revisá "
        "la categoría del producto si necesitás cuentas específicas."
    )
    return cid, log


def _ensure_partner_supplier(client: OdooXmlRpc, name: str) -> int:
    found = client.execute_kw(
        "res.partner",
        "search",
        [[["name", "=", name.strip()]]],
        {"limit": 1},
    )
    if found:
        return int(found[0])
    return int(
        client.execute_kw(
            "res.partner",
            "create",
            [{"name": name.strip(), "supplier_rank": 1}],
        )
    )


def _buy_route_ids(client: OdooXmlRpc) -> list[int]:
    """Rutas que permiten compras (nombre depende del idioma)."""
    rids = client.execute_kw(
        "stock.route",
        "search",
        [[["rule_ids.action", "=", "buy"]]],
        {"limit": 5},
    )
    if rids:
        return [int(x) for x in rids]
    rids = client.execute_kw(
        "stock.route",
        "search",
        [[["name", "ilike", "compr"]]],
        {"limit": 5},
    )
    if rids:
        return [int(x) for x in rids]
    rids = client.execute_kw(
        "stock.route",
        "search",
        [[["name", "ilike", "buy"]]],
        {"limit": 5},
    )
    return [int(x) for x in rids] if rids else []


def run_product_setup(app: AppSettings, plan: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta el plan; devuelve ids y bitácora (errores parciales en log)."""
    client = OdooXmlRpc(app)
    log: list[str] = []
    product_name = str(plan.get("product_name") or "").strip()
    if not product_name:
        raise ValueError("El plan no incluye product_name.")

    dup = client.execute_kw(
        "product.template",
        "search",
        [[["name", "=", product_name]]],
        {"limit": 1},
    )
    if dup:
        raise ValueError(
            f'Ya existe una plantilla de producto con el nombre «{product_name}». '
            "Cambiá el nombre o editá ese producto en Odoo."
        )

    curr_id = _currency_id(client, str(plan.get("currency_code") or "USD"))
    if curr_id:
        log.append(f"Moneda id={curr_id}")

    cat_name = str(plan.get("category_name") or "General").strip()
    fifo_rt = bool(plan.get("category_fifo_realtime", True))
    categ_id, clog = _ensure_category(client, cat_name, fifo_rt)
    log.extend(clog)

    tracking = str(plan.get("tracking") or "lot").lower()
    if tracking not in ("none", "lot", "serial"):
        tracking = "lot"

    tmpl_vals: dict[str, Any] = {
        "name": product_name,
        "default_code": str(plan.get("internal_reference") or "").strip() or False,
        "categ_id": categ_id,
        "type": "consu",
        "sale_ok": bool(plan.get("sale_ok", True)),
        "purchase_ok": bool(plan.get("purchase_ok", True)),
        "list_price": float(plan.get("list_price") or 0),
        "standard_price": float(plan.get("standard_price") or 0),
        "tracking": tracking,
        "is_storable": True,
    }
    w = plan.get("weight_kg")
    if w is not None:
        tmpl_vals["weight"] = float(w)

    try:
        tmpl_id = int(client.execute_kw("product.template", "create", [tmpl_vals]))
        log.append(f"product.template id={tmpl_id}")
    except xmlrpc.client.Fault as e:
        raise ValueError(_format_odoo_fault(e)) from e

    routes = _buy_route_ids(client)
    if routes:
        try:
            client.execute_kw(
                "product.template",
                "write",
                [[tmpl_id], {"route_ids": [(6, 0, routes)]}],
            )
            log.append(f"Rutas compra enlazadas: {routes}")
        except xmlrpc.client.Fault as e:
            log.append(f"Aviso rutas: {_format_odoo_fault(e)}")

    prods = client.execute_kw(
        "product.product",
        "search_read",
        [[["product_tmpl_id", "=", tmpl_id]]],
        {"fields": ["id"], "limit": 1},
    )
    if not prods:
        raise RuntimeError("No se encontró variante tras crear la plantilla.")
    variant_id = int(prods[0]["id"])

    suppliers = plan.get("suppliers") or []
    if isinstance(suppliers, list):
        for row in suppliers:
            if not isinstance(row, dict):
                continue
            pname = str(row.get("name") or "").strip()
            if not pname:
                continue
            pid = _ensure_partner_supplier(client, pname)
            svals: dict[str, Any] = {
                "partner_id": pid,
                "product_tmpl_id": tmpl_id,
                "price": float(row.get("price") or 0),
                "min_qty": float(row.get("min_qty") or 1),
                "delay": int(row.get("lead_days") or 1),
            }
            if curr_id:
                svals["currency_id"] = curr_id
            try:
                sid = client.execute_kw("product.supplierinfo", "create", [svals])
                log.append(f"Proveedor {pname} supplierinfo id={sid}")
            except xmlrpc.client.Fault as e:
                log.append(f"Proveedor {pname}: {_format_odoo_fault(e)}")

    # Dominio «todos los almacenes»: [(1,'=',1)] evita ambigüedades XML-RPC con [] / [[]]
    wh = client.execute_kw(
        "stock.warehouse",
        "search_read",
        [[(1, "=", 1)]],
        {"fields": ["id"], "limit": 1},
    )
    orderpoint_id: int | None = None
    if wh:
        wid = int(wh[0]["id"])
        mn = float(plan.get("reorder_min") or 0)
        mx = float(plan.get("reorder_max") or 0)
        if mn > mx:
            mn, mx = mx, mn
        op_vals: dict[str, Any] = {
            "product_id": variant_id,
            "warehouse_id": wid,
            "product_min_qty": mn,
            "product_max_qty": mx,
            "trigger": "auto",
        }
        try:
            oid = client.execute_kw("stock.warehouse.orderpoint", "create", [op_vals])
            orderpoint_id = int(oid)
            log.append(f"Regla de reorden id={oid} (min/max, disparo automático)")
        except xmlrpc.client.Fault as e:
            log.append(f"Aviso regla stock: {_format_odoo_fault(e)}")

    return {
        "product_tmpl_id": tmpl_id,
        "product_product_id": variant_id,
        "orderpoint_id": orderpoint_id,
        "log": log,
    }
