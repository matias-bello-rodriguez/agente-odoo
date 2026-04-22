"""Propuesta de altas en Odoo desde el chat + ejecución segura (lista blanca)."""

from __future__ import annotations

import json
import re
import xmlrpc.client
from typing import Any

from openai import OpenAI

from odoo_rag.config import Settings as AppSettings
from odoo_rag.odoo_client import OdooXmlRpc
from odoo_rag.product_setup import (
    DEFAULT_PRODUCT_SETUP_REPLY,
    extract_product_setup_draft,
    looks_like_full_product_setup,
    looks_like_leaked_structure_json,
)
from odoo_rag.rag import load_index_cached

ALLOWED_CREATE_FIELDS: dict[str, frozenset[str]] = {
    "res.partner": frozenset(
        {
            "name",
            "email",
            "phone",
            "street",
            "city",
            "zip",
            "vat",
            "is_company",
            "comment",
        }
    ),
    "product.product": frozenset(
        {
            "name",
            "default_code",
            "list_price",
            "standard_price",
            "type",
        }
    ),
    "account.move": frozenset(
        {
            "partner_name",
            "invoice_line_name",
            "invoice_line_price_unit",
            "invoice_date",
            "invoice_date_due",
            "ref",
            "narration",
        }
    ),
}

_ALLOWED_MODELS = frozenset(ALLOWED_CREATE_FIELDS.keys())


def retrieve_context_chunks(app: AppSettings, question: str, *, top_k: int) -> str:
    index = load_index_cached(app)
    retriever = index.as_retriever(similarity_top_k=top_k)
    nodes = retriever.retrieve(question)
    if not nodes:
        return "(No hay fragmentos relevantes en el índice.)"
    parts: list[str] = []
    for n in nodes:
        parts.append(n.get_content())
    return "\n---\n".join(parts)


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
    if model == "account.move" and field == "invoice_line_price_unit":
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    if isinstance(value, str):
        return value.strip()
    return value


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
    if raw.get("operation") != "create":
        return None
    model = raw.get("model")
    if model not in _ALLOWED_MODELS:
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


_STRUCTURED_SYSTEM = """Eres el asistente de una app web que habla con Odoo por API. El usuario confirma los datos en un modal y la app crea el registro: NO debe ir a pulsar menús en Odoo manualmente.

Responde SIEMPRE con UN solo objeto JSON (sin markdown). Campos obligatorios: "reply" (string) y "draft_action" (objeto o null).

## Cuándo draft_action ES OBLIGATORIO (no puede ser null)
Si el mensaje del usuario pide **crear, registrar, dar de alta, añadir, insertar o guardar** un contacto, producto o factura de cliente **y aporta al menos el dato principal** (nombre del contacto/producto o cliente en factura), DEBES rellenar draft_action con operation "create" y los valores extraídos del texto del usuario.

Palabras disparadoras (ejemplos): registra, crea, nuevo contacto, alta de cliente, dar de alta, añade empresa, inserta producto.

Si dice "empresa", "sociedad", "SA", "S.L." o similar para un contacto → model "res.partner" con "is_company": true.

## Cuándo draft_action debe ser null
- Solo consultas: listar, buscar, cuántos, resume, qué clientes… sin pedir alta.
- El usuario pide crear pero **no da ningún dato** (ni nombre): reply pidiendo nombre/email mínimos; draft_action null.

## PROHIBIDO en "reply" cuando envías draft_action
No escribas frases como: "debes seguir el procedimiento en Odoo", "completa el registro en el sistema", "utiliza el menú Contactos", "asegúrate de crear manualmente". La app abrirá un formulario de confirmación; tu reply debe ser **breve** (1–3 frases): confirmas que preparaste los datos para revisión y que puede confirmar en el modal.

## Campos permitidos en values
- res.partner: name, email, phone, street, city, zip, vat, is_company (boolean), comment
- product.product: name, default_code, list_price (precio venta), standard_price (costo), type siempre uno de: "consu" (bienes/material/mercancía), "service" (servicio), "combo". En Odoo 19 NO existe el valor "product"; para material físico usa "consu".
- account.move (factura cliente): partner_name, invoice_line_name, invoice_line_price_unit, invoice_date, invoice_date_due, ref, narration. Usa model "account.move".

Si el usuario dice **costo** o **precio de coste** → standard_price. Si dice **precio de venta** o **PVP** → list_price.

No inventes datos: solo lo que el usuario dijo.

## Ejemplo (debes imitar la lógica, no copiar texto literal si el usuario cambia datos)
Usuario: Registra un contacto empresa llamado ACME SA, email ventas@acme.cl, ciudad Santiago
Respuesta JSON:
{
  "reply": "He preparado el alta del contacto empresa ACME SA con los datos que indicaste. Revisa el formulario y confirma para crearlo en Odoo.",
  "draft_action": {
    "operation": "create",
    "model": "res.partner",
    "values": {
      "name": "ACME SA",
      "email": "ventas@acme.cl",
      "city": "Santiago",
      "is_company": true
    },
    "summary": "Nuevo contacto ACME SA"
  }
}

Usuario: He preparado la factura para SODIMAC por 40000 pesos
Respuesta JSON:
{
  "reply": "Preparé la factura de cliente para SODIMAC con el monto indicado. Revisá el formulario y confirmá para crearla en Odoo.",
  "draft_action": {
    "operation": "create",
    "model": "account.move",
    "values": {
      "partner_name": "SODIMAC",
      "invoice_line_name": "Factura de cliente",
      "invoice_line_price_unit": 40000
    },
    "summary": "Factura cliente SODIMAC"
  }
}
"""


