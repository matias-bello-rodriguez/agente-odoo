"""Propuesta de altas en Odoo desde el chat + ejecución segura (lista blanca)."""

from __future__ import annotations

import json
import re
import unicodedata
import xmlrpc.client
from datetime import date, timedelta
from typing import Any

from openai import OpenAI

from odoo_rag.config import Settings as AppSettings
from odoo_rag.odoo_client import OdooXmlRpc
from odoo_rag.erp_bridge import sanitize_erp_draft_action
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
            "move_kind",
            "partner_name",
            "invoice_line_name",
            "invoice_line_price_unit",
            "invoice_line_qty",
            "invoice_date",
            "invoice_date_due",
            "ref",
            "narration",
        }
    ),
    "sale.order": frozenset(
        {
            "partner_name",
            "order_line_name",
            "order_line_qty",
            "order_line_price_unit",
            "order_line_discount",
            "client_order_ref",
            "note",
        }
    ),
    "purchase.order": frozenset(
        {
            "vendor_name",
            "order_line_name",
            "order_line_qty",
            "order_line_price_unit",
            "partner_ref",
            "notes",
        }
    ),
    "stock.picking": frozenset(
        {
            "picking_type_code",
            "partner_name",
            "origin",
            "move_line_name",
            "product_name",
            "move_line_qty",
        }
    ),
}

_ALLOWED_MODELS = frozenset(ALLOWED_CREATE_FIELDS.keys())
_ALLOWED_LIST_QUERIES = frozenset(
    {
        "delivery_orders",
        "users_roles",
        "accounting_recent_actions",
        "accounting_missing_key_data",
        "users_last_login",
        "dirty_data_overview",
        "invoice_from_order_check",
        "overdue_invoices",
        "low_stock_products",
        "best_vendor_for_product",
        "payroll_preview",
        "dashboard_overview",
        "latest_product",
        "sales_quarter_compare",
        "customers_drop_with_active_contracts",
        "demand_forecast_purchase_hints",
        "sales_last_month_total",
        "issued_invoices_month_total",
    }
)
_ALLOWED_EMAIL_TARGETS = frozenset({"partner", "invoice", "sale_order", "purchase_order"})
_ALLOWED_WORKFLOWS = frozenset({"lead_to_payment"})


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
    if raw.get("operation") == "list":
        query = str(raw.get("query") or "").strip()
        if query not in _ALLOWED_LIST_QUERIES:
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
        if target not in _ALLOWED_EMAIL_TARGETS:
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
        if name not in _ALLOWED_WORKFLOWS:
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
Si el mensaje del usuario pide **crear, registrar, dar de alta, añadir, insertar o guardar** un registro de ventas, compras, inventario, facturación o maestro (contacto/producto) y aporta datos mínimos, DEBES rellenar draft_action con operation "create" y values.

Palabras disparadoras (ejemplos): registra, crea, nuevo contacto, alta de cliente, dar de alta, añade empresa, inserta producto.

Si dice "empresa", "sociedad", "SA", "S.L." o similar para un contacto → model "res.partner" con "is_company": true.

## Cuándo draft_action debe ser null
- Solo consultas: listar, buscar, cuántos, resume, qué clientes… sin pedir alta.
- El usuario pide crear pero **no da ningún dato** (ni nombre): reply pidiendo nombre/email mínimos; draft_action null.

## operation "list" (tablas y análisis en modal)
Si el usuario pide **proyección o modelo predictivo de demanda**, **pronóstico** de ventas a futuro o **sugerencias de compras** en sentido analítico (no dice explícitamente «crear orden de compra» ni da proveedor), respondé con draft_action:
`{"operation":"list","query":"demand_forecast_purchase_hints","params":{"horizon_months":3},"summary":"Demanda y compras"}`
Ajustá `horizon_months` (1–12) si indica otro horizonte (ej. 6 meses → 6). **No** confundas «generar un modelo» o «generar un pronóstico» con alta de `purchase.order`.

## operation "erp" (consultar, actualizar, archivar o borrar en Odoo)
Usá `draft_action` con **operation** `"erp"`, **kind** en `read` | `write` | `archive` | `unlink`, **model** y el resto según kind. La app valida todo contra listas blancas; dominios solo AND de tripletas `[campo, operador, valor]`.

### kind "read" (consulta / listado)
Modelos permitidos: `res.partner`, `product.product`, `sale.order`, `purchase.order`, `account.move`, `stock.picking`.
Incluí `domain` (array de tripletas), `fields` (array de nombres de campo permitidos para ese modelo), `limit` (número, máx. según modelo).
Operadores de dominio permitidos: `=`, `!=`, `>`, `<`, `>=`, `<=`, `ilike`, `like`, `in`, `not in`.
Ejemplo: listar clientes activos con email gmail:
`{"operation":"erp","kind":"read","model":"res.partner","domain":[["active","=",true],["email","ilike","gmail"]],"fields":["id","name","email","city"],"limit":40,"summary":"Buscar clientes"}`

### kind "write" (actualizar)
Solo campos permitidos por modelo. `account.move`: solo `ref` y `narration` y **solo si la factura está en borrador** (la app lo verifica).
`res.partner`: name, email, phone, street, city, zip, vat, comment, is_company, active.
`product.product`: name, default_code, list_price, standard_price, type, active, barcode.
`sale.order`: note, client_order_ref. `purchase.order`: notes, partner_ref.
Ejemplo: `{"operation":"erp","kind":"write","model":"res.partner","record_id":12,"values":{"phone":"+56 9 0000 0000"},"summary":"Actualizar teléfono"}`

### kind "archive" (desactivar / “borrar” suave)
Modelos: `res.partner`, `product.product`. `record_ids` array de enteros (máx. 8). Equivale a `active: false`.
Ejemplo: `{"operation":"erp","kind":"archive","model":"product.product","record_ids":[101],"summary":"Archivar producto"}`

### kind "unlink" (borrado físico, muy restringido)
Solo `product.product`, máximo **2** ids en `record_ids`. Usalo solo si el usuario pide explícitamente eliminar producto y entendés el riesgo.
Ejemplo: `{"operation":"erp","kind":"unlink","model":"product.product","record_ids":[55],"summary":"Eliminar producto"}`

Para **altas nuevas** seguí usando operation `"create"` con `values` como hasta ahora.

## PROHIBIDO en "reply" cuando envías draft_action
No escribas frases como: "debes seguir el procedimiento en Odoo", "completa el registro en el sistema", "utiliza el menú Contactos", "asegúrate de crear manualmente". La app abrirá un formulario de confirmación; tu reply debe ser **breve** (1–3 frases): confirmas que preparaste los datos para revisión y que puede confirmar en el modal.

