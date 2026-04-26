"""
Script para POBLAR la base de datos de Odoo con datos de prueba en español.

Crea (de forma idempotente):
  - Clientes y proveedores (res.partner)
  - Productos consumibles y servicios (product.product)
  - Reglas de reorden / stock mínimo (stock.warehouse.orderpoint)
  - Órdenes de venta (sale.order) en distintos estados (borrador y confirmadas)
  - Órdenes de compra (purchase.order) en borrador y confirmadas
  - Transferencias de inventario (stock.picking) entrada/salida
  - Facturas de cliente y proveedor (account.move) en borrador y publicadas

Diseñado para ser SEGURO de re-ejecutar: si un registro ya existe (mismo nombre/ref),
no se vuelve a crear.

Uso (PowerShell, desde la raíz del proyecto «agente»):
    .venv/Scripts/python.exe scripts/poblar_bd.py

Para forzar una "tanda" extra de demo (sufijo aleatorio):
    .venv/Scripts/python.exe scripts/poblar_bd.py --extra

Las credenciales se leen del .env del proyecto (vía odoo_rag.config).
"""

from __future__ import annotations

import argparse
import random
import sys
import xmlrpc.client
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Permitir ejecutar como script standalone añadiendo la raíz del proyecto al sys.path.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from odoo_rag.config import load_settings
from odoo_rag.odoo_client import OdooXmlRpc


# ============================================================================
# Helpers de impresión
# ============================================================================

def header(txt: str) -> None:
    print()
    print("=" * 78)
    print(f"  {txt}")
    print("=" * 78)


def info(txt: str) -> None:
    print(f"  • {txt}")


def warn(txt: str) -> None:
    print(f"  ! {txt}")


# ============================================================================
# Helpers Odoo (idempotentes)
# ============================================================================

def find_or_create(
    client: OdooXmlRpc,
    model: str,
    domain: list[Any],
    create_vals: dict[str, Any],
    *,
    label: str = "",
) -> int:
    """Busca por dominio; si no existe lo crea. Devuelve el id."""
    rows = client.execute_kw(
        model, "search_read", [domain],
        {"fields": ["id"], "limit": 1, "order": "id desc"},
    )
    if rows:
        return int(rows[0]["id"])
    new_id = int(client.execute_kw(model, "create", [create_vals]))
    if label:
        info(f"Creado {label} (id {new_id})")
    return new_id


def safe_get(client: OdooXmlRpc, model: str, domain: list[Any], fields: list[str]) -> dict[str, Any]:
    rows = client.execute_kw(
        model, "search_read", [domain],
        {"fields": fields, "limit": 1, "order": "id desc"},
    )
    return rows[0] if rows else {}


def field_exists(client: OdooXmlRpc, model: str, field: str) -> bool:
    try:
        meta = client.execute_kw(model, "fields_get", [], {"attributes": ["type"]})
        return isinstance(meta, dict) and field in meta
    except xmlrpc.client.Fault:
        return False


def model_exists(client: OdooXmlRpc, model: str) -> bool:
    """Verifica si el modelo existe en esta BD (módulo instalado)."""
    try:
        client.execute_kw(model, "fields_get", [], {"attributes": ["type"]})
        return True
    except xmlrpc.client.Fault:
        return False


def fault_brief(ex: xmlrpc.client.Fault, max_len: int = 220) -> str:
    """Extrae una línea útil del fault XML-RPC de Odoo."""
    fs = str(getattr(ex, "faultString", "") or "")
    lines = [ln.strip() for ln in fs.splitlines() if ln.strip()]
    if not lines:
        return str(ex)[:max_len]
    for ln in lines:
        low = ln.lower()
        if low.startswith("traceback") or low.startswith("file "):
            continue
        if "usererror" in low or "validationerror" in low or "accesserror" in low:
            return ln[:max_len]
    for ln in reversed(lines):
        low = ln.lower()
        if low.startswith("traceback") or low.startswith("file "):
            continue
        return ln[:max_len]
    return lines[0][:max_len]


# ============================================================================
# Datos demo
# ============================================================================

