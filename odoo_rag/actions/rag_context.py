from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from odoo_rag.config import Settings as AppSettings
from odoo_rag.rag import load_index_cached


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    score: float
    meta: dict[str, Any]


def _tokenize(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Záéíóúüñ0-9_]{3,}", (text or "").lower())
    return set(raw)


def _guess_allowed_models(question: str) -> set[str] | None:
    q = (question or "").lower()
    # Heurística simple: prioriza el modelo más probable según palabras clave.
    if any(w in q for w in ("cliente", "clientes", "contacto", "contactos", "proveedor", "proveedores")):
        return {"res.partner"}
    if any(w in q for w in ("producto", "productos", "artículo", "articulo", "item", "ítem", "stock")):
        return {"product.product"}
    if any(w in q for w in ("venta", "ventas", "cotización", "cotizacion", "orden", "órdenes", "pedido", "pedidos")):
        return {"sale.order"}
    return None


def _to_retrieved(nodes: Iterable[Any]) -> list[RetrievedChunk]:
    out: list[RetrievedChunk] = []
    for n in nodes:
        # LlamaIndex suele devolver NodeWithScore.
        score = float(getattr(n, "score", 0.0) or 0.0)
        node = getattr(n, "node", n)
        meta = dict(getattr(node, "metadata", None) or {})
        # `get_content()` existe en varios tipos de nodos.
        if hasattr(node, "get_content"):
            text = str(node.get_content() or "")
        else:
            text = str(getattr(node, "text", "") or getattr(n, "text", "") or "")
        if text.strip():
            out.append(RetrievedChunk(text=text.strip(), score=score, meta=meta))
    return out


def _prefilter(chunks: list[RetrievedChunk], question: str) -> list[RetrievedChunk]:
    allowed_models = _guess_allowed_models(question)
    if not allowed_models:
        return chunks
    kept = [c for c in chunks if str(c.meta.get("odoo_model") or "") in allowed_models]
    # Fallback: si el filtro deja vacío, no filtres (mejor algo que nada).
    return kept or chunks


def _rerank(chunks: list[RetrievedChunk], question: str) -> list[RetrievedChunk]:
    # Rerank barato: mezcla similitud vectorial (score) + overlap léxico.
    qtok = _tokenize(question)

    def lexical_overlap(c: RetrievedChunk) -> int:
        if not qtok:
            return 0
        ctok = _tokenize(c.text)
        return len(qtok & ctok)

    def key(c: RetrievedChunk) -> tuple[float, float]:
        overlap = float(lexical_overlap(c))
        return (overlap, float(c.score))

    return sorted(chunks, key=key, reverse=True)


def retrieve_context_chunks(app: AppSettings, question: str, *, top_k: int) -> str:
    """
    Propuesta 2: retrieval por etapas.
    - Trae candidatos (k grande)
    - Filtra por metadata (odoo_model) usando heurísticas de la pregunta
    - Rerank ligero antes de recortar a top_k
    Devuelve texto en formato legible (incluye metadata mínima).
    """
    index = load_index_cached(app)
    candidate_k = max(int(top_k) * 8, 24)
    retriever = index.as_retriever(similarity_top_k=candidate_k)
    raw_nodes = retriever.retrieve(question)
    if not raw_nodes:
        return "(No hay fragmentos relevantes en el índice.)"

    chunks = _to_retrieved(raw_nodes)
    chunks = _prefilter(chunks, question)
    chunks = _rerank(chunks, question)
    chunks = chunks[: int(top_k)]

    parts: list[str] = []
    for i, c in enumerate(chunks, start=1):
        model = c.meta.get("odoo_model")
        rid = c.meta.get("odoo_id")
        header = f"[CTX{i}] model={model} id={rid} score={round(c.score, 4)}"
        parts.append(header + "\n" + c.text)
    return "\n---\n".join(parts)

