from __future__ import annotations

import shutil
from pathlib import Path

from llama_index.core import Settings, StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI

from odoo_rag.config import Settings as AppSettings
from odoo_rag.indexer import build_documents
from odoo_rag.odoo_client import OdooXmlRpc

_index_memory: VectorStoreIndex | None = None


def invalidate_index_cache() -> None:
    """Liberar índice en memoria tras un rebuild (CLI o API)."""
    global _index_memory
    _index_memory = None


def _configure_llama(app: AppSettings) -> None:
    if not app.openai_api_key:
        raise RuntimeError(
            "Falta OPENAI_API_KEY en .env (necesaria para embeddings y respuesta con el stack por defecto)."
        )
    Settings.llm = OpenAI(model=app.openai_llm_model, api_key=app.openai_api_key)
    Settings.embed_model = OpenAIEmbedding(model=app.openai_embed_model, api_key=app.openai_api_key)


def persist_dir(app: AppSettings) -> Path:
    path = app.odoo_rag_storage_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_or_rebuild_index(app: AppSettings, *, rebuild: bool) -> VectorStoreIndex:
    invalidate_index_cache()
    _configure_llama(app)
    client = OdooXmlRpc(app)
    documents = build_documents(client, limit=app.odoo_rag_record_limit)
    store = persist_dir(app)
    has_index = any(store.iterdir())
    if has_index and not rebuild:
        raise RuntimeError(
            f"Ya existe un índice en {store}. Usa --rebuild para reemplazarlo o borra la carpeta a mano."
        )
    if rebuild and store.exists():
        shutil.rmtree(store)
        store.mkdir(parents=True, exist_ok=True)

    index = VectorStoreIndex.from_documents(documents)
    index.storage_context.persist(persist_dir=str(store))
    return index


def load_index(app: AppSettings) -> VectorStoreIndex:
    _configure_llama(app)
    store = persist_dir(app)
    if not any(store.iterdir()):
        raise RuntimeError(
            f"No hay índice en {store}. Ejecuta primero: python -m odoo_rag index --rebuild"
        )
    storage_context = StorageContext.from_defaults(persist_dir=str(store))
    return load_index_from_storage(storage_context)


def load_index_cached(app: AppSettings) -> VectorStoreIndex:
    global _index_memory
    if _index_memory is None:
        _index_memory = load_index(app)
    return _index_memory


def ask(app: AppSettings, question: str, *, similarity_top_k: int = 6) -> str:
    index = load_index_cached(app)
    engine = index.as_query_engine(similarity_top_k=similarity_top_k)
    response = engine.query(question)
    return str(response)