CLIENTES = [
    {"name": "ACME Industrial S.A.", "email": "ventas@acme-industrial.cl", "city": "Santiago", "phone": "+56 2 2345 6789"},
    {"name": "SODIMAC Constructor", "email": "compras@sodimacconstructor.cl", "city": "Valparaíso", "phone": "+56 32 245 6700"},
    {"name": "Constructora Andes Ltda.", "email": "contacto@andesconstructora.cl", "city": "Concepción", "phone": "+56 41 233 4455"},
    {"name": "Distribuidora Norte SpA", "email": "info@distrinorte.cl", "city": "Antofagasta", "phone": "+56 55 233 1100"},
    {"name": "Comercial Patagonia EIRL", "email": "patagonia@correo.cl", "city": "Puerto Montt", "phone": "+56 65 226 7788"},
    {"name": "Tienda La Esquina", "email": "laesquina@correo.cl", "city": "Santiago", "phone": "+56 2 2987 6543"},
]

PROVEEDORES = [
    {"name": "Maderas del Sur Ltda.", "email": "ventas@maderasdelsur.cl", "city": "Valdivia"},
    {"name": "Aceros & Perfiles SpA", "email": "comercial@acerosperfiles.cl", "city": "Santiago"},
    {"name": "Importadora Asia Trade", "email": "ventas@asiatrade.cl", "city": "Iquique"},
    {"name": "Logística Express Chile", "email": "operaciones@logexpress.cl", "city": "Santiago"},
]

PRODUCTOS = [
    {"name": "Silla ergonómica oficina", "default_code": "SILLA-ERG-001", "list_price": 89990, "standard_price": 52000, "type": "consu"},
    {"name": "Escritorio melamina 140cm", "default_code": "ESC-MEL-140", "list_price": 129990, "standard_price": 78000, "type": "consu"},
    {"name": "Tornillo autoperforante 1\"", "default_code": "TOR-AUT-1", "list_price": 250, "standard_price": 90, "type": "consu"},
    {"name": "Perfil metálico 6m", "default_code": "PER-MET-6M", "list_price": 4500, "standard_price": 2100, "type": "consu"},
    {"name": "Pintura látex blanca 1gal", "default_code": "PIN-LAT-1G", "list_price": 18990, "standard_price": 9800, "type": "consu"},
    {"name": "Cable eléctrico 2.5mm rollo 100m", "default_code": "CAB-25-100", "list_price": 35990, "standard_price": 19500, "type": "consu"},
    {"name": "Servicio de instalación", "default_code": "SRV-INST", "list_price": 25000, "standard_price": 0, "type": "service"},
    {"name": "Servicio de mantención", "default_code": "SRV-MANT", "list_price": 18000, "standard_price": 0, "type": "service"},
]

REORDEN_RULES = [
    {"code": "SILLA-ERG-001", "min": 5, "max": 20},
    {"code": "ESC-MEL-140", "min": 3, "max": 12},
    {"code": "TOR-AUT-1", "min": 100, "max": 500},
    {"code": "PER-MET-6M", "min": 10, "max": 60},
    {"code": "PIN-LAT-1G", "min": 8, "max": 30},
]


# ============================================================================
# Funciones de carga
# ============================================================================

def crear_clientes(client: OdooXmlRpc, sufijo: str = "") -> list[int]:
    header("CLIENTES")
    ids: list[int] = []
    for c in CLIENTES:
        nombre = f"{c['name']}{sufijo}"
        vals = {
            "name": nombre,
            "is_company": True,
            "customer_rank": 1,
            "email": c["email"],
            "city": c["city"],
            "phone": c.get("phone", ""),
        }
        pid = find_or_create(
            client, "res.partner",
            [["name", "=", nombre]],
            vals,
            label=f"cliente «{nombre}»",
        )
        ids.append(pid)
    info(f"Total clientes asegurados: {len(ids)}")
    return ids


def crear_proveedores(client: OdooXmlRpc, sufijo: str = "") -> list[int]:
    header("PROVEEDORES")
    ids: list[int] = []
    for p in PROVEEDORES:
        nombre = f"{p['name']}{sufijo}"
        vals = {
            "name": nombre,
            "is_company": True,
            "supplier_rank": 1,
            "email": p["email"],
            "city": p["city"],
        }
        pid = find_or_create(
            client, "res.partner",
            [["name", "=", nombre]],
            vals,
            label=f"proveedor «{nombre}»",
        )
        ids.append(pid)
    info(f"Total proveedores asegurados: {len(ids)}")
    return ids


