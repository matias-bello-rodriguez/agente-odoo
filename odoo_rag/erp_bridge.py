"""Operaciones genéricas pero acotadas contra Odoo (lectura, escritura, archivo, baja)."""

from __future__ import annotations

import xmlrpc.client
from typing import Any

from odoo_rag.config import Settings as AppSettings
from odoo_rag.odoo_client import OdooXmlRpc
from odoo_rag.odoo_urls import link_record

_ALLOWED_DOMAIN_OPS = frozenset({"=", "!=", ">", "<", ">=", "<=", "ilike", "like", "in", "not in"})

# Campos devueltos y permitidos en dominio (solo condiciones AND planas; sin | & !).
ERP_READ_CONFIG: dict[str, dict[str, Any]] = {
    "res.partner": {
        "read_fields": frozenset(
            {
                "id",
                "name",
                "email",
                "phone",
                "street",
                "city",
                "zip",
                "vat",
                "is_company",
                "active",
                "customer_rank",
                "supplier_rank",
            }
        ),
        "domain_fields": frozenset(
            {
                "id",
                "name",
                "email",
                "phone",
                "city",
                "zip",
                "vat",
                "is_company",
                "active",
                "customer_rank",
                "supplier_rank",
            }
        ),
        "max_limit": 100,
    },
    "product.product": {
        "read_fields": frozenset(
            {
                "id",
                "name",
                "default_code",
                "list_price",
                "standard_price",
                "type",
                "active",
                "barcode",
            }
        ),
        "domain_fields": frozenset(
            {"id", "name", "default_code", "type", "active", "list_price", "standard_price", "barcode"}
        ),
        "max_limit": 100,
    },
    "sale.order": {
        "read_fields": frozenset(
            {
                "id",
                "name",
                "partner_id",
                "date_order",
                "state",
                "amount_total",
                "invoice_status",
            }
        ),
        "domain_fields": frozenset({"id", "name", "state", "partner_id", "invoice_status", "amount_total"}),
        "max_limit": 80,
    },
    "purchase.order": {
        "read_fields": frozenset(
            {"id", "name", "partner_id", "date_order", "state", "amount_total"}
        ),
        "domain_fields": frozenset({"id", "name", "state", "partner_id", "amount_total"}),
        "max_limit": 80,
    },
    "account.move": {
        "read_fields": frozenset(
            {
                "id",
                "name",
                "partner_id",
                "move_type",
                "state",
                "amount_total",
                "amount_residual",
                "payment_state",
                "invoice_date",
                "invoice_date_due",
            }
        ),
        "domain_fields": frozenset(
            {
                "id",
                "name",
                "move_type",
                "state",
                "partner_id",
                "payment_state",
                "amount_total",
                "amount_residual",
                "invoice_date",
                "invoice_date_due",
            }
        ),
        "max_limit": 80,
    },
    "stock.picking": {
        "read_fields": frozenset(
            {"id", "name", "partner_id", "origin", "state", "scheduled_date", "picking_type_id"}
        ),
        "domain_fields": frozenset({"id", "name", "state", "partner_id", "origin"}),
        "max_limit": 80,
    },
}

# Campos modificables (no incluye relaciones M2O por nombre; el LLM debe pasar id si aplica).
ERP_WRITE_FIELDS: dict[str, frozenset[str]] = {
    "res.partner": frozenset(
        {"name", "email", "phone", "street", "city", "zip", "vat", "comment", "is_company", "active"}
    ),
    "product.product": frozenset(
        {"name", "default_code", "list_price", "standard_price", "type", "active", "barcode"}
    ),
    "sale.order": frozenset({"note", "client_order_ref"}),
    "purchase.order": frozenset({"notes", "partner_ref"}),
    "account.move": frozenset({"ref", "narration"}),
}

ERP_UNLINK_MODELS = frozenset({"product.product"})
ERP_ARCHIVE_MODELS = frozenset({"res.partner", "product.product"})

_MAX_DOMAIN_CLAUSES = 12
_MAX_IN_LEN = 24
_STR_MAX = 240


def _format_odoo_fault(ex: xmlrpc.client.Fault) -> str:
    return str(getattr(ex, "faultString", None) or ex)


def _sanitize_domain_value(op: str, val: Any) -> Any:
    if op in ("in", "not in"):
        if not isinstance(val, (list, tuple)):
            return []
        out: list[Any] = []
        for x in val[:_MAX_IN_LEN]:
            if isinstance(x, bool):
                out.append(x)
            elif isinstance(x, int):
                out.append(int(x))
            elif isinstance(x, float):
                out.append(float(x))
            elif isinstance(x, str):
                s = x.strip()[:_STR_MAX]
                if s:
                    out.append(s)
        return out
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return int(val)
    if isinstance(val, float):
        return float(val)
    if val is None:
        return False
    return str(val).strip()[:_STR_MAX]


