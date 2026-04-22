from __future__ import annotations

import argparse
import sys

from odoo_rag.config import load_settings
from odoo_rag.indexer import preview_first_rows
from odoo_rag.odoo_client import OdooXmlRpc
from odoo_rag.rag import ask, build_or_rebuild_index


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

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