def crear_productos(client: OdooXmlRpc, sufijo: str = "") -> dict[str, int]:
    header("PRODUCTOS")
    out: dict[str, int] = {}
    for p in PRODUCTOS:
        nombre = f"{p['name']}{sufijo}"
        codigo = f"{p['default_code']}{sufijo}".replace(" ", "")[:64]
        vals = {
            "name": nombre,
            "default_code": codigo,
            "list_price": p["list_price"],
            "standard_price": p["standard_price"],
            "type": p["type"],
            "sale_ok": True,
            "purchase_ok": True,
        }
        pid = find_or_create(
            client, "product.product",
            [["default_code", "=", codigo]],
            vals,
            label=f"producto «{nombre}»",
        )
        out[codigo] = pid
    info(f"Total productos asegurados: {len(out)}")
    return out


def crear_reglas_reorden(client: OdooXmlRpc, productos_por_codigo: dict[str, int]) -> int:
    header("REGLAS DE REORDEN (stock mínimo / máximo)")
    if not field_exists(client, "stock.warehouse.orderpoint", "product_min_qty"):
        warn("Modelo stock.warehouse.orderpoint no disponible; se omiten reglas.")
        return 0
    creadas = 0
    warehouse_id = 0
    rows = client.execute_kw(
        "stock.warehouse", "search_read", [[["company_id", "!=", False]]],
        {"fields": ["id"], "limit": 1, "order": "id asc"},
    )
    if rows:
        warehouse_id = int(rows[0]["id"])
    location_id = 0
    if warehouse_id:
        wh = client.execute_kw("stock.warehouse", "read", [[warehouse_id]], {"fields": ["lot_stock_id"]})
        if wh and wh[0].get("lot_stock_id"):
            location_id = int(wh[0]["lot_stock_id"][0])
    for r in REORDEN_RULES:
        prod_id = None
        for codigo, pid in productos_por_codigo.items():
            if codigo.startswith(r["code"]):
                prod_id = pid
                break
        if not prod_id:
            continue
        existing = client.execute_kw(
            "stock.warehouse.orderpoint", "search_read",
            [[["product_id", "=", prod_id]]],
            {"fields": ["id"], "limit": 1},
        )
        if existing:
            continue
        vals: dict[str, Any] = {
            "product_id": prod_id,
            "product_min_qty": r["min"],
            "product_max_qty": r["max"],
        }
        if warehouse_id:
            vals["warehouse_id"] = warehouse_id
        if location_id:
            vals["location_id"] = location_id
        try:
            new_id = int(client.execute_kw("stock.warehouse.orderpoint", "create", [vals]))
            info(f"Regla reorden producto id {prod_id}: min {r['min']} / max {r['max']} (id {new_id})")
            creadas += 1
        except xmlrpc.client.Fault as ex:
            warn(f"No se pudo crear regla para producto id {prod_id}: {fault_brief(ex, 180)}")
    info(f"Reglas de reorden creadas: {creadas}")
    return creadas


def _picking_type_id(client: OdooXmlRpc, code: str) -> int:
    try:
        rows = client.execute_kw(
            "stock.picking.type", "search_read",
            [[["code", "=", code]]],
            {"fields": ["id"], "limit": 1, "order": "sequence asc, id asc"},
        )
        return int(rows[0]["id"]) if rows else 0
    except xmlrpc.client.Fault:
        return 0


def crear_ordenes_venta(client: OdooXmlRpc, clientes: list[int], productos: dict[str, int]) -> list[int]:
    header("ÓRDENES DE VENTA (sale.order)")
    if not clientes or not productos:
        warn("Faltan clientes o productos.")
        return []
    creadas: list[int] = []
    codigos_fisicos = [c for c in productos if not c.startswith("SRV-")]
    for i in range(5):
        cliente = random.choice(clientes)
        items = random.sample(codigos_fisicos, k=min(2, len(codigos_fisicos)))
        order_lines = []
        for codigo in items:
            pid = productos[codigo]
            order_lines.append((0, 0, {
                "product_id": pid,
                "product_uom_qty": random.randint(1, 5),
            }))
        vals = {
            "partner_id": cliente,
            "client_order_ref": f"DEMO-VENTA-{i+1:03d}",
            "order_line": order_lines,
        }
        try:
            so_id = int(client.execute_kw("sale.order", "create", [vals]))
            creadas.append(so_id)
            info(f"Cotización creada (id {so_id}, cliente id {cliente}, {len(order_lines)} líneas)")
            if i < 3:
                try:
                    client.execute_kw("sale.order", "action_confirm", [[so_id]])
                    info(f"  → confirmada como venta (id {so_id})")
                except xmlrpc.client.Fault as ex:
                    warn(f"  No se pudo confirmar (id {so_id}): {fault_brief(ex, 180)}")
        except xmlrpc.client.Fault as ex:
            warn(f"Error creando venta: {fault_brief(ex)}")
    info(f"Total órdenes de venta creadas: {len(creadas)}")
    return creadas


