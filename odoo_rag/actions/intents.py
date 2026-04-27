from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from odoo_rag.config import Settings as AppSettings
from odoo_rag.product_setup import (
    extract_product_setup_draft,
    looks_like_full_product_setup,
)


def heuristic_reply(app: AppSettings, user_message: str, *, top_k: int) -> dict[str, Any] | None:
    # Copia literal de la sección de heurísticas de structured_chat_reply() en actions.py.
    # No ejecuta RAG ni LLM: si no hay match devuelve None.
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
    _ = top_k
    return None

