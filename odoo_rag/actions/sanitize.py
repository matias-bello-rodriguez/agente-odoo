from __future__ import annotations

from typing import Any

from odoo_rag.actions.allowlists import (
    ALLOWED_CREATE_FIELDS,
    ALLOWED_EMAIL_TARGETS,
    ALLOWED_LIST_QUERIES,
    ALLOWED_MODELS,
    ALLOWED_WORKFLOWS,
)
from odoo_rag.erp_bridge import sanitize_erp_draft_action


def _coerce_value(model: str, field: str, value: Any) -> Any:
    if field == "is_company":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "sí", "si", "yes", "empresa")
        return bool(value)
    if field in {"list_price", "standard_price"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    if field in {
        "invoice_line_price_unit",
        "invoice_line_qty",
        "order_line_qty",
        "order_line_price_unit",
        "order_line_discount",
        "move_line_qty",
    }:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_product_type(raw: Any) -> str:
    """Odoo 19: product.template.type solo admite consu, service, combo (ya no existe 'product')."""
    t = str(raw).lower().strip()
    if t in ("consu", "service", "combo"):
        return t
    to_consu = {
        "product",
        "goods",
        "good",
        "material",
        "materials",
        "mercancia",
        "consumible",
        "consumo",
        "almacenable",
        "stock",
        "articulo",
        "artículo",
        "bien",
        "bienes",
    }
    if t in to_consu:
        return "consu"
    if "material" in t or "mercanc" in t or "perfil" in t or "bien" in t:
        return "consu"
    if t in ("servicio", "service"):
        return "service"
    if t == "combo":
        return "combo"
    return "consu"


def sanitize_values_for_model(model: str, values: dict[str, Any]) -> dict[str, Any]:
    allowed = ALLOWED_CREATE_FIELDS.get(model)
    if not allowed:
        raise ValueError(f"Modelo no permitido: {model}")
    out: dict[str, Any] = {}
    for key, raw in values.items():
        if key not in allowed:
            continue
        if raw is None or raw == "":
            continue
        out[key] = _coerce_value(model, key, raw)
    if model == "product.product" and "type" in out:
        out["type"] = _normalize_product_type(out["type"])
        if out["type"] not in ("consu", "service", "combo"):
            del out["type"]
    return out


def sanitize_draft_action(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw or not isinstance(raw, dict):
        return None
    if raw.get("operation") == "product_setup":
        plan = raw.get("plan")
        if not isinstance(plan, dict) or not str(plan.get("product_name") or "").strip():
            return None
        summary = raw.get("summary") or plan.get("product_name")
        return {
            "operation": "product_setup",
            "plan": plan,
            "summary": str(summary)[:240],
        }
    if raw.get("operation") == "list":
        query = str(raw.get("query") or "").strip()
        if query not in ALLOWED_LIST_QUERIES:
            return None
        summary = raw.get("summary") or "Lista"
        params = raw.get("params")
        if not isinstance(params, dict):
            params = {}
        return {
            "operation": "list",
            "query": query,
            "params": params,
            "summary": str(summary)[:240],
        }
    if raw.get("operation") == "email":
        target = str(raw.get("target") or "").strip().lower()
        if target not in ALLOWED_EMAIL_TARGETS:
            return None
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        subject = str(params.get("subject") or raw.get("subject") or "").strip()
        body = str(params.get("body") or raw.get("body") or "").strip()
        to_name = str(params.get("to_name") or raw.get("to_name") or "").strip()
        to_email = str(params.get("to_email") or raw.get("to_email") or "").strip()
        record_id = params.get("record_id") or raw.get("record_id")
        try:
            record_id_int = int(record_id) if record_id not in (None, "") else 0
        except (TypeError, ValueError):
            record_id_int = 0
        clean_params = {
            "subject": subject[:240],
            "body": body[:6000],
            "to_name": to_name[:240],
            "to_email": to_email[:240],
            "record_id": record_id_int,
        }
        summary = raw.get("summary") or f"Enviar correo ({target})"
        return {
            "operation": "email",
            "target": target,
            "params": clean_params,
            "summary": str(summary)[:240],
        }
    if raw.get("operation") == "workflow":
        name = str(raw.get("name") or "").strip().lower()
        if name not in ALLOWED_WORKFLOWS:
            return None
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        summary = raw.get("summary") or f"Workflow {name}"
        return {
            "operation": "workflow",
            "name": name,
            "params": params,
            "summary": str(summary)[:240],
        }
    if raw.get("operation") == "erp":
        erp = sanitize_erp_draft_action(raw)
        return erp
    if raw.get("operation") != "create":
        return None
    model = raw.get("model")
    if model not in ALLOWED_MODELS:
        return None
    vals_in = raw.get("values")
    if not isinstance(vals_in, dict):
        return None
    try:
        cleaned_vals = sanitize_values_for_model(model, vals_in)
    except ValueError:
        return None
    if not cleaned_vals:
        return None
    summary = raw.get("summary")
    return {
        "operation": "create",
        "model": model,
        "values": cleaned_vals,
        "summary": str(summary)[:240] if summary else f"Crear registro en {model}",
    }