def crear_ordenes_compra(client: OdooXmlRpc, proveedores: list[int], productos: dict[str, int]) -> list[int]:
    header("ÓRDENES DE COMPRA (purchase.order)")
    if not model_exists(client, "purchase.order"):
        warn("Módulo de Compras no instalado (modelo purchase.order). Se omite esta sección.")
        return []
    if not proveedores or not productos:
        warn("Faltan proveedores o productos.")
        return []
    creadas: list[int] = []
    codigos_fisicos = [c for c in productos if not c.startswith("SRV-")]
    for i in range(4):
        prov = random.choice(proveedores)
        items = random.sample(codigos_fisicos, k=min(2, len(codigos_fisicos)))
        lines = []
        for codigo in items:
            pid = productos[codigo]
            lines.append((0, 0, {
                "product_id": pid,
                "product_qty": random.randint(5, 25),
                "name": codigo,
                "date_planned": (date.today() + timedelta(days=7)).isoformat(),
            }))
        vals = {
            "partner_id": prov,
            "partner_ref": f"DEMO-COMPRA-{i+1:03d}",
            "order_line": lines,
        }
        try:
            po_id = int(client.execute_kw("purchase.order", "create", [vals]))
            creadas.append(po_id)
            info(f"OC creada (id {po_id}, proveedor id {prov}, {len(lines)} líneas)")
            if i < 2:
                try:
                    client.execute_kw("purchase.order", "button_confirm", [[po_id]])
                    info(f"  → confirmada (id {po_id})")
                except xmlrpc.client.Fault as ex:
                    warn(f"  No se pudo confirmar OC (id {po_id}): {fault_brief(ex, 180)}")
        except xmlrpc.client.Fault as ex:
            warn(f"Error creando OC: {fault_brief(ex)}")
    info(f"Total órdenes de compra creadas: {len(creadas)}")
    return creadas


def crear_transferencias_inventario(client: OdooXmlRpc, productos: dict[str, int], proveedores: list[int]) -> list[int]:
    header("TRANSFERENCIAS DE INVENTARIO (stock.picking)")
    pickings: list[int] = []
    if not model_exists(client, "stock.picking") or not model_exists(client, "stock.move"):
        warn("Módulo de Inventario no instalado o incompleto (stock.picking/stock.move). Se omite esta sección.")
        return pickings
    if not productos:
        return pickings
    pt_in = _picking_type_id(client, "incoming")
    pt_out = _picking_type_id(client, "outgoing")
    if not pt_in and not pt_out:
        warn("No hay tipos de operación de inventario configurados.")
        return pickings
    codigos_fisicos = [c for c in productos if not c.startswith("SRV-")]
    if not codigos_fisicos:
        return pickings

    if pt_in and proveedores:
        codigo = random.choice(codigos_fisicos)
        pid = productos[codigo]
        try:
            picking_id = int(client.execute_kw("stock.picking", "create", [{
                "picking_type_id": pt_in,
                "partner_id": random.choice(proveedores),
                "origin": "DEMO recepción",
            }]))
            move_vals = _build_stock_move_vals(
                client,
                picking_id=picking_id,
                product_id=pid,
                qty=random.randint(10, 30),
                label=codigo,
            )
            client.execute_kw("stock.move", "create", [move_vals])
            pickings.append(picking_id)
            info(f"Recepción creada (picking id {picking_id})")
        except xmlrpc.client.Fault as ex:
            warn(f"No se pudo crear recepción: {fault_brief(ex)}")

    if pt_out:
        codigo = random.choice(codigos_fisicos)
        pid = productos[codigo]
        try:
            picking_id = int(client.execute_kw("stock.picking", "create", [{
                "picking_type_id": pt_out,
                "origin": "DEMO entrega",
            }]))
            move_vals = _build_stock_move_vals(
                client,
                picking_id=picking_id,
                product_id=pid,
                qty=random.randint(1, 5),
                label=codigo,
            )
            client.execute_kw("stock.move", "create", [move_vals])
            pickings.append(picking_id)
            info(f"Entrega creada (picking id {picking_id})")
        except xmlrpc.client.Fault as ex:
            warn(f"No se pudo crear entrega: {fault_brief(ex)}")
    info(f"Transferencias creadas: {len(pickings)}")
    return pickings


