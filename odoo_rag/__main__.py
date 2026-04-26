from __future__ import annotations

import argparse
import json
import sys

from odoo_rag.alerts import run_all_alerts
from odoo_rag.config import load_settings
from odoo_rag.indexer import preview_first_rows
from odoo_rag.odoo_client import OdooXmlRpc
from odoo_rag.rag import ask, build_or_rebuild_index
from odoo_rag.reports import monthly_sales_report


def _cmd_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Instala dependencias web: pip install fastapi uvicorn[standard]", file=sys.stderr)
        return 1

    cfg = load_settings()
    host = args.host if args.host is not None else cfg.odoo_rag_web_host
    port = args.port if args.port is not None else cfg.odoo_rag_web_port
    print(f"Interfaz en http://{host}:{port}/")
    uvicorn.run(
        "odoo_rag.web_app:app",
        host=host,
        port=port,
        reload=False,
    )
    return 0


def _cmd_index(args: argparse.Namespace) -> int:
    app = load_settings()
    if args.preview:
        client = OdooXmlRpc(app)
        print(preview_first_rows(client, limit=app.odoo_rag_record_limit))
        return 0
    build_or_rebuild_index(app, rebuild=args.rebuild)
    print(f"Índice guardado en {app.odoo_rag_storage_dir.resolve()}")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    app = load_settings()
    print(ask(app, args.question))
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    app = load_settings()
    print("Modo chat (vacío o 'salir' para terminar).")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line or line.lower() in {"salir", "exit", "quit"}:
            return 0
        print(ask(app, line, similarity_top_k=args.top_k))
        print()


def _cmd_alerts(args: argparse.Namespace) -> int:
    app = load_settings()
    only = [a.strip() for a in (args.only or "").split(",") if a.strip()] or None
    payload = run_all_alerts(app, use_cache=not args.no_cache, only=only)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"Severidad global: {payload['severity']}  (total ítems: {payload['count']})")
    for a in payload["alerts"]:
        head = f"[{a['severity'].upper():7}] {a['title']} — {a['count']}"
        print("\n" + head)
        print("-" * len(head))
        print(a["summary"])
        for it in a["items"][:5]:
            label = it.get("name") or it.get("partner") or it.get("id")
            extra = (
                it.get("qty_available")
                if "qty_available" in it
                else it.get("amount_residual")
                if "amount_residual" in it
                else it.get("amount_total")
            )
            print(f"  · {label}  ({extra})")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    app = load_settings()
    payload = monthly_sales_report(
        app,
        year=args.year,
        month=args.month,
        write_summary=not args.no_summary,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    data = payload["data"]
    totals = data["totals"]
    period = data["period"]
    growth = totals.get("growth_pct")
    growth_str = f"{growth:+.2f}%" if growth is not None else "n/a"
    print(f"Reporte de ventas {period['label']} ({period['start']} → {period['end']})")
    print(
        f"  Ventas (sale.order): {totals['sales_amount']:,.2f}  | "
        f"Mes anterior: {totals['previous_month_sales']:,.2f}  | Crec.: {growth_str}"
    )
    print(
        f"  Facturado (out_invoice posted): {totals['invoiced_amount']:,.2f}  | "
        f"Órdenes confirmadas: {totals['confirmed_orders']}"
    )
    if data["top_customers"]:
        print("\nTop clientes:")
        for c in data["top_customers"][:5]:
            print(f"  · {c['name']:32s}  {c['amount']:,.2f}")
    if data["top_products"]:
        print("\nTop productos:")
        for p in data["top_products"][:5]:
            print(f"  · {p['name']:32s}  {p['qty']:>6.0f}u  {p['amount']:,.2f}")
    if payload.get("summary"):
        print("\nAnálisis:")
        print(payload["summary"])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG sobre Odoo Community (XML-RPC + LlamaIndex).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="Descargar datos de Odoo y construir el índice vectorial.")
    p_index.add_argument("--rebuild", action="store_true", help="Borra el almacenamiento previo y reindexa.")
    p_index.add_argument(
        "--preview",
        action="store_true",
        help="Solo muestra un JSON con los primeros documentos (no llama a OpenAI).",
    )
    p_index.set_defaults(func=_cmd_index)

    p_ask = sub.add_parser("ask", help="Una sola pregunta en lenguaje natural.")
    p_ask.add_argument("question", type=str, help="Pregunta sobre clientes, productos, pedidos, etc.")
    p_ask.add_argument("--top-k", dest="top_k", type=int, default=6)
    p_ask.set_defaults(func=_cmd_ask)

    p_chat = sub.add_parser("chat", help="Bucle interactivo de preguntas.")
    p_chat.add_argument("--top-k", dest="top_k", type=int, default=6)
    p_chat.set_defaults(func=_cmd_chat)

    p_web = sub.add_parser("web", help="Servidor web con chat y reindexación (FastAPI).")
    p_web.add_argument("--host", default=None, help="Override de ODOO_RAG_WEB_HOST (.env).")
    p_web.add_argument("--port", type=int, default=None, help="Override de ODOO_RAG_WEB_PORT (.env).")
    p_web.set_defaults(func=_cmd_web)

    p_alerts = sub.add_parser("alerts", help="Ejecuta las alertas proactivas y muestra resumen.")
    p_alerts.add_argument("--only", default="", help="Lista separada por comas (low_stock,overdue_invoices,stale_drafts).")
    p_alerts.add_argument("--no-cache", action="store_true", help="Ignora la caché y vuelve a consultar Odoo.")
    p_alerts.add_argument("--json", action="store_true", help="Salida en JSON.")
    p_alerts.set_defaults(func=_cmd_alerts)

    p_report = sub.add_parser("report", help="Reporte de ventas mensual con análisis LLM.")
    p_report.add_argument("--year", type=int, default=None)
    p_report.add_argument("--month", type=int, default=None)
    p_report.add_argument("--no-summary", action="store_true", help="No genera análisis con LLM.")
    p_report.add_argument("--json", action="store_true", help="Salida en JSON.")
    p_report.set_defaults(func=_cmd_report)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