def structured_chat_reply(app: AppSettings, user_message: str, *, top_k: int) -> dict[str, Any]:
    if not app.openai_api_key:
        raise RuntimeError("Falta OPENAI_API_KEY en .env.")
    if looks_like_full_product_setup(user_message):
        try:
            out = extract_product_setup_draft(app, user_message)
            if out.get("draft_action"):
                return out
        except (json.JSONDecodeError, RuntimeError, KeyError, TypeError):
            pass
    ctx = retrieve_context_chunks(app, user_message, top_k=top_k)
    client = OpenAI(api_key=app.openai_api_key)
    user_payload = (
        "Contexto recuperado del índice Odoo:\n"
        f"{ctx}\n\n---\n\n"
        "Mensaje del usuario:\n"
        f"{user_message.strip()}\n\n"
        "Si el mensaje pide registrar o crear un contacto/producto con datos concretos en el mismo texto, "
        "debés incluir draft_action con operation create y values rellenados (no solo explicar pasos)."
    )
    completion = client.chat.completions.create(
        model=app.openai_llm_model,
        response_format={"type": "json_object"},
        temperature=0.15,
        messages=[
            {"role": "system", "content": _STRUCTURED_SYSTEM},
            {"role": "user", "content": user_payload},
        ],
    )
    raw = completion.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if looks_like_leaked_structure_json(raw):
            return {
                "reply": "No pude interpretar la respuesta del modelo. Reformulá la solicitud.",
                "draft_action": None,
            }
        return {"reply": (raw or "")[:1200], "draft_action": None}

    reply = str(data.get("reply") or "").strip()
    draft = sanitize_draft_action(data.get("draft_action"))

    if looks_like_leaked_structure_json(reply):
        if draft and draft.get("operation") == "product_setup":
            reply = DEFAULT_PRODUCT_SETUP_REPLY
        elif draft:
            reply = (
                "Preparé los datos para crear el registro en Odoo. "
                "Revisá el formulario y confirmá."
            )
        else:
            reply = (
                "Recibí datos en un formato interno. Reformulá la solicitud o indicá nombre y datos del alta."
            )

    if not reply and draft:
        reply = (
            DEFAULT_PRODUCT_SETUP_REPLY
            if draft.get("operation") == "product_setup"
            else "Preparé los datos para Odoo; revisá el modal y confirmá para crear el registro."
        )

    return {"reply": reply.strip(), "draft_action": draft}


def _format_odoo_fault(exc: xmlrpc.client.Fault) -> str:
    """Extrae un mensaje legible del Fault XML-RPC de Odoo (UserError, ValidationError, etc.)."""
    fs = getattr(exc, "faultString", "") or ""
    for pattern in (
        r"UserError:\s*(.+?)(?:\n\n|\Z)",
        r"ValidationError:\s*(.+?)(?:\n\n|\Z)",
        r"AccessError:\s*(.+?)(?:\n\n|\Z)",
    ):
        m = re.search(pattern, fs, re.DOTALL | re.IGNORECASE)
        if m:
            msg = m.group(1).strip()
            msg = msg.strip("'\"")
            # Odoo a veces incluye markdown o HTML; primera línea suele bastar
            msg = msg.split("\n")[0].strip()
            return msg[:600]
    lines = [ln.strip() for ln in fs.splitlines() if ln.strip() and not ln.strip().startswith('File "')]
    return (lines[-1] if lines else str(exc))[:600]


