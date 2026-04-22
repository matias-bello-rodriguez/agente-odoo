from __future__ import annotations

import json
from typing import Any

from llama_index.core import Document

from odoo_rag.odoo_client import OdooXmlRpc


def _format_m2o(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[1])
    return str(value)


def _format_m2m(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list) and value and isinstance(value[0], (list, tuple)):
        return ", ".join(str(v[1]) if isinstance(v, (list, tuple)) and len(v) > 1 else str(v) for v in value)
    return str(value)


def _row_to_text(model: str, row: dict[str, Any]) -> str:
    rid = row.get("id")
    lines = [f"Modelo Odoo: {model}", f"ID: {rid}"]
    skip = {"id"}
    for key in sorted(k for k in row if k not in skip):
        val = row[key]
        if isinstance(val, str) and not val.strip():
            continue
        if val in (False, None, []):
            continue
        if key.endswith("_id") and isinstance(val, (list, tuple)):
            lines.append(f"{key}: {_format_m2o(val)}")
        elif key.endswith("_ids"):
            lines.append(f"{key}: {_format_m2m(val)}")
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines)


def _search_read_safe(
    client: OdooXmlRpc,
    model: str,
    domain: list[Any],
    fields: list[str],
    *,
    limit: int,
    order: str | None = None,
) -> list[dict[str, Any]]:
    try:
        return client.search_read(model, domain, fields, limit=limit, order=order)
    except Exception as exc:  # noqa: BLE001 - Fault y errores de red
        raise RuntimeError(f"No se pudo leer {model} con campos {fields}: {exc}") from exc


def _fields_existing_on_model(client: OdooXmlRpc, model: str, requested: list[str]) -> list[str]:
    """Evita Invalid field … cuando el esquema cambia entre versiones (p. ej. Odoo 19 sin `mobile`)."""
    meta = client.execute_kw(model, "fields_get", [], {"attributes": ["type"]})
    valid = set(meta.keys())
    kept = [f for f in requested if f in valid]
    dropped = [f for f in requested if f not in valid]
    if dropped:
        print(f"Aviso ({model}): esta BD no tiene los campos {dropped}; se omiten.")
    return kept


def build_documents(client: OdooXmlRpc, *, limit: int) -> list[Document]:
    """Genera documentos de texto a partir de modelos típicos de Odoo Community."""

    chunks: list[Document] = []

    partner_specs: list[tuple[str, list[str], str]] = [
        (
            "res.partner",
            [
                "name",
                "display_name",
                "email",
                "phone",
                "street",
                "city",
                "zip",
                "country_id",
                "vat",
                "is_company",
                "customer_rank",
                "supplier_rank",
                "comment",
            ],
            "id desc",
        ),
    ]

    product_specs: list[tuple[str, list[str], str]] = [
        (
            "product.product",
            [
                "name",
                "display_name",
                "default_code",
                "list_price",
                "type",
                "active",
            ],
            "id desc",
        ),
    ]

    sale_specs: list[tuple[str, list[str], str]] = [
        (
            "sale.order",
            [
                "name",
                "partner_id",
                "date_order",
                "amount_total",
                "currency_id",
                "state",
                "user_id",
                "invoice_status",
                "client_order_ref",
                "note",
            ],
            "id desc",
        ),
    ]

    for model, fields, order in partner_specs + product_specs + sale_specs:
        fields = _fields_existing_on_model(client, model, fields)
        if not fields:
            print(f"Aviso: no quedó ningún campo válido para {model}, se omite.")
            continue
        try:
            rows = _search_read_safe(client, model, [], fields, limit=limit, order=order)
        except RuntimeError as err:
            # Módulos no instalados o campos distintos entre versiones
            print(str(err))
            continue
        for row in rows:
            text = _row_to_text(model, row)
            meta = {
                "odoo_model": model,
                "odoo_id": row.get("id"),
                "source": "odoo_xmlrpc",
            }
            chunks.append(Document(text=text, metadata=meta, id_=f"{model}:{row.get('id')}"))

    if not chunks:
        raise RuntimeError(
            "No se generó ningún documento. Verifica que Odoo esté arriba, las credenciales, "
            "y que existan al menos contactos o productos."
        )

    return chunks


def preview_first_rows(client: OdooXmlRpc, *, limit: int, preview: int = 3) -> str:
    docs = build_documents(client, limit=limit)
    head = docs[:preview]
    return json.dumps([d.metadata | {"text": d.text[:400]} for d in head], ensure_ascii=False, indent=2)
