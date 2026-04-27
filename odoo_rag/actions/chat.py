from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from odoo_rag.actions.intents import heuristic_reply
from odoo_rag.actions.rag_context import retrieve_context_chunks
from odoo_rag.actions.sanitize import sanitize_draft_action
from odoo_rag.config import Settings as AppSettings
from odoo_rag.product_setup import (
    DEFAULT_PRODUCT_SETUP_REPLY,
    looks_like_leaked_structure_json,
)


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

    hit = heuristic_reply(app, user_message, top_k=top_k)
    if hit is not None:
        return hit

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