def _preflight_duplicate_product_by_name(client: OdooXmlRpc, name: str) -> None:
    """Evita llamar create si ya hay product.product con ese nombre (mensaje más claro que el Fault genérico)."""
    rows = client.execute_kw(
        "product.product",
        "search_read",
        [[["name", "=", name]]],
        {"fields": ["id"], "limit": 10},
    )
    if rows:
        ids = ", ".join(str(r["id"]) for r in rows)
        raise ValueError(
            f'Ya existe un producto con el nombre «{name}» (product.product, ids: {ids}). '
            "Cambiá el nombre en el modal o editá ese registro en Odoo."
        )


def _find_partner_id_by_name(client: OdooXmlRpc, partner_name: str) -> int:
    name = str(partner_name or "").strip()
    if not name:
        raise ValueError("El nombre del cliente es obligatorio para la factura.")
    rows = client.execute_kw(
        "res.partner",
        "search_read",
        [[["name", "ilike", name], ["customer_rank", ">=", 0]]],
        {"fields": ["id", "name"], "limit": 5, "order": "id desc"},
    )
    if not rows:
        raise ValueError(f"PARTNER_NOT_FOUND::{name}")
    exact = [r for r in rows if str(r.get("name", "")).strip().lower() == name.lower()]
    picked = exact[0] if exact else rows[0]
    return int(picked["id"])


def _build_invoice_create_vals(client: OdooXmlRpc, cleaned: dict[str, Any]) -> dict[str, Any]:
    partner_name = str(cleaned.get("partner_name") or "").strip()
    line_name = str(cleaned.get("invoice_line_name") or "").strip() or "Servicio"
    amount = cleaned.get("invoice_line_price_unit")
    if amount in (None, ""):
        raise ValueError("El monto de la factura es obligatorio.")
    partner_id = _find_partner_id_by_name(client, partner_name)
    vals: dict[str, Any] = {
        "move_type": "out_invoice",
        "partner_id": partner_id,
        "invoice_line_ids": [
            (
                0,
                0,
                {
                    "name": line_name,
                    "quantity": 1.0,
                    "price_unit": float(amount),
                },
            )
        ],
    }
    if cleaned.get("invoice_date"):
        vals["invoice_date"] = str(cleaned["invoice_date"])
    if cleaned.get("invoice_date_due"):
        vals["invoice_date_due"] = str(cleaned["invoice_date_due"])
    if cleaned.get("ref"):
        vals["ref"] = str(cleaned["ref"])
    if cleaned.get("narration"):
        vals["narration"] = str(cleaned["narration"])
    return vals


def build_missing_partner_suggestion(partner_name: str) -> dict[str, Any]:
    guessed_name = str(partner_name or "").strip()
    return {
        "operation": "create",
        "model": "res.partner",
        "values": {
            "name": guessed_name,
            "is_company": True,
        },
        "summary": f"Crear cliente {guessed_name}" if guessed_name else "Crear cliente",
    }


def execute_create(app: AppSettings, model: str, values: dict[str, Any]) -> int:
    cleaned = sanitize_values_for_model(model, values)
    if not cleaned.get("name") and model == "res.partner":
        raise ValueError("El nombre del contacto es obligatorio.")
    if not cleaned.get("name") and model == "product.product":
        raise ValueError("El nombre del producto es obligatorio.")
    client = OdooXmlRpc(app)
    payload = cleaned
    if model == "account.move":
        payload = _build_invoice_create_vals(client, cleaned)
    if model == "product.product" and cleaned.get("name"):
        _preflight_duplicate_product_by_name(client, cleaned["name"])
    try:
        rec_id = client.execute_kw(model, "create", [payload])
    except xmlrpc.client.Fault as ex:
        raise ValueError(_format_odoo_fault(ex)) from ex
    return int(rec_id)