def sanitize_erp_domain(model: str, domain: Any) -> list:
    cfg = ERP_READ_CONFIG.get(model)
    if not cfg:
        raise ValueError(f"Modelo no permitido para lectura ERP: {model}")
    allowed_dom = cfg["domain_fields"]
    if not isinstance(domain, list):
        return []
    out: list[Any] = []
    for clause in domain:
        if not isinstance(clause, (list, tuple)) or len(clause) != 3:
            continue
        field, op, val = clause[0], clause[1], clause[2]
        if str(field) not in allowed_dom or str(op) not in _ALLOWED_DOMAIN_OPS:
            continue
        out.append([str(field), str(op), _sanitize_domain_value(str(op), val)])
        if len(out) >= _MAX_DOMAIN_CLAUSES:
            break
    return out


def sanitize_erp_read_draft(model: str, domain: Any, fields: Any, limit: Any) -> dict[str, Any]:
    model = str(model or "").strip()
    cfg = ERP_READ_CONFIG.get(model)
    if not cfg:
        raise ValueError(f"Modelo no permitido para consulta: {model}")
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 40
    lim = max(1, min(lim, int(cfg["max_limit"])))
    dom = sanitize_erp_domain(model, domain)
    rf = cfg["read_fields"]
    if not isinstance(fields, list) or not fields:
        field_list = sorted(rf)[:12]
    else:
        field_list = []
        for f in fields:
            fs = str(f).strip()
            if fs in rf and fs not in field_list:
                field_list.append(fs)
        if not field_list:
            field_list = sorted(list(rf))[:12]
    if "id" not in field_list:
        field_list = ["id"] + [x for x in field_list if x != "id"]
    return {"model": model, "domain": dom, "fields": field_list, "limit": lim}


def sanitize_erp_write_draft(model: str, record_id: Any, values: Any) -> dict[str, Any]:
    model = str(model or "").strip()
    allowed = ERP_WRITE_FIELDS.get(model)
    if not allowed:
        raise ValueError(f"Modelo no permitido para actualización: {model}")
    try:
        rid = int(record_id)
    except (TypeError, ValueError):
        raise ValueError("record_id inválido para escritura.") from None
    if rid <= 0:
        raise ValueError("record_id debe ser positivo.")
    if not isinstance(values, dict) or not values:
        raise ValueError("Sin campos para actualizar.")
    clean: dict[str, Any] = {}
    for k, raw in values.items():
        key = str(k).strip()
        if key not in allowed:
            continue
        if raw is None:
            continue
        if key == "is_company" or key == "active":
            if isinstance(raw, bool):
                clean[key] = raw
            else:
                clean[key] = str(raw).lower() in ("1", "true", "sí", "si", "yes")
            continue
        if key in {"list_price", "standard_price"}:
            try:
                clean[key] = float(raw)
            except (TypeError, ValueError):
                continue
            continue
        if key == "type":
            t = str(raw).lower().strip()
            if t in ("consu", "service", "combo"):
                clean[key] = t
            continue
        clean[key] = str(raw).strip()[:_STR_MAX] if isinstance(raw, str) else raw
    if not clean:
        raise ValueError("Ningún campo permitido para actualizar.")
    return {"model": model, "record_id": rid, "values": clean}


def sanitize_erp_archive_draft(model: str, record_ids: Any) -> dict[str, Any]:
    model = str(model or "").strip()
    if model not in ERP_ARCHIVE_MODELS:
        raise ValueError(f"Archivar no permitido para el modelo {model}.")
    ids = _coerce_id_list(record_ids, max_n=8)
    if not ids:
        raise ValueError("Indica uno o más record_ids para archivar.")
    return {"model": model, "record_ids": ids}


def sanitize_erp_unlink_draft(model: str, record_ids: Any) -> dict[str, Any]:
    model = str(model or "").strip()
    if model not in ERP_UNLINK_MODELS:
        raise ValueError("Borrado físico solo está permitido para product.product (máx. 2 ids).")
    ids = _coerce_id_list(record_ids, max_n=2)
    if not ids:
        raise ValueError("Indica record_ids para eliminar.")
    return {"model": model, "record_ids": ids}


def _coerce_id_list(record_ids: Any, *, max_n: int) -> list[int]:
    if isinstance(record_ids, int):
        raw = [record_ids]
    elif isinstance(record_ids, list):
        raw = record_ids
    else:
        return []
    out: list[int] = []
    for x in raw[:max_n]:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in out:
            out.append(i)
    return out


def _cell_value(val: Any) -> Any:
    if isinstance(val, (list, tuple)) and len(val) >= 2 and isinstance(val[0], int):
        name = val[1] if val[1] is not None else ""
        return f"{name} (#{val[0]})"
    if isinstance(val, bool):
        return "Sí" if val else "No"
    return val