## Campos permitidos en values
- res.partner: name, email, phone, street, city, zip, vat, is_company (boolean), comment
- product.product: name, default_code, list_price (precio venta), standard_price (costo), type siempre uno de: "consu" (bienes/material/mercancía), "service" (servicio), "combo". En Odoo 19 NO existe el valor "product"; para material físico usa "consu".
- account.move (facturas): move_kind ("out_invoice" cliente o "in_invoice" proveedor), partner_name, invoice_line_name, invoice_line_price_unit, invoice_line_qty, invoice_date, invoice_date_due, ref, narration.
- sale.order (ventas): partner_name, order_line_name, order_line_qty, order_line_price_unit, client_order_ref, note.
- purchase.order (compras): vendor_name, order_line_name, order_line_qty, order_line_price_unit, partner_ref, notes.
- stock.picking (inventario): picking_type_code ("incoming","outgoing","internal"), partner_name opcional, origin, product_name, move_line_name, move_line_qty.

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
      "move_kind": "out_invoice",
      "invoice_line_name": "Factura de cliente",
      "invoice_line_price_unit": 40000,
      "invoice_line_qty": 1
    },
    "summary": "Factura cliente SODIMAC"
  }
}
"""


def structured_chat_reply(app: AppSettings, user_message: str, *, top_k: int) -> dict[str, Any]:
    if not app.openai_api_key:
        raise RuntimeError("Falta OPENAI_API_KEY en .env.")
    lowered = user_message.lower()
    lowered_norm = unicodedata.normalize("NFKD", lowered)
    lowered_norm = "".join(ch for ch in lowered_norm if not unicodedata.combining(ch))
    invoice_word_like = bool(
        "factur" in lowered_norm
        or "fatctur" in lowered_norm
        or re.search(r"f[a-z]{0,2}ctur", lowered_norm)
    )
    if (
        "dashboard" in lowered
        or "panel" in lowered
        or "kpi" in lowered
        or "kpis" in lowered
        or ("indicadores" in lowered and ("ventas" in lowered or "negocio" in lowered or "empresa" in lowered or "general" in lowered))
        or ("resumen" in lowered and ("ventas" in lowered or "negocio" in lowered or "empresa" in lowered or "general" in lowered))
    ):
        return {
            "reply": "Preparé un panel con los KPIs principales (ventas, facturación, cobranza, stock). Lo abro en un modal.",
            "draft_action": {
                "operation": "list",
                "query": "dashboard_overview",
                "summary": "Dashboard general",
            },
        }
    if (
        ("venta" in lowered_norm or "ventas" in lowered_norm)
        and ("ultimo mes" in lowered_norm or "último mes" in lowered)
        and (
            "total" in lowered_norm
            or "cuanto" in lowered_norm
            or "cuánto" in lowered
            or "monto" in lowered_norm
        )
    ):
        return {
            "reply": "Voy a calcular el total de ventas del último mes y te lo muestro en un modal.",
            "draft_action": {
                "operation": "list",
                "query": "sales_last_month_total",
                "summary": "Total ventas último mes",
            },
        }
    if (
        ("factura" in lowered_norm or "facturas" in lowered_norm)
        and ("emitidas" in lowered_norm or "emitida" in lowered_norm or "publicadas" in lowered_norm)
        and ("mes" in lowered_norm)
        and (
            "suma" in lowered_norm
            or "total" in lowered_norm
            or "sumalos" in lowered_norm
            or "sumalos todos" in lowered_norm
            or "sumalas" in lowered_norm
        )
    ):
        return {
            "reply": "Voy a calcular la suma de todas las facturas emitidas del mes y te lo muestro en un modal.",
            "draft_action": {
                "operation": "list",
                "query": "issued_invoices_month_total",
                "summary": "Suma facturas emitidas del mes",
            },
        }
    if (
        ("ventas" in lowered_norm or "venta" in lowered_norm)
        and ("ultimo trimestre" in lowered_norm or "ultim trimestre" in lowered_norm or "trimestre" in lowered_norm)
        and ("ano pasado" in lowered_norm or "año pasado" in lowered or "mismo periodo" in lowered_norm)
        and ("region" in lowered_norm or "región" in lowered)
        and ("canal" in lowered_norm)
        and ("margen" in lowered_norm or "margen neto" in lowered_norm)
    ):
        return {
            "reply": "Preparé la comparación trimestral de ventas vs año pasado por región y canal, con margen neto estimado incluyendo logística variable.",
            "draft_action": {
                "operation": "list",
                "query": "sales_quarter_compare",
                "params": {"logistic_rate": 0.08},
                "summary": "Ventas trimestre vs año pasado",
            },
        }
    if (
        ("cliente" in lowered_norm or "clientes" in lowered_norm)
        and ("caido" in lowered_norm or "caida" in lowered_norm or "bajado" in lowered_norm or "disminuido" in lowered_norm)
        and ("20%" in lowered_norm or "20 %" in lowered_norm or "veinte" in lowered_norm)
        and ("mes a mes" in lowered_norm or "mensual" in lowered_norm)
        and ("contrato" in lowered_norm or "contratos" in lowered_norm)
        and ("incidencia" in lowered_norm or "incidencias" in lowered_norm or "facturacion" in lowered_norm)
    ):
        return {
            "reply": "Preparé el análisis de clientes con caída mensual mayor al 20%, filtrando por contrato activo y sin incidencias de facturación.",
            "draft_action": {
                "operation": "list",
                "query": "customers_drop_with_active_contracts",
                "params": {"drop_pct_threshold": 20.0},
                "summary": "Clientes con caída >20% (contrato activo, sin incidencias)",
            },
        }
    if (
        ("flujo" in lowered or "workflow" in lowered or "proceso" in lowered)
        and ("lead" in lowered or "oportunidad" in lowered)
        and ("pago" in lowered or "factur" in lowered or "venta" in lowered)
    ):
        m_partner = re.search(r"(?:cliente|para)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&-]+?)(?:\s+por|\s+con|\s+monto|\s*$)", user_message, re.IGNORECASE)
        partner_name = (m_partner.group(1).strip() if m_partner else "").strip()
        m_amt = re.search(r"(\d[\d\.\,]*)\s*(?:pesos|clp|usd|dolares|d[oó]lares|monto)?", lowered)
        amount = float(m_amt.group(1).replace(".", "").replace(",", ".")) if m_amt else 0.0
        return {
            "reply": "Preparé el workflow lead→cotización→venta→factura→pago. Revisalo y confirmá para ejecutarlo paso a paso.",
            "draft_action": {
                "operation": "workflow",
                "name": "lead_to_payment",
                "params": {
                    "partner_name": partner_name,
                    "amount": amount,
                    "product_name": "",
                },
                "summary": "Flujo lead → pago",
            },
        }
    if (
        ("envia" in lowered or "envía" in lowered or "enviar" in lowered or "manda" in lowered or "mandar" in lowered or "mandame" in lowered)
        and ("correo" in lowered or "email" in lowered or "mail" in lowered)
    ):
        target = "partner"
        m_email_first = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", user_message)
        text_no_email = (user_message[: m_email_first.start()] + " " + user_message[m_email_first.end():]).lower() if m_email_first else lowered
        if "factur" in text_no_email:
            target = "invoice"
        elif re.search(r"\bventa", text_no_email) or "cotiz" in text_no_email or "pedido" in text_no_email:
            target = "sale_order"
        elif re.search(r"\bcompra", text_no_email) or "proveedor" in text_no_email:
            target = "purchase_order"
        m_email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", user_message)
        to_email = m_email.group(0) if m_email else ""
        m_to = re.search(r"\ba\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&-]+?)(?:\s+sobre|\s+con|\s+asunto|\s+por|\s+para|\s*$)", user_message, re.IGNORECASE)
        to_name = (m_to.group(1).strip() if m_to else "").strip()
        m_subj = re.search(r"asunto\s+[\"\u2018\u2019\u201C\u201D']?([^\"\u2018\u2019\u201C\u201D'\n]+)", user_message, re.IGNORECASE)
        subject = (m_subj.group(1).strip() if m_subj else "").strip()
        return {
            "reply": "Preparé el correo. Revisá destinatario, asunto y cuerpo en el modal y confirmá para enviarlo desde Odoo.",
            "draft_action": {
                "operation": "email",
                "target": target,
                "params": {
                    "to_name": to_name,
                    "to_email": to_email,
                    "subject": subject or "Mensaje desde Odoo",
                    "body": "",
                    "record_id": 0,
                },
                "summary": "Enviar correo",
            },
        }
    if (
        ("orden" in lowered_norm or "ordenes" in lowered_norm)
        and ("entregar" in lowered_norm or "entrega" in lowered_norm or "delivery" in lowered_norm)
        and (
            "lista" in lowered_norm
            or "listar" in lowered_norm
            or "muestrame" in lowered_norm
            or "mostrar" in lowered_norm
            or "dame" in lowered_norm
        )
    ):
        return {
            "reply": "Preparé la lista de órdenes por entregar. Abro el modal para que la revises.",
            "draft_action": {
                "operation": "list",
                "query": "delivery_orders",
                "summary": "Órdenes por entregar",
            },
        }
    m_order_code = re.search(r"\bS\d{3,}\b", user_message, re.IGNORECASE)
    if m_order_code and (
        "ver" in lowered_norm
        or "mostrar" in lowered_norm
        or "muestrame" in lowered_norm
        or "esta" in lowered_norm
        or "orden" in lowered_norm
    ):
        order_code = m_order_code.group(0).upper()
        return {
            "reply": f"Voy a mostrarte la orden {order_code}. Abro el modal con su detalle.",
            "draft_action": {
                "operation": "list",
                "query": "delivery_orders",
                "params": {"order_ref": order_code},
                "summary": f"Orden {order_code}",
            },
        }
    if (
        ("usuario" in lowered or "usuarios" in lowered)
        and ("rol" in lowered or "roles" in lowered or "grupos" in lowered)
        and ("lista" in lowered or "listar" in lowered or "muéstrame" in lowered or "muestrame" in lowered)
    ):
        return {
            "reply": "Preparé la lista de usuarios y roles. Abro el modal para que la revises.",
            "draft_action": {
                "operation": "list",
                "query": "users_roles",
                "summary": "Usuarios y roles",
            },
        }
    if (
        ("ultima" in lowered_norm or "última" in lowered or "reciente" in lowered_norm)
        and invoice_word_like
        and ("mostrar" in lowered_norm or "muestrame" in lowered_norm or "ver" in lowered_norm or "dame" in lowered_norm)
    ):
        return {
            "reply": "Voy a mostrarte la última factura en un modal.",
            "draft_action": {
                "operation": "list",
                "query": "accounting_recent_actions",
                "params": {"latest_only": True},
                "summary": "Última factura",
            },
        }
    if (
        ("ultimo" in lowered_norm or "ultima" in lowered_norm or "reciente" in lowered_norm)
        and ("producto" in lowered_norm or "articulo" in lowered_norm or "item" in lowered_norm or "ítem" in lowered)
        and ("ingresado" in lowered_norm or "creado" in lowered_norm or "registrado" in lowered_norm or "alta" in lowered_norm)
    ):
        return {
            "reply": "Voy a mostrarte el último producto ingresado en un modal.",
            "draft_action": {
                "operation": "list",
                "query": "latest_product",
                "summary": "Último producto ingresado",
            },
        }
    if (
        ("factur" in lowered or "contabil" in lowered)
        and ("ultima" in lowered or "última" in lowered or "reciente" in lowered)
        and ("accion" in lowered or "acción" in lowered or "acciones" in lowered)
    ):
        return {
            "reply": "Preparé la lista de últimas acciones de facturación. Abro el modal para que la revises.",
            "draft_action": {
                "operation": "list",
                "query": "accounting_recent_actions",
                "summary": "Últimas acciones en facturación",
            },
        }
    if (
        "factur" in lowered
        and ("falt" in lowered or "incomplet" in lowered or "clave" in lowered)
        and ("dato" in lowered or "campos" in lowered)
    ):
        return {
            "reply": "Revisé facturas con datos clave faltantes. Abro el modal con el detalle.",
            "draft_action": {
                "operation": "list",
                "query": "accounting_missing_key_data",
                "summary": "Facturas con datos faltantes",
            },
        }
    if (
        ("ultima" in lowered or "última" in lowered)
        and ("conexion" in lowered or "conexión" in lowered or "login" in lowered)
        and ("usuario" in lowered or "usuarios" in lowered)
    ):
        return {
            "reply": "Preparé la lista con la última conexión de usuarios. Abro el modal.",
            "draft_action": {
                "operation": "list",
                "query": "users_last_login",
                "summary": "Última conexión de usuarios",
            },
        }
    if "dato" in lowered and ("sucio" in lowered or "sucios" in lowered):
        return {
            "reply": "Preparé un chequeo de datos sucios. Abro el modal con hallazgos.",
            "draft_action": {
                "operation": "list",
                "query": "dirty_data_overview",
                "summary": "Datos sucios detectados",
            },
        }
    if ("factura" in lowered or "factur" in lowered) and ("orden" in lowered) and ("existe" in lowered or "duplic" in lowered):
        m = re.search(r"(?:orden|ov|so|#)\s*#?([A-Za-z0-9/\-]+)", user_message, re.IGNORECASE)
        order_ref = m.group(1) if m else ""
        return {
            "reply": "Voy a revisar si existe factura para esa orden y si hay duplicados. Abro el modal.",
            "draft_action": {
                "operation": "list",
                "query": "invoice_from_order_check",
                "params": {"order_ref": order_ref},
                "summary": "Control de factura por orden",
            },
        }
    if ("factura" in lowered or "facturas" in lowered) and ("vencid" in lowered or "atrasad" in lowered):
        return {
            "reply": "Preparé la lista de facturas vencidas. Abro el modal.",
            "draft_action": {
                "operation": "list",
                "query": "overdue_invoices",
                "summary": "Facturas vencidas",
            },
        }
    if (
        ("stock" in lowered or "inventario" in lowered or "producto" in lowered or "productos" in lowered)
        and ("bajo" in lowered or "mínimo" in lowered or "minimo" in lowered)
    ):
        return {
            "reply": "Preparé productos bajo mínimo y sugerencias de reposición. Abro el modal.",
            "draft_action": {
                "operation": "list",
                "query": "low_stock_products",
                "summary": "Bajo stock y reposición",
            },
        }
    if (
        re.search(r"\b(demanda|predictivo|pronostico|forecast|proyeccion|modelo\s+predictivo)\b", lowered_norm)
        and re.search(r"\b(compra|compras|abastecimiento|reposicion|proveedor)\b", lowered_norm)
    ) or (
        re.search(r"\b(demanda|predictivo|pronostico)\b", lowered_norm)
        and re.search(r"\bmes(es)?\b", lowered_norm)
    ):
        hm = 3
        if re.search(r"\btres\s+mes", lowered_norm):
            hm = 3
        elif (m_hm := re.search(r"\b(\d{1,2})\s*mes", lowered_norm)):
            try:
                hm = max(1, min(12, int(m_hm.group(1))))
            except ValueError:
                hm = 3
        return {
            "reply": "Preparé una vista con proyección simple de demanda (próximos meses) y sugerencias de ajuste en compras según ventas recientes y reposición.",
            "draft_action": {
                "operation": "list",
                "query": "demand_forecast_purchase_hints",
                "params": {"horizon_months": hm},
                "summary": "Demanda predictiva y compras",
            },
        }
    _analytics_context = bool(
        re.search(
            r"\b(modelo|predictivo|pronostico|forecast|demanda|machine\s+learning|\bml\b|estadistico|analisis|insight|reporte|grafico|dashboard)\b",
            lowered_norm,
        )
    )
    if ("sin proveedor" in lowered_norm) or (
        not _analytics_context
        and ("crear" in lowered_norm or "crea" in lowered_norm or "genera" in lowered_norm)
        and "proveedor" not in lowered_norm
        and (
            re.search(r"\borden\s+de\s+compra\b", lowered_norm)
            or re.search(r"\bpurchase\s+order\b", lowered_norm)
            or (re.search(r"\borden\b", lowered_norm) and re.search(r"\bcompras?\b", lowered_norm))
        )
    ):
        return {
            "reply": "Falta el proveedor para crear la orden. Indica proveedor, producto, cantidad y precio si lo tienes; con eso preparo el formulario.",
            "draft_action": None,
        }
    if (
        "proveedor" in lowered_norm
        and ("barato" in lowered_norm or "mas barato" in lowered_norm or "mejor precio" in lowered_norm)
        and ("producto" in lowered_norm or "item" in lowered_norm or "articulo" in lowered_norm)
    ):
        m_qty = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:unidades|unidad|u)\b", lowered)
        qty = float(m_qty.group(1).replace(",", ".")) if m_qty else 1.0
        m_prod = re.search(r"(?:producto)\s+([A-Za-z0-9 _\-/]+?)(?:\s+al proveedor|\s*$)", user_message, re.IGNORECASE)
        product_name = (m_prod.group(1).strip() if m_prod else "").strip()
        if not product_name:
            return {
                "reply": "Para comparar proveedores necesito el nombre del producto. Indícamelo y te muestro el más barato.",
                "draft_action": None,
            }
        return {
            "reply": "Voy a buscar el proveedor más barato para ese producto. Abro el modal.",
            "draft_action": {
                "operation": "list",
                "query": "best_vendor_for_product",
                "params": {"product_name": product_name, "qty": qty},
                "summary": "Proveedor más barato",
            },
        }
    if ("cotiz" in lowered) and ("descuento" in lowered):
        m_disc = re.search(r"(\d+(?:[.,]\d+)?)\s*%", lowered)
        discount = float(m_disc.group(1).replace(",", ".")) if m_disc else 0.0
        m_partner = re.search(r"para\s+(?:cliente\s+)?([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .,&-]+?)(?:\s+con|\s*$)", user_message, re.IGNORECASE)
        partner_name = (m_partner.group(1).strip() if m_partner else "").strip()
        if not partner_name or partner_name.lower() in {"cliente frecuente", "frecuente"}:
            return {
                "reply": "Para crear la cotización con descuento necesito el nombre del cliente y al menos una línea (producto/servicio, cantidad y precio).",
                "draft_action": None,
            }
        return {
            "reply": "Preparé la cotización con descuento para revisión en modal. Confirma para crearla.",
            "draft_action": {
                "operation": "create",
                "model": "sale.order",
                "values": {
                    "order_line_name": "Cotización comercial",
                    "order_line_qty": 1,
                    "order_line_price_unit": 0,
                    "order_line_discount": discount,
                    "partner_name": partner_name,
                },
                "summary": "Cotización con descuento",
            },
        }
    if ("sueldo" in lowered or "nomina" in lowered or "nómina" in lowered) and ("hora" in lowered or "bono" in lowered):
        return {
            "reply": "Voy a calcular un preview de sueldo con horas extra y bonos. Abro el modal.",
            "draft_action": {
                "operation": "list",
                "query": "payroll_preview",
                "summary": "Preview nómina",
            },
        }
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
        "Si el mensaje pide registrar o crear registros de ventas, compras, inventario, facturas o maestros con datos concretos en el mismo texto, "
        "debés incluir draft_action con operation create y values rellenados (no solo explicar pasos). "
        "Si pide buscar, listar, actualizar, archivar o borrar datos ya existentes en Odoo, usá draft_action con operation erp y kind read|write|archive|unlink según corresponda (ver reglas del sistema)."
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


def execute_list_query(app: AppSettings, query: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if query not in _ALLOWED_LIST_QUERIES:
        raise ValueError(f"Consulta no permitida: {query}")
    p = params or {}
    client = OdooXmlRpc(app)
    if query == "sales_last_month_total":
        today = date.today()
        first_day_this_month = date(today.year, today.month, 1)
        last_day_prev_month = first_day_this_month - timedelta(days=1)
        first_day_prev_month = date(last_day_prev_month.year, last_day_prev_month.month, 1)
        try:
            rows = client.execute_kw(
                "sale.order",
                "read_group",
                [
                    [
                        ["state", "in", ["sale", "done"]],
                        ["date_order", ">=", first_day_prev_month.isoformat()],
                        ["date_order", "<=", last_day_prev_month.isoformat()],
                    ],
                    ["amount_total:sum"],
                    [],
                ],
                {"lazy": False},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        total = float(rows[0].get("amount_total") if rows else 0.0) if rows else 0.0
        return {
            "query": "sales_last_month_total",
            "title": "Total ventas del último mes",
            "count": 1,
            "items": [
                {
                    "period": f"{first_day_prev_month.isoformat()} a {last_day_prev_month.isoformat()}",
                    "sales_total": round(total, 2),
                }
            ],
            "meta": {
                "period_start": first_day_prev_month.isoformat(),
                "period_end": last_day_prev_month.isoformat(),
            },
            "hint": "",
        }
    if query == "issued_invoices_month_total":
        today = date.today()
        first_day_month = date(today.year, today.month, 1)
        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)
        last_day_month = next_month - timedelta(days=1)
        try:
            rows = client.execute_kw(
                "account.move",
                "read_group",
                [
                    [
                        ["move_type", "=", "out_invoice"],
                        ["state", "=", "posted"],
                        ["invoice_date", ">=", first_day_month.isoformat()],
                        ["invoice_date", "<=", last_day_month.isoformat()],
                    ],
                    ["amount_total:sum", "id:count"],
                    [],
                ],
                {"lazy": False},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        amount_total = float(rows[0].get("amount_total") if rows else 0.0) if rows else 0.0
        raw_count = (rows[0].get("id_count") if rows else None) or (rows[0].get("__count") if rows else 0) or 0
        try:
            invoices_count = int(raw_count)
        except (TypeError, ValueError):
            invoices_count = 0
        return {
            "query": "issued_invoices_month_total",
            "title": "Suma de facturas emitidas del mes",
            "count": 1,
            "items": [
                {
                    "period": f"{first_day_month.isoformat()} a {last_day_month.isoformat()}",
                    "invoices_count": invoices_count,
                    "invoices_total": round(amount_total, 2),
                }
            ],
            "meta": {
                "period_start": first_day_month.isoformat(),
                "period_end": last_day_month.isoformat(),
            },
            "hint": "",
        }
    if query == "customers_drop_with_active_contracts":
        try:
            drop_threshold = float(p.get("drop_pct_threshold") or 20.0)
        except (TypeError, ValueError):
            drop_threshold = 20.0
        if drop_threshold < 1:
            drop_threshold = 1.0
        if drop_threshold > 95:
            drop_threshold = 95.0

        today = date.today()
        first_day_this_month = date(today.year, today.month, 1)
        last_day_prev_month = first_day_this_month - timedelta(days=1)
        first_day_prev_month = date(last_day_prev_month.year, last_day_prev_month.month, 1)
        last_day_prev2_month = first_day_prev_month - timedelta(days=1)
        first_day_prev2_month = date(last_day_prev2_month.year, last_day_prev2_month.month, 1)

        try:
            sales_rows = client.execute_kw(
                "sale.order",
                "search_read",
                [[
                    ["state", "in", ["sale", "done"]],
                    ["date_order", ">=", first_day_prev2_month.isoformat()],
                    ["date_order", "<=", last_day_prev_month.isoformat()],
                ]],
                {
                    "fields": ["id", "partner_id", "date_order", "amount_total"],
                    "limit": 5000,
                    "order": "date_order desc, id desc",
                },
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex

        by_partner: dict[int, dict[str, float]] = {}
        for row in sales_rows:
            partner = row.get("partner_id")
            if not (isinstance(partner, (list, tuple)) and partner):
                continue
            pid = int(partner[0])
            dt = str(row.get("date_order") or "")[:10]
            amount = float(row.get("amount_total") or 0.0)
            if pid not in by_partner:
                by_partner[pid] = {"prev_month": 0.0, "prev2_month": 0.0}
            if first_day_prev_month.isoformat() <= dt <= last_day_prev_month.isoformat():
                by_partner[pid]["prev_month"] += amount
            elif first_day_prev2_month.isoformat() <= dt <= last_day_prev2_month.isoformat():
                by_partner[pid]["prev2_month"] += amount

        # Contratos activos: intenta usar sale.subscription si existe.
        active_contract_partner_ids: set[int] = set()
        try:
            sub_rows = client.execute_kw(
                "sale.subscription",
                "search_read",
                [[["partner_id", "!=", False]]],
                {"fields": ["partner_id", "stage_category"], "limit": 5000},
            )
            for sr in sub_rows:
                partner = sr.get("partner_id")
                stage = str(sr.get("stage_category") or "").lower()
                if isinstance(partner, (list, tuple)) and partner and stage not in {"closed", "cancel"}:
                    active_contract_partner_ids.add(int(partner[0]))
        except xmlrpc.client.Fault:
            # Si no hay módulo contratos, se intenta campo contractual en partner.
            try:
                partner_rows = client.execute_kw(
                    "res.partner",
                    "search_read",
                    [[["id", "in", sorted(by_partner.keys())]]],
                    {"fields": ["id", "is_company", "active"], "limit": len(by_partner) + 10},
                )
                for pr in partner_rows:
                    if bool(pr.get("active")):
                        active_contract_partner_ids.add(int(pr.get("id") or 0))
            except xmlrpc.client.Fault:
                active_contract_partner_ids = set()

        candidate_ids = [pid for pid, vals in by_partner.items() if vals["prev2_month"] > 0 and pid in active_contract_partner_ids]

        items: list[dict[str, Any]] = []
        for pid in candidate_ids:
            prev2 = by_partner[pid]["prev2_month"]
            prev1 = by_partner[pid]["prev_month"]
            drop_pct = ((prev2 - prev1) / prev2) * 100.0 if prev2 else 0.0
            if drop_pct <= drop_threshold:
                continue

            # Incidencias facturación: vencidas/no pagadas o canceladas en últimos 6 meses.
            six_months_ago = (today - timedelta(days=180)).isoformat()
            try:
                incidents_count = int(
                    client.execute_kw(
                        "account.move",
                        "search_count",
                        [[
                            ["partner_id", "=", pid],
                            ["move_type", "in", ["out_invoice", "in_invoice"]],
                            ["invoice_date", ">=", six_months_ago],
                            "|",
                            ["payment_state", "in", ["not_paid", "partial"]],
                            ["state", "=", "cancel"],
                        ]],
                    )
                )
            except xmlrpc.client.Fault:
                incidents_count = 0
            if incidents_count > 0:
                continue

            try:
                partner_row = client.execute_kw(
                    "res.partner",
                    "read",
                    [[pid]],
                    {"fields": ["name"]},
                )
                partner_name = str(partner_row[0].get("name") or "") if partner_row else f"Partner {pid}"
            except xmlrpc.client.Fault:
                partner_name = f"Partner {pid}"

            items.append(
                {
                    "partner_id": pid,
                    "customer": partner_name,
                    "month_prev2_sales": round(prev2, 2),
                    "month_prev1_sales": round(prev1, 2),
                    "drop_pct": round(drop_pct, 2),
                    "has_active_contract": True,
                    "billing_incidents": incidents_count,
                }
            )

        items.sort(key=lambda x: -x["drop_pct"])
        return {
            "query": "customers_drop_with_active_contracts",
            "title": "Clientes con caída >20% (contrato activo, sin incidencias)",
            "count": len(items),
            "items": items,
            "meta": {
                "month_prev2": f"{first_day_prev2_month.isoformat()} a {last_day_prev2_month.isoformat()}",
                "month_prev1": f"{first_day_prev_month.isoformat()} a {last_day_prev_month.isoformat()}",
                "drop_threshold_pct": drop_threshold,
            },
            "hint": "No encontré clientes que cumplan la condición solicitada." if not items else "",
        }
    if query == "sales_quarter_compare":
        try:
            logistic_rate = float(p.get("logistic_rate") or 0.08)
        except (TypeError, ValueError):
            logistic_rate = 0.08
        if logistic_rate < 0:
            logistic_rate = 0.0
        if logistic_rate > 0.6:
            logistic_rate = 0.6

        today = date.today()
        q = ((today.month - 1) // 3) + 1
        q_start_month = ((q - 1) * 3) + 1
        current_start = date(today.year, q_start_month, 1)
        if q_start_month + 3 > 12:
            next_q_start = date(today.year + 1, 1, 1)
        else:
            next_q_start = date(today.year, q_start_month + 3, 1)
        current_end = next_q_start - timedelta(days=1)
        prev_start = date(today.year - 1, q_start_month, 1)
        if q_start_month + 3 > 12:
            prev_next_q_start = date(today.year, 1, 1)
        else:
            prev_next_q_start = date(today.year - 1, q_start_month + 3, 1)
        prev_end = prev_next_q_start - timedelta(days=1)

        sales_fields = [
            "id",
            "name",
            "date_order",
            "amount_total",
            "amount_untaxed",
            "partner_id",
            "team_id",
            "user_id",
            "state",
        ]
        try:
            rows = client.execute_kw(
                "sale.order",
                "search_read",
                [[["state", "in", ["sale", "done"]], ["date_order", ">=", prev_start.isoformat()], ["date_order", "<=", current_end.isoformat()]]],
                {"fields": sales_fields, "limit": 1000, "order": "date_order desc, id desc"},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex

        partner_ids: set[int] = set()
        for r in rows:
            pval = r.get("partner_id")
            if isinstance(pval, (list, tuple)) and pval:
                try:
                    partner_ids.add(int(pval[0]))
                except (TypeError, ValueError):
                    pass
        partner_region: dict[int, str] = {}
        if partner_ids:
            try:
                partner_rows = client.execute_kw(
                    "res.partner",
                    "search_read",
                    [[["id", "in", sorted(partner_ids)]]],
                    {"fields": ["id", "state_id", "country_id", "city"], "limit": len(partner_ids) + 10},
                )
            except xmlrpc.client.Fault:
                partner_rows = []
            for pr in partner_rows:
                rid = int(pr.get("id") or 0)
                state = pr.get("state_id")
                country = pr.get("country_id")
                city = str(pr.get("city") or "").strip()
                region = ""
                if isinstance(state, (list, tuple)) and len(state) > 1:
                    region = str(state[1] or "").strip()
                elif isinstance(country, (list, tuple)) and len(country) > 1:
                    region = str(country[1] or "").strip()
                elif city:
                    region = city
                partner_region[rid] = region or "Sin región"

        def period_of(order_date: str) -> str:
            dt = str(order_date or "")[:10]
            if not dt:
                return ""
            if current_start.isoformat() <= dt <= current_end.isoformat():
                return "current"
            if prev_start.isoformat() <= dt <= prev_end.isoformat():
                return "previous"
            return ""

        bucket: dict[tuple[str, str], dict[str, float]] = {}
        for r in rows:
            period = period_of(str(r.get("date_order") or ""))
            if not period:
                continue
            partner = r.get("partner_id")
            pid = int(partner[0]) if isinstance(partner, (list, tuple)) and partner else 0
            region = partner_region.get(pid, "Sin región")
            team = r.get("team_id")
            channel = team[1] if isinstance(team, (list, tuple)) and len(team) > 1 else "Canal no definido"
            key = (region, str(channel))
            if key not in bucket:
                bucket[key] = {"current_sales": 0.0, "previous_sales": 0.0, "current_net_margin": 0.0}
            amt_total = float(r.get("amount_total") or 0.0)
            amt_untaxed = float(r.get("amount_untaxed") or 0.0)
            est_log_cost = amt_total * logistic_rate
            est_margin_net = amt_untaxed - est_log_cost
            if period == "current":
                bucket[key]["current_sales"] += amt_total
                bucket[key]["current_net_margin"] += est_margin_net
            else:
                bucket[key]["previous_sales"] += amt_total

        items: list[dict[str, Any]] = []
        for (region, channel), data in bucket.items():
            cur = float(data["current_sales"])
            prev = float(data["previous_sales"])
            delta = cur - prev
            growth_pct = ((delta / prev) * 100.0) if prev else 0.0
            margin_net = float(data["current_net_margin"])
            margin_net_pct = ((margin_net / cur) * 100.0) if cur else 0.0
            items.append(
                {
                    "region": region,
                    "channel": channel,
                    "sales_current_quarter": round(cur, 2),
                    "sales_same_quarter_last_year": round(prev, 2),
                    "delta": round(delta, 2),
                    "growth_pct": round(growth_pct, 2),
                    "net_margin_estimated": round(margin_net, 2),
                    "net_margin_pct": round(margin_net_pct, 2),
                }
            )
        items.sort(key=lambda x: (x["region"], -x["sales_current_quarter"]))
        return {
            "query": "sales_quarter_compare",
            "title": "Ventas trimestrales comparadas (región/canal)",
            "count": len(items),
            "items": items,
            "meta": {
                "current_period": f"{current_start.isoformat()} a {current_end.isoformat()}",
                "previous_period": f"{prev_start.isoformat()} a {prev_end.isoformat()}",
                "logistic_rate": logistic_rate,
            },
            "hint": "No hay ventas confirmadas para los periodos comparados." if not items else "",
        }
    if query == "dashboard_overview":
        return _build_dashboard_overview(client)
    if query == "users_roles":
        try:
            users_fields_meta = client.execute_kw(
                "res.users",
                "fields_get",
                [],
                {"attributes": ["type", "relation"]},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        group_field_name = ""
        for candidate in ("groups_id", "group_ids", "groups"):
            meta = users_fields_meta.get(candidate) if isinstance(users_fields_meta, dict) else None
            if isinstance(meta, dict) and meta.get("type") in {"many2many", "many2one"}:
                group_field_name = candidate
                break
        user_read_fields = ["id", "name", "login", "active", "share"]
        if group_field_name:
            user_read_fields.append(group_field_name)
        try:
            user_rows = client.execute_kw(
                "res.users",
                "search_read",
                [[["active", "in", [True, False]]]],
                {
                    "fields": user_read_fields,
                    "limit": 200,
                    "order": "id desc",
                },
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        all_group_ids: set[int] = set()
        for r in user_rows:
            gids = r.get(group_field_name) if group_field_name else []
            if isinstance(gids, tuple):
                gids = [gids[0]]
            if isinstance(gids, list):
                for gid in gids:
                    try:
                        all_group_ids.add(int(gid))
                    except (TypeError, ValueError):
                        pass
        group_name_by_id: dict[int, str] = {}
        if all_group_ids:
            try:
                group_rows = client.execute_kw(
                    "res.groups",
                    "search_read",
                    [[["id", "in", sorted(all_group_ids)]]],
                    {"fields": ["id", "display_name"], "limit": len(all_group_ids) + 5},
                )
            except xmlrpc.client.Fault as ex:
                raise ValueError(_format_odoo_fault(ex)) from ex
            group_name_by_id = {
                int(g.get("id")): str(g.get("display_name") or "")
                for g in group_rows
                if g.get("id") is not None
            }
        items: list[dict[str, Any]] = []
        for r in user_rows:
            gids = r.get(group_field_name) if group_field_name else []
            if isinstance(gids, tuple):
                gids = [gids[0]]
            role_names: list[str] = []
            if isinstance(gids, list):
                for gid in gids:
                    try:
                        gid_i = int(gid)
                    except (TypeError, ValueError):
                        continue
                    nm = group_name_by_id.get(gid_i)
                    if nm:
                        role_names.append(nm)
            items.append(
                {
                    "id": int(r.get("id") or 0),
                    "name": str(r.get("name") or ""),
                    "login": str(r.get("login") or ""),
                    "active": bool(r.get("active")),
                    "internal_user": not bool(r.get("share")),
                    "roles": ", ".join(sorted(role_names)) if role_names else "—",
                }
            )
        return {
            "query": "users_roles",
            "title": "Usuarios y roles",
            "count": len(items),
            "items": items,
        }
    if query == "accounting_recent_actions":
        # "Acciones" recientes aproximadas por últimas facturas/documentos contables actualizados.
        latest_only = bool(p.get("latest_only"))
        domain: list[Any] = [["state", "in", ["draft", "posted", "cancel"]]]
        if latest_only:
            domain.append(["move_type", "in", ["out_invoice", "in_invoice"]])
        try:
            rows = client.execute_kw(
                "account.move",
                "search_read",
                [domain],
                {
                    "fields": [
                        "id",
                        "name",
                        "move_type",
                        "state",
                        "invoice_date",
                        "write_date",
                        "partner_id",
                        "amount_total",
                        "currency_id",
                        "payment_state",
                    ],
                    "limit": 1 if latest_only else 120,
                    "order": "write_date desc, id desc",
                },
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        items: list[dict[str, Any]] = []
        for r in rows:
            partner = r.get("partner_id")
            currency = r.get("currency_id")
            items.append(
                {
                    "id": int(r.get("id") or 0),
                    "name": str(r.get("name") or ""),
                    "partner": partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "",
                    "move_type": str(r.get("move_type") or ""),
                    "state": str(r.get("state") or ""),
                    "invoice_date": str(r.get("invoice_date") or ""),
                    "write_date": str(r.get("write_date") or ""),
                    "amount_total": float(r.get("amount_total") or 0.0),
                    "currency": currency[1] if isinstance(currency, (list, tuple)) and len(currency) > 1 else "",
                    "payment_state": str(r.get("payment_state") or ""),
                }
            )
        return {
            "query": "accounting_recent_actions",
            "title": "Última factura" if latest_only else "Últimas acciones en facturación",
            "count": len(items),
            "items": items,
        }
    if query == "accounting_missing_key_data":
        try:
            rows = client.execute_kw(
                "account.move",
                "search_read",
                [[["move_type", "in", ["out_invoice", "in_invoice"]], ["state", "!=", "cancel"]]],
                {
                    "fields": [
                        "id",
                        "name",
                        "move_type",
                        "state",
                        "partner_id",
                        "invoice_date",
                        "invoice_date_due",
                        "invoice_payment_term_id",
                        "currency_id",
                        "invoice_line_ids",
                        "amount_total",
                    ],
                    "limit": 300,
                    "order": "id desc",
                },
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        items: list[dict[str, Any]] = []
        for r in rows:
            partner = r.get("partner_id")
            currency = r.get("currency_id")
            missing: list[str] = []
            if not partner:
                missing.append("Cliente/Proveedor")
            if not r.get("invoice_date"):
                missing.append("Fecha factura")
            if not r.get("invoice_date_due"):
                missing.append("Vencimiento")
            if not r.get("currency_id"):
                missing.append("Moneda")
            lines = r.get("invoice_line_ids") or []
            if not isinstance(lines, list) or len(lines) == 0:
                missing.append("Líneas de factura")
            if float(r.get("amount_total") or 0.0) <= 0:
                missing.append("Total > 0")
            if missing:
                items.append(
                    {
                        "id": int(r.get("id") or 0),
                        "name": str(r.get("name") or ""),
                        "move_type": str(r.get("move_type") or ""),
                        "state": str(r.get("state") or ""),
                        "partner": partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "",
                        "invoice_date": str(r.get("invoice_date") or ""),
                        "invoice_date_due": str(r.get("invoice_date_due") or ""),
                        "currency": (
                            currency[1] if isinstance(currency, (list, tuple)) and len(currency) > 1 else ""
                        ),
                        "missing_fields": ", ".join(missing),
                    }
                )
        return {
            "query": "accounting_missing_key_data",
            "title": "Facturas con datos clave faltantes",
            "count": len(items),
            "items": items,
        }
    if query == "users_last_login":
        try:
            users_fields_meta = client.execute_kw(
                "res.users",
                "fields_get",
                [],
                {"attributes": ["type"]},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        last_login_field = ""
        for candidate in ("login_date", "last_login", "write_date"):
            if isinstance(users_fields_meta, dict) and candidate in users_fields_meta:
                last_login_field = candidate
                break
        read_fields = ["id", "name", "login", "active"]
        if last_login_field:
            read_fields.append(last_login_field)
        try:
            rows = client.execute_kw(
                "res.users",
                "search_read",
                [[["active", "in", [True, False]]]],
                {"fields": read_fields, "limit": 200, "order": "id desc"},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        items: list[dict[str, Any]] = []
        for r in rows:
            items.append(
                {
                    "id": int(r.get("id") or 0),
                    "name": str(r.get("name") or ""),
                    "login": str(r.get("login") or ""),
                    "active": bool(r.get("active")),
                    "last_login": str(r.get(last_login_field) or ""),
                }
            )
        return {
            "query": "users_last_login",
            "title": "Última conexión de usuarios",
            "count": len(items),
            "items": items,
        }
    if query == "dirty_data_overview":
        out: list[dict[str, Any]] = []
        try:
            partner_rows = client.execute_kw(
                "res.partner",
                "search_read",
                [[["active", "=", True], ["is_company", "=", True]]],
                {"fields": ["id", "name", "email", "vat", "phone"], "limit": 250, "order": "id desc"},
            )
            for p in partner_rows:
                issues: list[str] = []
                if not str(p.get("name") or "").strip():
                    issues.append("Nombre vacío")
                if not str(p.get("email") or "").strip():
                    issues.append("Email vacío")
                if not str(p.get("vat") or "").strip():
                    issues.append("RUT/VAT vacío")
                if issues:
                    out.append(
                        {
                            "entity": "Cliente/Empresa",
                            "record": str(p.get("name") or f"ID {p.get('id')}"),
                            "issues": ", ".join(issues),
                        }
                    )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        try:
            product_rows = client.execute_kw(
                "product.product",
                "search_read",
                [[["active", "=", True]]],
                {"fields": ["id", "name", "default_code", "list_price"], "limit": 250, "order": "id desc"},
            )
            for p in product_rows:
                issues = []
                if not str(p.get("default_code") or "").strip():
                    issues.append("Referencia interna vacía")
                if float(p.get("list_price") or 0.0) <= 0:
                    issues.append("Precio de venta <= 0")
                if issues:
                    out.append(
                        {
                            "entity": "Producto",
                            "record": str(p.get("name") or f"ID {p.get('id')}"),
                            "issues": ", ".join(issues),
                        }
                    )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        return {
            "query": "dirty_data_overview",
            "title": "Datos sucios detectados",
            "count": len(out),
            "items": out,
        }
    if query == "invoice_from_order_check":
        order_ref = str(p.get("order_ref") or "").strip()
        if not order_ref:
            raise ValueError("Indica el número de orden para revisar factura y duplicados.")
        try:
            rows = client.execute_kw(
                "account.move",
                "search_read",
                [[["move_type", "=", "out_invoice"], ["invoice_origin", "ilike", order_ref]]],
                {"fields": ["id", "name", "state", "partner_id", "invoice_origin", "amount_total"], "limit": 200, "order": "id desc"},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        sale_order: dict[str, Any] | None = None
        try:
            so_rows = client.execute_kw(
                "sale.order",
                "search_read",
                [[["name", "ilike", order_ref]]],
                {
                    "fields": ["id", "name", "partner_id", "amount_total", "currency_id", "state"],
                    "limit": 1,
                    "order": "id desc",
                },
            )
            sale_order = so_rows[0] if so_rows else None
        except xmlrpc.client.Fault:
            sale_order = None
        by_partner: dict[str, int] = {}
        items: list[dict[str, Any]] = []
        for r in rows:
            partner = r.get("partner_id")
            pname = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "(sin cliente)"
            key = f"{pname}|{r.get('invoice_origin')}"
            by_partner[key] = by_partner.get(key, 0) + 1
            items.append(
                {
                    "document": str(r.get("name") or ""),
                    "order_ref": str(r.get("invoice_origin") or ""),
                    "partner": pname,
                    "state": str(r.get("state") or ""),
                    "amount_total": float(r.get("amount_total") or 0.0),
                }
            )
        for it in items:
            key = f"{it['partner']}|{it['order_ref']}"
            it["duplicate_flag"] = "Posible duplicado" if by_partner.get(key, 0) > 1 else "OK"
        out: dict[str, Any] = {
            "query": query,
            "title": f"Factura(s) para orden {order_ref}",
            "count": len(items),
            "items": items,
        }
        if not items and sale_order:
            partner = sale_order.get("partner_id")
            partner_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else ""
            amount_total = float(sale_order.get("amount_total") or 0.0)
            out["suggested_action"] = {
                "operation": "create",
                "model": "account.move",
                "values": {
                    "move_kind": "out_invoice",
                    "partner_name": partner_name,
                    "invoice_line_name": f"Factura desde orden {sale_order.get('name') or order_ref}",
                    "invoice_line_price_unit": amount_total if amount_total > 0 else 0.0,
                    "invoice_line_qty": 1,
                    "ref": str(sale_order.get("name") or order_ref),
                },
                "summary": f"Crear factura para orden {sale_order.get('name') or order_ref}",
            }
            out["hint"] = "No existe factura para esta orden. Puedes crearla ahora con datos precargados."
        elif not items:
            out["hint"] = "No encontré la orden en ventas para precargar la factura."
        return out
    if query == "overdue_invoices":
        today = str(date.today())
        try:
            rows = client.execute_kw(
                "account.move",
                "search_read",
                [[["move_type", "=", "out_invoice"], ["state", "=", "posted"], ["payment_state", "!=", "paid"], ["invoice_date_due", "<", today]]],
                {"fields": ["name", "partner_id", "invoice_date_due", "amount_residual", "payment_state"], "limit": 300, "order": "invoice_date_due asc"},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        items = []
        for r in rows:
            partner = r.get("partner_id")
            items.append({
                "document": str(r.get("name") or ""),
                "partner": partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "",
                "due_date": str(r.get("invoice_date_due") or ""),
                "residual": float(r.get("amount_residual") or 0.0),
                "payment_state": str(r.get("payment_state") or ""),
            })
        return {"query": query, "title": "Facturas vencidas", "count": len(items), "items": items}
    if query == "low_stock_products":
        try:
            rows = client.execute_kw(
                "stock.warehouse.orderpoint",
                "search_read",
                [[]],
                {"fields": ["product_id", "product_min_qty", "product_max_qty", "qty_to_order"], "limit": 300, "order": "qty_to_order desc"},
            )
        except xmlrpc.client.Fault:
            rows = []
        items = []
        for r in rows:
            p2o = float(r.get("qty_to_order") or 0.0)
            if p2o <= 0:
                continue
            prod = r.get("product_id")
            items.append({
                "product": prod[1] if isinstance(prod, (list, tuple)) and len(prod) > 1 else "",
                "min_qty": float(r.get("product_min_qty") or 0.0),
                "max_qty": float(r.get("product_max_qty") or 0.0),
                "suggested_qty": p2o,
                "suggested_action": "Generar OC",
            })
        return {"query": query, "title": "Productos bajo mínimo", "count": len(items), "items": items}
    if query == "demand_forecast_purchase_hints":
        try:
            horizon = int(p.get("horizon_months") or 3)
        except (TypeError, ValueError):
            horizon = 3
        horizon = max(1, min(horizon, 12))
        today = date.today()
        start_90 = today - timedelta(days=90)
        start_45 = today - timedelta(days=45)
        start_90_s = start_90.isoformat()
        start_45_s = start_45.isoformat()
        today_end = f"{today.isoformat()} 23:59:59"

        def _read_group_lines(domain: list) -> list:
            try:
                return client.execute_kw(
                    "sale.order.line",
                    "read_group",
                    [domain, ["product_id", "product_uom_qty:sum"], ["product_id"]],
                    {"lazy": False, "limit": 800},
                )
            except xmlrpc.client.Fault:
                return []

        domain_first = [
            ["order_id.state", "in", ["sale", "done"]],
            ["order_id.date_order", ">=", start_90_s],
            ["order_id.date_order", "<", start_45_s],
        ]
        domain_second = [
            ["order_id.state", "in", ["sale", "done"]],
            ["order_id.date_order", ">=", start_45_s],
            ["order_id.date_order", "<=", today_end],
        ]
        domain_all = [
            ["order_id.state", "in", ["sale", "done"]],
            ["order_id.date_order", ">=", start_90_s],
            ["order_id.date_order", "<=", today_end],
        ]

        def _qty_by_product(rows: list) -> dict[int, tuple[float, str]]:
            out: dict[int, tuple[float, str]] = {}
            for r in rows or []:
                pid_ex = r.get("product_id")
                if not isinstance(pid_ex, (list, tuple)) or not pid_ex:
                    continue
                pid = int(pid_ex[0])
                label = str(pid_ex[1] or f"ID {pid}")
                out[pid] = (float(r.get("product_uom_qty") or 0.0), label)
            return out

        first_map = _qty_by_product(_read_group_lines(domain_first))
        second_map = _qty_by_product(_read_group_lines(domain_second))
        all_map = _qty_by_product(_read_group_lines(domain_all))
        pids_sorted = sorted(all_map.keys(), key=lambda pid: -all_map[pid][0])[:40]
        items: list[dict[str, Any]] = []
        for pid in pids_sorted:
            total_q, name = all_map[pid]
            q1 = first_map.get(pid, (0.0, ""))[0]
            q2 = second_map.get(pid, (0.0, ""))[0]
            base_month = total_q / 3.0
            trend = (q2 - q1) / (q1 + 1.0)
            trend = max(-0.5, min(0.5, trend))
            forecast_horizon = max(0.0, base_month * horizon * (1.0 + trend * 0.35))
            hint = "Revisar reglas de reposición y lead time de proveedor."
            try:
                op_rows = client.execute_kw(
                    "stock.warehouse.orderpoint",
                    "search_read",
                    [[["product_id", "=", pid]]],
                    {"fields": ["qty_to_order", "product_min_qty"], "limit": 1},
                )
                if op_rows:
                    qto = float(op_rows[0].get("qty_to_order") or 0.0)
                    pmin = float(op_rows[0].get("product_min_qty") or 0.0)
                    if qto > 0:
                        hint = f"Reposición sugerida por regla: ~{round(qto, 1)} uds (mín. {round(pmin, 1)})."
                    else:
                        hint = f"Sin cantidad a pedir ahora (mín. {round(pmin, 1)}); vigilar tendencia vs pronóstico."
            except xmlrpc.client.Fault:
                pass
            items.append(
                {
                    "product": name,
                    "sold_qty_90d": round(total_q, 2),
                    "avg_monthly": round(base_month, 2),
                    "trend_pct": round(trend * 100.0, 1),
                    "forecast_horizon_qty": round(forecast_horizon, 2),
                    "purchase_hint": hint,
                }
            )
        return {
            "query": query,
            "title": f"Proyección de demanda (~{horizon} meses) y compras",
            "count": len(items),
            "items": items,
            "meta": {
                "window_days": 90,
                "horizon_months": horizon,
                "method": "Promedio mensual últimos 90 días de ventas confirmadas, ajuste leve por tendencia 45d vs 45d previos; sugerencia de compra desde stock.warehouse.orderpoint si existe.",
            },
            "hint": ""
            if items
            else "No hay líneas de venta confirmadas en los últimos 90 días para proyectar.",
        }
    if query == "best_vendor_for_product":
        product_name = str(p.get("product_name") or "").strip()
        qty = float(p.get("qty") or 1.0)
        if not product_name:
            raise ValueError("Indica el producto para comparar proveedores.")
        try:
            product_rows = client.execute_kw(
                "product.product",
                "search_read",
                [[["name", "ilike", product_name]]],
                {"fields": ["id", "product_tmpl_id", "name"], "limit": 1},
            )
            if not product_rows:
                raise ValueError(f"No encontré el producto '{product_name}'.")
            tmpl = product_rows[0].get("product_tmpl_id")
            tmpl_id = int(tmpl[0]) if isinstance(tmpl, (list, tuple)) and tmpl else 0
            si_rows = client.execute_kw(
                "product.supplierinfo",
                "search_read",
                [[["product_tmpl_id", "=", tmpl_id]]],
                {"fields": ["partner_id", "price", "min_qty", "delay"], "limit": 100, "order": "price asc"},
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        items = []
        for r in si_rows:
            partner = r.get("partner_id")
            items.append({
                "vendor": partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "",
                "price": float(r.get("price") or 0.0),
                "min_qty": float(r.get("min_qty") or 0.0),
                "lead_days": float(r.get("delay") or 0.0),
            })
        items.sort(key=lambda x: x["price"])
        if items:
            items[0]["best_option"] = "Sí"
        return {"query": query, "title": f"Proveedor más barato para {product_name} (qty {qty})", "count": len(items), "items": items}
    if query == "payroll_preview":
        # Preview simple si no hay parámetros explícitos; evita fallar y pide completar datos.
        hours_extra = float(p.get("hours_extra") or 0.0)
        bonus = float(p.get("bonus") or 0.0)
        base_salary = float(p.get("base_salary") or 0.0)
        if base_salary <= 0:
            return {
                "query": query,
                "title": "Preview nómina (datos incompletos)",
                "count": 1,
                "items": [
                    {
                        "employee": str(p.get("employee_name") or ""),
                        "message": "Falta sueldo base para calcular. Indica sueldo base, horas extra y bono.",
                    }
                ],
            }
        hourly = base_salary / 180.0
        extra_pay = hours_extra * hourly * 1.5
        total = base_salary + extra_pay + bonus
        return {
            "query": query,
            "title": "Preview nómina",
            "count": 1,
            "items": [
                {
                    "employee": str(p.get("employee_name") or ""),
                    "base_salary": base_salary,
                    "hours_extra": hours_extra,
                    "bonus": bonus,
                    "extra_pay": round(extra_pay, 2),
                    "total": round(total, 2),
                }
            ],
        }
    if query == "latest_product":
        try:
            rows = client.execute_kw(
                "product.product",
                "search_read",
                [[["id", ">", 0]]],
                {
                    "fields": ["id", "name", "default_code", "create_date", "list_price", "standard_price", "active"],
                    "limit": 1,
                    "order": "id desc",
                },
            )
        except xmlrpc.client.Fault as ex:
            raise ValueError(_format_odoo_fault(ex)) from ex
        items: list[dict[str, Any]] = []
        for r in rows:
            items.append(
                {
                    "id": int(r.get("id") or 0),
                    "name": str(r.get("name") or ""),
                    "default_code": str(r.get("default_code") or ""),
                    "create_date": str(r.get("create_date") or ""),
                    "list_price": float(r.get("list_price") or 0.0),
                    "standard_price": float(r.get("standard_price") or 0.0),
                    "active": bool(r.get("active")),
                }
            )
        return {
            "query": "latest_product",
            "title": "Último producto ingresado",
            "count": len(items),
            "items": items,
            "hint": "No encontré productos en la base." if not items else "",
        }

    order_ref = str(p.get("order_ref") or "").strip()
    if order_ref:
        domain: list[Any] = [["name", "=", order_ref]]
    else:
        domain = [["state", "in", ["sale", "done"]], ["delivery_status", "!=", "full"]]
    rows = client.execute_kw(
        "sale.order",
        "search_read",
        [domain],
        {
            "fields": [
                "id",
                "name",
                "partner_id",
                "date_order",
                "amount_total",
                "currency_id",
                "state",
                "delivery_status",
                "invoice_status",
            ],
            "limit": 120,
            "order": "date_order desc, id desc",
        },
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        partner = r.get("partner_id")
        currency = r.get("currency_id")
        items.append(
            {
                "id": int(r.get("id") or 0),
                "name": str(r.get("name") or ""),
                "customer": partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "",
                "date_order": str(r.get("date_order") or ""),
                "amount_total": float(r.get("amount_total") or 0.0),
                "currency": currency[1] if isinstance(currency, (list, tuple)) and len(currency) > 1 else "",
                "state": str(r.get("state") or ""),
                "delivery_status": str(r.get("delivery_status") or ""),
                "invoice_status": str(r.get("invoice_status") or ""),
            }
        )
    return {
        "query": "delivery_orders",
        "title": f"Orden {order_ref}" if order_ref else "Órdenes por entregar",
        "count": len(items),
        "items": items,
        "hint": (f"No encontré la orden {order_ref} en pendientes de entrega." if order_ref and not items else ""),
    }


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


def _find_vendor_id_by_name(client: OdooXmlRpc, vendor_name: str) -> int:
    name = str(vendor_name or "").strip()
    if not name:
        raise ValueError("El nombre del proveedor es obligatorio.")
    rows = client.execute_kw(
        "res.partner",
        "search_read",
        [[["name", "ilike", name], ["supplier_rank", ">=", 0]]],
        {"fields": ["id", "name"], "limit": 5, "order": "id desc"},
    )
    if not rows:
        raise ValueError(f"VENDOR_NOT_FOUND::{name}")
    exact = [r for r in rows if str(r.get("name", "")).strip().lower() == name.lower()]
    picked = exact[0] if exact else rows[0]
    return int(picked["id"])


def _find_product_id_by_name(client: OdooXmlRpc, product_name: str) -> int:
    name = str(product_name or "").strip()
    if not name:
        raise ValueError("El nombre del producto es obligatorio.")
    rows = client.execute_kw(
        "product.product",
        "search_read",
        [[["name", "ilike", name], ["active", "=", True]]],
        {"fields": ["id", "name"], "limit": 5, "order": "id desc"},
    )
    if not rows:
        raise ValueError(f"PRODUCT_NOT_FOUND::{name}")
    exact = [r for r in rows if str(r.get("name", "")).strip().lower() == name.lower()]
    picked = exact[0] if exact else rows[0]
    return int(picked["id"])


def _find_picking_type_id(client: OdooXmlRpc, code: str) -> int:
    wanted = str(code or "").strip().lower() or "internal"
    if wanted not in {"incoming", "outgoing", "internal"}:
        wanted = "internal"
    rows = client.execute_kw(
        "stock.picking.type",
        "search_read",
        [[["code", "=", wanted]]],
        {"fields": ["id"], "limit": 1, "order": "id asc"},
    )
    if not rows:
        raise ValueError(
            f"No existe un tipo de operación de inventario para código '{wanted}'."
        )
    return int(rows[0]["id"])


def _build_invoice_create_vals(client: OdooXmlRpc, cleaned: dict[str, Any]) -> dict[str, Any]:
    partner_name = str(cleaned.get("partner_name") or "").strip()
    line_name = str(cleaned.get("invoice_line_name") or "").strip() or "Servicio"
    amount = cleaned.get("invoice_line_price_unit")
    if amount in (None, ""):
        raise ValueError("El monto de la factura es obligatorio.")
    qty = float(cleaned.get("invoice_line_qty") or 1.0)
    move_kind = str(cleaned.get("move_kind") or "out_invoice").strip().lower()
    if move_kind not in {"out_invoice", "in_invoice"}:
        move_kind = "out_invoice"
    partner_id = (
        _find_vendor_id_by_name(client, partner_name)
        if move_kind == "in_invoice"
        else _find_partner_id_by_name(client, partner_name)
    )
    vals: dict[str, Any] = {
        "move_type": move_kind,
        "partner_id": partner_id,
        "invoice_line_ids": [
            (
                0,
                0,
                {
                    "name": line_name,
                    "quantity": qty if qty > 0 else 1.0,
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


def _build_sale_order_create_vals(client: OdooXmlRpc, cleaned: dict[str, Any]) -> dict[str, Any]:
    partner_id = _find_partner_id_by_name(client, cleaned.get("partner_name"))
    qty = float(cleaned.get("order_line_qty") or 1.0)
    price = float(cleaned.get("order_line_price_unit") or 0.0)
    line_name = str(cleaned.get("order_line_name") or "").strip() or "Línea de venta"
    vals: dict[str, Any] = {
        "partner_id": partner_id,
        "order_line": [
            (
                0,
                0,
                {
                    "name": line_name,
                    "product_uom_qty": qty if qty > 0 else 1.0,
                    "price_unit": price,
                    "discount": float(cleaned.get("order_line_discount") or 0.0),
                },
            )
        ],
    }
    if cleaned.get("client_order_ref"):
        vals["client_order_ref"] = str(cleaned["client_order_ref"])
    if cleaned.get("note"):
        vals["note"] = str(cleaned["note"])
    return vals


def _build_purchase_order_create_vals(client: OdooXmlRpc, cleaned: dict[str, Any]) -> dict[str, Any]:
    partner_id = _find_vendor_id_by_name(client, cleaned.get("vendor_name"))
    qty = float(cleaned.get("order_line_qty") or 1.0)
    price = float(cleaned.get("order_line_price_unit") or 0.0)
    line_name = str(cleaned.get("order_line_name") or "").strip() or "Línea de compra"
    vals: dict[str, Any] = {
        "partner_id": partner_id,
        "order_line": [
            (
                0,
                0,
                {
                    "name": line_name,
                    "product_qty": qty if qty > 0 else 1.0,
                    "price_unit": price,
                    "date_planned": str(date.today()),
                },
            )
        ],
    }
    if cleaned.get("partner_ref"):
        vals["partner_ref"] = str(cleaned["partner_ref"])
    if cleaned.get("notes"):
        vals["notes"] = str(cleaned["notes"])
    return vals


def _build_stock_picking_create_vals(client: OdooXmlRpc, cleaned: dict[str, Any]) -> dict[str, Any]:
    product_id = _find_product_id_by_name(client, cleaned.get("product_name"))
    picking_type_id = _find_picking_type_id(client, cleaned.get("picking_type_code"))
    qty = float(cleaned.get("move_line_qty") or 1.0)
    line_name = str(cleaned.get("move_line_name") or "").strip() or "Movimiento de stock"
    vals: dict[str, Any] = {
        "picking_type_id": picking_type_id,
        "move_ids_without_package": [
            (
                0,
                0,
                {
                    "name": line_name,
                    "product_id": product_id,
                    "product_uom_qty": qty if qty > 0 else 1.0,
                },
            )
        ],
    }
    partner_name = str(cleaned.get("partner_name") or "").strip()
    if partner_name:
        vals["partner_id"] = _find_partner_id_by_name(client, partner_name)
    if cleaned.get("origin"):
        vals["origin"] = str(cleaned["origin"])
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


def build_missing_vendor_suggestion(vendor_name: str) -> dict[str, Any]:
    guessed_name = str(vendor_name or "").strip()
    return {
        "operation": "create",
        "model": "res.partner",
        "values": {
            "name": guessed_name,
            "is_company": True,
        },
        "summary": f"Crear proveedor {guessed_name}" if guessed_name else "Crear proveedor",
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
    if model == "sale.order":
        payload = _build_sale_order_create_vals(client, cleaned)
    if model == "purchase.order":
        payload = _build_purchase_order_create_vals(client, cleaned)
    if model == "stock.picking":
        payload = _build_stock_picking_create_vals(client, cleaned)
    if model == "product.product" and cleaned.get("name"):
        _preflight_duplicate_product_by_name(client, cleaned["name"])
    try:
        rec_id = client.execute_kw(model, "create", [payload])
    except xmlrpc.client.Fault as ex:
        raise ValueError(_format_odoo_fault(ex)) from ex
    return int(rec_id)


def _build_dashboard_overview(client: OdooXmlRpc) -> dict[str, Any]:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    today_iso = today.isoformat()

    def _safe_search_count(model: str, domain: list) -> int:
        try:
            return int(client.execute_kw(model, "search_count", [domain]))
        except (xmlrpc.client.Fault, ValueError, TypeError):
            return 0

    def _safe_read_group(model: str, domain: list, fields: list[str], groupby: list[str]) -> list:
        try:
            return client.execute_kw(model, "read_group", [domain, fields, groupby], {"lazy": False})
        except (xmlrpc.client.Fault, ValueError, TypeError):
            return []

    sales_month = _safe_read_group(
        "sale.order",
        [["state", "in", ["sale", "done"]], ["date_order", ">=", month_start]],
        ["amount_total:sum"],
        [],
    )
    sales_month_total = float(sales_month[0].get("amount_total") if sales_month else 0.0) if sales_month else 0.0

    invoices_month = _safe_read_group(
        "account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"], ["invoice_date", ">=", month_start]],
        ["amount_total:sum"],
        [],
    )
    invoiced_month_total = float(invoices_month[0].get("amount_total") if invoices_month else 0.0) if invoices_month else 0.0

    overdue_total = _safe_read_group(
        "account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"], ["payment_state", "in", ["not_paid", "partial"]], ["invoice_date_due", "<", today_iso]],
        ["amount_residual:sum"],
        [],
    )
    overdue_amount = float(overdue_total[0].get("amount_residual") if overdue_total else 0.0) if overdue_total else 0.0
    overdue_count = _safe_search_count(
        "account.move",
        [["move_type", "=", "out_invoice"], ["state", "=", "posted"], ["payment_state", "in", ["not_paid", "partial"]], ["invoice_date_due", "<", today_iso]],
    )

    purchases_month = _safe_read_group(
        "purchase.order",
        [["state", "in", ["purchase", "done"]], ["date_order", ">=", month_start]],
        ["amount_total:sum"],
        [],
    )
    purchases_month_total = float(purchases_month[0].get("amount_total") if purchases_month else 0.0) if purchases_month else 0.0

    open_quotations = _safe_search_count("sale.order", [["state", "in", ["draft", "sent"]]])
    confirmed_orders = _safe_search_count("sale.order", [["state", "in", ["sale", "done"]]])
    pickings_pending = _safe_search_count("stock.picking", [["state", "in", ["assigned", "confirmed", "waiting"]]])
    draft_invoices = _safe_search_count("account.move", [["move_type", "=", "out_invoice"], ["state", "=", "draft"]])

    customers_count = _safe_search_count("res.partner", [["customer_rank", ">", 0]])
    vendors_count = _safe_search_count("res.partner", [["supplier_rank", ">", 0]])
    products_count = _safe_search_count("product.product", [["active", "=", True]])

    top_customers_rows = []
    try:
        top_customers_rows = client.execute_kw(
            "account.move",
            "read_group",
            [
                [["move_type", "=", "out_invoice"], ["state", "=", "posted"], ["invoice_date", ">=", month_start]],
                ["partner_id", "amount_total:sum"],
                ["partner_id"],
            ],
            {"limit": 5, "orderby": "amount_total desc", "lazy": False},
        )
    except (xmlrpc.client.Fault, ValueError, TypeError):
        top_customers_rows = []
    top_customers = []
    for r in top_customers_rows or []:
        partner = r.get("partner_id")
        name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else ""
        top_customers.append({"name": name, "amount": float(r.get("amount_total") or 0.0)})

    sales_by_state_rows = _safe_read_group(
        "sale.order",
        [["date_order", ">=", month_start]],
        ["amount_total:sum"],
        ["state"],
    )
    sales_by_state = []
    for r in sales_by_state_rows or []:
        sales_by_state.append({"state": str(r.get("state") or ""), "amount": float(r.get("amount_total") or 0.0), "count": int(r.get("__count") or 0)})

    return {
        "query": "dashboard_overview",
        "title": "Dashboard general",
        "count": 1,
        "kpis": {
            "sales_month": sales_month_total,
            "invoiced_month": invoiced_month_total,
            "overdue_amount": overdue_amount,
            "overdue_count": overdue_count,
            "purchases_month": purchases_month_total,
            "open_quotations": open_quotations,
            "confirmed_orders": confirmed_orders,
            "pickings_pending": pickings_pending,
            "draft_invoices": draft_invoices,
            "customers": customers_count,
            "vendors": vendors_count,
            "products": products_count,
        },
        "top_customers": top_customers,
        "sales_by_state": sales_by_state,
        "items": [],
    }


def execute_email_action(app: AppSettings, target: str, params: dict[str, Any]) -> dict[str, Any]:
    target = str(target or "").strip().lower()
    if target not in _ALLOWED_EMAIL_TARGETS:
        raise ValueError(f"Tipo de correo no permitido: {target}")
    p = params or {}
    subject = str(p.get("subject") or "").strip() or "Mensaje desde Odoo"
    body_text = str(p.get("body") or "").strip()
    to_email = str(p.get("to_email") or "").strip()
    to_name = str(p.get("to_name") or "").strip()
    try:
        record_id = int(p.get("record_id") or 0)
    except (TypeError, ValueError):
        record_id = 0
    if not body_text:
        raise ValueError("El cuerpo del correo no puede estar vacío.")

    client = OdooXmlRpc(app)

    resolved_email = to_email
    resolved_name = to_name
    res_model = ""
    res_id = 0

    if target == "partner":
        if not resolved_email and to_name:
            try:
                pid = _find_partner_id_by_name(client, to_name)
            except ValueError:
                pid = 0
            if pid:
                rows = client.execute_kw(
                    "res.partner", "search_read", [[["id", "=", pid]]],
                    {"fields": ["id", "name", "email"], "limit": 1},
                )
                if rows:
                    resolved_email = resolved_email or str(rows[0].get("email") or "")
                    resolved_name = resolved_name or str(rows[0].get("name") or "")
                    res_model = "res.partner"
                    res_id = int(rows[0]["id"])
    elif target == "invoice" and record_id:
        rows = client.execute_kw(
            "account.move", "read", [[record_id]],
            {"fields": ["partner_id", "name"]},
        )
        if rows:
            partner = rows[0].get("partner_id")
            if isinstance(partner, (list, tuple)) and len(partner) > 1:
                resolved_name = resolved_name or str(partner[1])
                prows = client.execute_kw(
                    "res.partner", "read", [[int(partner[0])]], {"fields": ["email"]},
                )
                if prows and not resolved_email:
                    resolved_email = str(prows[0].get("email") or "")
            res_model = "account.move"
            res_id = record_id
    elif target == "sale_order" and record_id:
        rows = client.execute_kw(
            "sale.order", "read", [[record_id]], {"fields": ["partner_id", "name"]},
        )
        if rows:
            partner = rows[0].get("partner_id")
            if isinstance(partner, (list, tuple)) and len(partner) > 1:
                resolved_name = resolved_name or str(partner[1])
                prows = client.execute_kw(
                    "res.partner", "read", [[int(partner[0])]], {"fields": ["email"]},
                )
                if prows and not resolved_email:
                    resolved_email = str(prows[0].get("email") or "")
            res_model = "sale.order"
            res_id = record_id
    elif target == "purchase_order" and record_id:
        rows = client.execute_kw(
            "purchase.order", "read", [[record_id]], {"fields": ["partner_id", "name"]},
        )
        if rows:
            partner = rows[0].get("partner_id")
            if isinstance(partner, (list, tuple)) and len(partner) > 1:
                resolved_name = resolved_name or str(partner[1])
                prows = client.execute_kw(
                    "res.partner", "read", [[int(partner[0])]], {"fields": ["email"]},
                )
                if prows and not resolved_email:
                    resolved_email = str(prows[0].get("email") or "")
            res_model = "purchase.order"
            res_id = record_id

    if not resolved_email:
        raise ValueError(
            "No tengo una dirección de correo para enviar. Indícame el email del destinatario."
        )

    safe_body = body_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body_html = "<p>" + safe_body.replace("\n", "<br/>") + "</p>"
    if resolved_name:
        email_to = f'"{resolved_name}" <{resolved_email}>'
    else:
        email_to = resolved_email

    mail_vals: dict[str, Any] = {
        "subject": subject,
        "body_html": body_html,
        "email_to": email_to,
        "auto_delete": False,
    }
    if res_model and res_id:
        mail_vals["model"] = res_model
        mail_vals["res_id"] = res_id

    try:
        mail_id = int(client.execute_kw("mail.mail", "create", [mail_vals]))
        client.execute_kw("mail.mail", "send", [[mail_id]])
    except xmlrpc.client.Fault as ex:
        raise ValueError(_format_odoo_fault(ex)) from ex

    return {
        "ok": True,
        "mail_id": mail_id,
        "to": email_to,
        "subject": subject,
        "linked_model": res_model,
        "linked_id": res_id,
    }


def execute_workflow(app: AppSettings, name: str, params: dict[str, Any]) -> dict[str, Any]:
    name = str(name or "").strip().lower()
    if name not in _ALLOWED_WORKFLOWS:
        raise ValueError(f"Workflow no permitido: {name}")
    p = params or {}
    if name != "lead_to_payment":
        raise ValueError(f"Workflow no implementado: {name}")

    client = OdooXmlRpc(app)
    partner_name = str(
        p.get("partner_name")
        or p.get("customer_name")
        or p.get("partner")
        or ""
    ).strip()
    partner_name = re.sub(r"^\s*cliente\s+", "", partner_name, flags=re.IGNORECASE).strip()
    product_name = str(p.get("product_name") or "").strip()
    amount = 0.0
    for amount_key in ("amount", "total", "amount_total", "price_unit", "price"):
        try:
            candidate = float(p.get(amount_key) or 0.0)
        except (TypeError, ValueError):
            candidate = 0.0
        if candidate > 0:
            amount = candidate
            break
    try:
        qty = float(p.get("qty") or 1.0)
    except (TypeError, ValueError):
        qty = 1.0
    if not partner_name:
        raise ValueError("El workflow requiere el nombre del cliente.")
    steps: list[dict[str, Any]] = []
    if qty <= 0:
        qty = 1.0
        steps.append(
            {
                "step": "Validación",
                "ok": True,
                "detail": "Cantidad no válida o vacía; se ajustó automáticamente a 1.",
            }
        )
    if amount <= 0:
        amount = 1.0
        steps.append(
            {
                "step": "Validación",
                "ok": True,
                "detail": "Monto no informado o inválido; se usó monto mínimo 1. Ajusta la orden/factura luego si aplica.",
            }
        )

    try:
        partner_id = _find_partner_id_by_name(client, partner_name)
        steps.append({"step": "Cliente", "ok": True, "detail": f"Cliente existente (id {partner_id})", "ref": partner_id})
    except ValueError:
        try:
            partner_id = int(client.execute_kw("res.partner", "create", [{"name": partner_name, "is_company": True, "customer_rank": 1}]))
            steps.append({"step": "Cliente", "ok": True, "detail": f"Cliente creado (id {partner_id})", "ref": partner_id})
        except xmlrpc.client.Fault as ex:
            steps.append({"step": "Cliente", "ok": False, "detail": _format_odoo_fault(ex)})
            return {"workflow": name, "ok": False, "steps": steps}

    product_id = 0
    if product_name:
        try:
            product_id = _find_product_id_by_name(client, product_name)
        except ValueError:
            try:
                tmpl_id = int(client.execute_kw("product.product", "create", [{"name": product_name, "list_price": amount, "type": "service"}]))
                product_id = tmpl_id
                steps.append({"step": "Producto", "ok": True, "detail": f"Producto creado «{product_name}» (id {product_id})", "ref": product_id})
            except xmlrpc.client.Fault as ex:
                steps.append({"step": "Producto", "ok": False, "detail": _format_odoo_fault(ex)})
                return {"workflow": name, "ok": False, "steps": steps}
        else:
            steps.append({"step": "Producto", "ok": True, "detail": f"Producto existente (id {product_id})", "ref": product_id})
    if not product_id:
        # Odoo 19 puede impedir confirmar la venta si la línea no tiene producto real.
        # Usamos/creamos un producto de servicio técnico para que el flujo siempre avance.
        fallback_name = "Servicio Workflow IA"
        try:
            product_id = _find_product_id_by_name(client, fallback_name)
            steps.append(
                {
                    "step": "Producto",
                    "ok": True,
                    "detail": f"Producto fallback existente «{fallback_name}» (id {product_id})",
                    "ref": product_id,
                }
            )
        except ValueError:
            try:
                product_id = int(
                    client.execute_kw(
                        "product.product",
                        "create",
                        [{"name": fallback_name, "list_price": amount, "type": "service"}],
                    )
                )
                steps.append(
                    {
                        "step": "Producto",
                        "ok": True,
                        "detail": f"Producto fallback creado «{fallback_name}» (id {product_id})",
                        "ref": product_id,
                    }
                )
            except xmlrpc.client.Fault as ex:
                steps.append({"step": "Producto", "ok": False, "detail": _format_odoo_fault(ex)})
                return {"workflow": name, "ok": False, "steps": steps}

    line_vals: dict[str, Any] = {
        "name": product_name or "Servicio",
        "product_uom_qty": qty,
        "price_unit": amount / max(qty, 1.0),
    }
    line_vals["product_id"] = product_id
    try:
        so_id = int(client.execute_kw(
            "sale.order", "create",
            [{"partner_id": partner_id, "order_line": [(0, 0, line_vals)]}],
        ))
        steps.append({"step": "Cotización", "ok": True, "detail": f"sale.order id {so_id}", "ref": so_id})
    except xmlrpc.client.Fault as ex:
        steps.append({"step": "Cotización", "ok": False, "detail": _format_odoo_fault(ex)})
        return {"workflow": name, "ok": False, "steps": steps}

    try:
        client.execute_kw("sale.order", "action_confirm", [[so_id]])
        steps.append({"step": "Confirmar venta", "ok": True, "detail": f"Orden de venta confirmada (id {so_id})", "ref": so_id})
    except xmlrpc.client.Fault as ex:
        steps.append({"step": "Confirmar venta", "ok": False, "detail": _format_odoo_fault(ex)})
        return {"workflow": name, "ok": False, "steps": steps}

    invoice_id = 0
    try:
        so_rows = client.execute_kw(
            "sale.order",
            "read",
            [[so_id]],
            {"fields": ["name"]},
        )
        so_name = str(so_rows[0].get("name") or "") if so_rows else ""
        # 1) Intento de factura ya creada por automatizaciones del servidor.
        if so_name:
            inv_rows = client.execute_kw(
                "account.move",
                "search_read",
                [[["invoice_origin", "=", so_name]]],
                {"fields": ["id"], "limit": 1, "order": "id desc"},
            )
            if inv_rows:
                invoice_id = int(inv_rows[0]["id"])
        # 2) Fallback compatible por XML-RPC/Odoo 19: crear account.move manual.
        if not invoice_id:
            line_price = amount / max(qty, 1.0)
            inv_payload: dict[str, Any] = {
                "move_type": "out_invoice",
                "partner_id": partner_id,
                "invoice_origin": so_name or f"SO#{so_id}",
                "ref": so_name or f"SO#{so_id}",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": product_name or "Servicio",
                            "quantity": qty,
                            "price_unit": line_price,
                        },
                    )
                ],
            }
            invoice_id = int(client.execute_kw("account.move", "create", [inv_payload]))
        steps.append({"step": "Factura", "ok": True, "detail": f"Factura generada (id {invoice_id})", "ref": invoice_id})
    except xmlrpc.client.Fault as ex:
        steps.append({"step": "Factura", "ok": False, "detail": _format_odoo_fault(ex)})
        return {"workflow": name, "ok": False, "steps": steps}

    if invoice_id:
        try:
            client.execute_kw("account.move", "action_post", [[invoice_id]])
            steps.append({"step": "Validar factura", "ok": True, "detail": f"Factura validada (id {invoice_id})", "ref": invoice_id})
        except xmlrpc.client.Fault as ex:
            steps.append({"step": "Validar factura", "ok": False, "detail": _format_odoo_fault(ex)})

    return {"workflow": name, "ok": all(s.get("ok") for s in steps), "steps": steps, "sale_order_id": so_id, "invoice_id": invoice_id, "partner_id": partner_id}