def _uom_unit_id(client: OdooXmlRpc, product_id: int) -> int:
    rows = client.execute_kw("product.product", "read", [[product_id]], {"fields": ["uom_id"]})
    if rows and rows[0].get("uom_id"):
        return int(rows[0]["uom_id"][0])
    rows = client.execute_kw(
        "uom.uom", "search_read", [[["name", "ilike", "Unidad"]]],
        {"fields": ["id"], "limit": 1},
    )
    return int(rows[0]["id"]) if rows else 1


def _picking_field(client: OdooXmlRpc, picking_id: int, field: str) -> int:
    rows = client.execute_kw("stock.picking", "read", [[picking_id]], {"fields": [field]})
    if rows and rows[0].get(field):
        v = rows[0][field]
        if isinstance(v, (list, tuple)) and v:
            return int(v[0])
        return int(v)
    return 0


def _build_stock_move_vals(client: OdooXmlRpc, *, picking_id: int, product_id: int, qty: float, label: str) -> dict[str, Any]:
    """
    Compatibilidad entre versiones Odoo para crear stock.move:
    - algunas usan product_uom, otras product_uom_id
    - algunas no aceptan campo name
    """
    meta = client.execute_kw("stock.move", "fields_get", [], {"attributes": ["type"]})
    vals: dict[str, Any] = {
        "product_id": product_id,
        "product_uom_qty": qty,
        "picking_id": picking_id,
        "location_id": _picking_field(client, picking_id, "location_id"),
        "location_dest_id": _picking_field(client, picking_id, "location_dest_id"),
    }
    uom_id = _uom_unit_id(client, product_id)
    if isinstance(meta, dict) and "product_uom" in meta:
        vals["product_uom"] = uom_id
    elif isinstance(meta, dict) and "product_uom_id" in meta:
        vals["product_uom_id"] = uom_id

    if isinstance(meta, dict) and "name" in meta:
        vals["name"] = label
    elif isinstance(meta, dict) and "description_picking" in meta:
        vals["description_picking"] = label
    elif isinstance(meta, dict) and "reference" in meta:
        vals["reference"] = label
    return vals


def crear_facturas_cliente(client: OdooXmlRpc, clientes: list[int], productos: dict[str, int]) -> list[int]:
    header("FACTURAS CLIENTE (account.move out_invoice)")
    if not clientes or not productos:
        return []
    creadas: list[int] = []
    codigos = list(productos.keys())
    for i in range(4):
        cliente = random.choice(clientes)
        codigo = random.choice(codigos)
        pid = productos[codigo]
        line = {
            "name": codigo,
            "product_id": pid,
            "quantity": random.randint(1, 4),
            "price_unit": random.choice([15000, 25000, 50000, 80000]),
        }
        vals: dict[str, Any] = {
            "move_type": "out_invoice",
            "partner_id": cliente,
            "invoice_date": (date.today() - timedelta(days=random.randint(0, 25))).isoformat(),
            "invoice_date_due": (date.today() + timedelta(days=random.randint(-10, 30))).isoformat(),
            "ref": f"DEMO-FAC-{i+1:03d}",
            "invoice_line_ids": [(0, 0, line)],
        }
        try:
            inv_id = int(client.execute_kw("account.move", "create", [vals]))
            creadas.append(inv_id)
            info(f"Factura cliente creada (id {inv_id})")
            if i < 2:
                try:
                    client.execute_kw("account.move", "action_post", [[inv_id]])
                    info(f"  → publicada (id {inv_id})")
                except xmlrpc.client.Fault as ex:
                    warn(f"  No se pudo publicar (id {inv_id}): {fault_brief(ex, 180)}")
        except xmlrpc.client.Fault as ex:
            warn(f"Error creando factura cliente: {fault_brief(ex)}")
    info(f"Total facturas cliente creadas: {len(creadas)}")
    return creadas