def execute_erp_read(app: AppSettings, spec: dict[str, Any]) -> dict[str, Any]:
    client = OdooXmlRpc(app)
    model = spec["model"]
    domain = spec["domain"]
    fields = spec["fields"]
    limit = spec["limit"]
    try:
        rows = client.execute_kw(
            model,
            "search_read",
            [domain],
            {"fields": fields, "limit": limit, "order": "id desc"},
        )
    except xmlrpc.client.Fault as ex:
        raise ValueError(_format_odoo_fault(ex)) from ex
    items: list[dict[str, Any]] = []
    for r in rows or []:
        row = {f: _cell_value(r.get(f)) for f in fields}
        items.append(row)
    return {
        "query": "erp_read",
        "title": f"Consulta · {model}",
        "count": len(items),
        "items": items,
        "fields": fields,
        "meta": {"model": model, "domain": domain, "limit": limit},
        "hint": ""
        if items
        else "Ningún registro coincide con el dominio indicado (revisá filtros o ampliá el límite).",
    }


def execute_erp_write(app: AppSettings, spec: dict[str, Any]) -> dict[str, Any]:
    client = OdooXmlRpc(app)
    model = spec["model"]
    rid = spec["record_id"]
    vals = spec["values"]
    if model == "account.move":
        try:
            st = client.execute_kw(model, "read", [[rid]], {"fields": ["state"]})
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        if not st or str(st[0].get("state") or "") != "draft":
            raise ValueError("Solo se pueden editar ref/notas en facturas en borrador.")
    try:
        client.execute_kw(model, "write", [[rid], vals])
    except xmlrpc.client.Fault as ex:
        raise ValueError(_format_odoo_fault(ex)) from ex
    return {"ok": True, "model": model, "id": rid, "updated": list(vals.keys())}


def execute_erp_archive(app: AppSettings, spec: dict[str, Any]) -> dict[str, Any]:
    client = OdooXmlRpc(app)
    model = spec["model"]
    ids = spec["record_ids"]
    try:
        client.execute_kw(model, "write", [ids, {"active": False}])
    except xmlrpc.client.Fault as ex:
        raise ValueError(_format_odoo_fault(ex)) from ex
    return {"ok": True, "model": model, "archived_ids": ids}


def execute_erp_unlink(app: AppSettings, spec: dict[str, Any]) -> dict[str, Any]:
    client = OdooXmlRpc(app)
    model = spec["model"]
    ids = spec["record_ids"]
    try:
        client.execute_kw(model, "unlink", [ids])
    except xmlrpc.client.Fault as ex:
        raise ValueError(_format_odoo_fault(ex)) from ex
    return {"ok": True, "model": model, "deleted_ids": ids}


def sanitize_erp_draft_action(raw: dict[str, Any]) -> dict[str, Any] | None:
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ("read", "write", "archive", "unlink"):
        return None
    model = str(raw.get("model") or "").strip()
    summary = str(raw.get("summary") or "")[:240] or f"ERP {kind} {model}"
    try:
        if kind == "read":
            spec = sanitize_erp_read_draft(
                model,
                raw.get("domain"),
                raw.get("fields"),
                raw.get("limit"),
            )
            return {"operation": "erp", "kind": "read", "spec": spec, "summary": summary}
        if kind == "write":
            spec = sanitize_erp_write_draft(model, raw.get("record_id"), raw.get("values"))
            return {"operation": "erp", "kind": "write", "spec": spec, "summary": summary}
        if kind == "archive":
            spec = sanitize_erp_archive_draft(model, raw.get("record_ids"))
            return {"operation": "erp", "kind": "archive", "spec": spec, "summary": summary}
        spec = sanitize_erp_unlink_draft(model, raw.get("record_ids"))
        return {"operation": "erp", "kind": "unlink", "spec": spec, "summary": summary}
    except ValueError:
        return None


def execute_erp_action(app: AppSettings, kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta tras re-sanitizar `spec` (no confiar en el cuerpo del cliente)."""
    kind = str(kind or "").strip().lower()
    sp = spec if isinstance(spec, dict) else {}
    if kind == "read":
        s = sanitize_erp_read_draft(sp.get("model"), sp.get("domain"), sp.get("fields"), sp.get("limit"))
        return execute_erp_read(app, s)
    if kind == "write":
        s = sanitize_erp_write_draft(sp.get("model"), sp.get("record_id"), sp.get("values"))
        out = execute_erp_write(app, s)
        links = [link_record(app.odoo_url, out["model"], int(out["id"]), "Registro en Odoo")]
        return {**out, "odoo_links": links}
    if kind == "archive":
        s = sanitize_erp_archive_draft(sp.get("model"), sp.get("record_ids"))
        return execute_erp_archive(app, s)
    if kind == "unlink":
        s = sanitize_erp_unlink_draft(sp.get("model"), sp.get("record_ids"))
        return execute_erp_unlink(app, s)
    raise ValueError(f"Operación ERP no soportada: {kind}")
