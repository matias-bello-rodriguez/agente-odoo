"""Enlaces al cliente web de Odoo (formulario de registro por id + model)."""

from __future__ import annotations

from typing import Any


def odoo_form_url(base_url: str, model: str, record_id: int) -> str:
    """Abre el formulario estándar del registro en el backend Odoo instalado."""
    base = base_url.rstrip("/")
    mid = int(record_id)
    # Hash routing del web client (Odoo Community / Enterprise)
    return f"{base}/web#id={mid}&model={model}&view_type=form"


def link_record(base_url: str, model: str, record_id: int, label: str) -> dict[str, Any]:
    return {
        "label": label,
        "model": model,
        "id": int(record_id),
        "url": odoo_form_url(base_url, model, record_id),
    }


def odoo_links_after_create(base_url: str, model: str, record_id: int) -> list[dict[str, Any]]:
    """Un enlace tras execute_kw create."""
    titles = {
        "res.partner": "Contacto en Odoo",
        "product.product": "Producto en Odoo",
        "account.move": "Factura en Odoo",
    }
    label = titles.get(model, f"Registro en Odoo ({model})")
    return [link_record(base_url, model, record_id, label)]


def odoo_links_after_product_setup(
    base_url: str,
    *,
    product_tmpl_id: int,
    product_product_id: int,
    orderpoint_id: int | None = None,
) -> list[dict[str, Any]]:
    """Enlaces a plantilla, variante y opcionalmente regla de reorden."""
    out: list[dict[str, Any]] = [
        link_record(
            base_url,
            "product.template",
            product_tmpl_id,
            "Plantilla del producto",
        ),
        link_record(
            base_url,
            "product.product",
            product_product_id,
            "Variante / ficha producto",
        ),
    ]
    if orderpoint_id:
        out.append(
            link_record(
                base_url,
                "stock.warehouse.orderpoint",
                orderpoint_id,
                "Regla de reorden (stock)",
            )
        )
    return out