def crear_facturas_proveedor(client: OdooXmlRpc, proveedores: list[int], productos: dict[str, int]) -> list[int]:
    header("FACTURAS PROVEEDOR (account.move in_invoice)")
    if not proveedores or not productos:
        return []
    creadas: list[int] = []
    codigos = list(productos.keys())
    for i in range(3):
        prov = random.choice(proveedores)
        codigo = random.choice(codigos)
        pid = productos[codigo]
        line = {
            "name": codigo,
            "product_id": pid,
            "quantity": random.randint(2, 10),
            "price_unit": random.choice([2000, 6000, 12000, 25000]),
        }
        vals: dict[str, Any] = {
            "move_type": "in_invoice",
            "partner_id": prov,
            "invoice_date": (date.today() - timedelta(days=random.randint(1, 20))).isoformat(),
            "ref": f"DEMO-FAC-PROV-{i+1:03d}",
            "invoice_line_ids": [(0, 0, line)],
        }
        try:
            inv_id = int(client.execute_kw("account.move", "create", [vals]))
            creadas.append(inv_id)
            info(f"Factura proveedor creada (id {inv_id})")
            if i < 1:
                try:
                    client.execute_kw("account.move", "action_post", [[inv_id]])
                    info(f"  → publicada (id {inv_id})")
                except xmlrpc.client.Fault as ex:
                    warn(f"  No se pudo publicar (id {inv_id}): {fault_brief(ex, 180)}")
        except xmlrpc.client.Fault as ex:
            warn(f"Error creando factura proveedor: {fault_brief(ex)}")
    info(f"Total facturas proveedor creadas: {len(creadas)}")
    return creadas


# ============================================================================
# Main
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Poblar Odoo con datos de prueba en español.")
    parser.add_argument("--extra", action="store_true", help="Añade un sufijo aleatorio para forzar nuevo lote.")
    parser.add_argument("--seed", type=int, default=42, help="Semilla aleatoria para reproducibilidad.")
    args = parser.parse_args(argv)

    random.seed(args.seed)
    sufijo = ""
    if args.extra:
        sufijo = " #" + str(random.randint(100, 999))

    settings = load_settings()
    client = OdooXmlRpc(settings)

    print()
    print("Conectando a Odoo:")
    print(f"  URL: {settings.odoo_url}")
    print(f"  DB:  {settings.odoo_db}")
    print(f"  User: {settings.odoo_username}")
    try:
        uid = client.uid
    except Exception as ex:
        print(f"\nERROR de autenticación: {ex}")
        return 2
    print(f"  uid: {uid}")
    if sufijo:
        print(f"  Sufijo demo: «{sufijo}»")

    try:
        clientes = crear_clientes(client, sufijo)
        proveedores = crear_proveedores(client, sufijo)
        productos = crear_productos(client, sufijo)
        crear_reglas_reorden(client, productos)
        ventas = crear_ordenes_venta(client, clientes, productos)
        compras = crear_ordenes_compra(client, proveedores, productos)
        pickings = crear_transferencias_inventario(client, productos, proveedores)
        fac_cli = crear_facturas_cliente(client, clientes, productos)
        fac_prov = crear_facturas_proveedor(client, proveedores, productos)
    except xmlrpc.client.Fault as ex:
        print(f"\nFault Odoo no controlado: {ex.faultString}")
        return 3

    header("RESUMEN")
    print(f"  Clientes asegurados:        {len(clientes)}")
    print(f"  Proveedores asegurados:     {len(proveedores)}")
    print(f"  Productos asegurados:       {len(productos)}")
    print(f"  Órdenes de venta creadas:   {len(ventas)}")
    print(f"  Órdenes de compra creadas:  {len(compras)}")
    print(f"  Transferencias creadas:     {len(pickings)}")
    print(f"  Facturas cliente creadas:   {len(fac_cli)}")
    print(f"  Facturas proveedor creadas: {len(fac_prov)}")
    print()
    print("Hecho. Ya puedes probar el agente con:")
    print("  - 'Muéstrame el dashboard general con KPIs'")
    print("  - 'Lista las órdenes por entregar'")
    print("  - 'Muéstrame las facturas vencidas'")
    print("  - 'Revisa productos bajo mínimo'")
    print("  - 'Muéstrame el proveedor más barato para producto SILLA'")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
