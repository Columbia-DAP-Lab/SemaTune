#!/usr/bin/env python3
"""Load/query offline memory artifacts in a persistent Chroma store."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from google import genai
from google.genai import types

from .common import (
    GEMINI_EMBEDDING_DIMENSION,
    GEMINI_EMBEDDING_MODEL,
    RAW_HISTORY_COLLECTION,
    RUN_SUMMARY_COLLECTION,
    REDACTION_SCHEMA_VERSION,
    SUMMARY_SCHEMA_VERSION,
)


@dataclass
class MemoryDocument:
    id: str
    collection: str
    metadata: Dict[str, Any]
    text: str


class GeminiEmbeddingProvider:
    """Embed retrieval documents/queries with Gemini embeddings."""

    def __init__(
        self,
        *,
        model_name: str = GEMINI_EMBEDDING_MODEL,
        dimension: int = GEMINI_EMBEDDING_DIMENSION,
    ) -> None:
        self.model_name = model_name
        self.dimension = dimension
        resolved_api_key = os.getenv("GEMINI_API_KEY")
        if not resolved_api_key:
            raise RuntimeError("Gemini embeddings require GEMINI_API_KEY.")
        self.client = genai.Client(api_key=resolved_api_key)

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> List[float]:
        vectors = self._embed([text], task_type="RETRIEVAL_QUERY")
        return vectors[0]

    def _embed(self, texts: Sequence[str], *, task_type: str) -> List[List[float]]:
        if not texts:
            return []
        response = self.client.models.embed_content(
            model=self.model_name,
            contents=list(texts),
            config=types.EmbedContentConfig(
                taskType=task_type,
                outputDimensionality=self.dimension,
            ),
        )
        embeddings = getattr(response, "embeddings", None) or []
        return [list(item.values or []) for item in embeddings]


def _require_chromadb():
    import chromadb

    return chromadb


def _get_client(store_path: str | Path):
    chromadb = _require_chromadb()
    return chromadb.PersistentClient(path=str(store_path))


def _get_or_create_collection(client, name: str):
    return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _documents_from_redacted(payload: Mapping[str, Any]) -> List[MemoryDocument]:
    docs: List[MemoryDocument] = []
    rag = payload.get("rag_projection") or {}
    for key in ("first_entry_to_redacted", "full_redacted"):
        doc = rag.get(key)
        if doc:
            docs.append(MemoryDocument(**doc))
    return docs


def _documents_from_summary(payload: Mapping[str, Any]) -> List[MemoryDocument]:
    docs: List[MemoryDocument] = []
    rag = payload.get("rag_projection") or {}
    for key in ("first_entry_to_summary", "full_summary"):
        doc = rag.get(key)
        if doc:
            docs.append(MemoryDocument(**doc))
    return docs


def _coerce_documents(payload: Mapping[str, Any]) -> List[MemoryDocument]:
    schema_version = payload.get("schema_version")
    if schema_version == REDACTION_SCHEMA_VERSION:
        return _documents_from_redacted(payload)
    if schema_version == SUMMARY_SCHEMA_VERSION:
        return _documents_from_summary(payload)
    raise ValueError(f"Unsupported memory artifact schema_version: {schema_version}")


def _summary_metadata_from_payload(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    schema_version = payload.get("schema_version")
    if schema_version != SUMMARY_SCHEMA_VERSION:
        return None
    run_id = str(payload.get("source_run_id") or payload.get("run_id") or "").strip()
    if not run_id:
        return None
    return {
        "run_id": run_id,
        "run_summary_text": str(payload.get("summary_text") or "").strip(),
        "run_summary_model": str(payload.get("model_name") or "").strip() or None,
    }


def _summary_lookup_from_collection(summary_collection) -> Dict[str, Dict[str, Any]]:
    try:
        result = summary_collection.get(
            where={"source_kind": "full_summary"},
            include=["documents", "metadatas"],
        )
    except Exception:
        return {}

    ids = result.get("ids") or []
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    lookup: Dict[str, Dict[str, Any]] = {}
    for _, document, metadata in zip(ids, documents, metadatas):
        meta = dict(metadata or {})
        run_id = str(meta.get("run_id") or "").strip()
        if not run_id:
            continue
        lookup[run_id] = {
            "run_id": run_id,
            "run_summary_text": str(meta.get("run_summary_text") or document or "").strip(),
            "run_summary_model": meta.get("run_summary_model") or meta.get("summary_model_name"),
        }
    return lookup


def _attach_summary_metadata(
    document: MemoryDocument,
    summary_lookup: Mapping[str, Mapping[str, Any]],
) -> MemoryDocument:
    run_id = str(document.metadata.get("run_id") or "").strip()
    summary_metadata = summary_lookup.get(run_id)
    if not run_id or not summary_metadata:
        return document
    document.metadata = {
        **document.metadata,
        "run_summary_text": summary_metadata.get("run_summary_text"),
        "run_summary_model": summary_metadata.get("run_summary_model"),
    }
    return document


def _backfill_summary_metadata(collection, run_id: str, summary_metadata: Mapping[str, Any]) -> int:
    try:
        result = collection.get(where={"run_id": run_id}, include=["metadatas"])
    except Exception:
        return 0

    ids = result.get("ids") or []
    metadatas = result.get("metadatas") or []
    if not ids:
        return 0

    updated_metadatas = []
    for metadata in metadatas:
        updated_metadatas.append(
            {
                **dict(metadata or {}),
                "run_summary_text": summary_metadata.get("run_summary_text"),
                "run_summary_model": summary_metadata.get("run_summary_model"),
            }
        )
    try:
        collection.update(ids=ids, metadatas=updated_metadatas)
    except Exception:
        return 0
    return len(ids)


def load_into_store(
    paths: Sequence[str | Path],
    *,
    store_path: str | Path,
    embedding_provider: Optional[Any] = None,
) -> Dict[str, Any]:
    provider = embedding_provider or GeminiEmbeddingProvider()
    client = _get_client(store_path)
    collections = {
        RAW_HISTORY_COLLECTION: _get_or_create_collection(client, RAW_HISTORY_COLLECTION),
        RUN_SUMMARY_COLLECTION: _get_or_create_collection(client, RUN_SUMMARY_COLLECTION),
    }

    payloads = [_load_json(Path(raw_path)) for raw_path in paths]
    existing_summary_lookup = _summary_lookup_from_collection(collections[RUN_SUMMARY_COLLECTION])
    input_summary_lookup: Dict[str, Dict[str, Any]] = {}
    for payload in payloads:
        summary_metadata = _summary_metadata_from_payload(payload)
        if summary_metadata:
            input_summary_lookup[str(summary_metadata["run_id"])] = summary_metadata
    summary_lookup = {**existing_summary_lookup, **input_summary_lookup}

    all_documents: List[MemoryDocument] = []
    for payload in payloads:
        for document in _coerce_documents(payload):
            all_documents.append(_attach_summary_metadata(document, summary_lookup))

    grouped: Dict[str, List[MemoryDocument]] = {
        RAW_HISTORY_COLLECTION: [],
        RUN_SUMMARY_COLLECTION: [],
    }
    for document in all_documents:
        grouped[document.collection].append(document)

    result = {"store_path": str(store_path), "collections": {}}
    for collection_name, documents in grouped.items():
        if not documents:
            result["collections"][collection_name] = {"upserted": 0}
            continue
        vectors = provider.embed_documents([document.text for document in documents])
        collections[collection_name].upsert(
            ids=[document.id for document in documents],
            documents=[document.text for document in documents],
            metadatas=[document.metadata for document in documents],
            embeddings=vectors,
        )
        result["collections"][collection_name] = {"upserted": len(documents)}

    backfilled = {RAW_HISTORY_COLLECTION: 0, RUN_SUMMARY_COLLECTION: 0}
    for run_id, summary_metadata in input_summary_lookup.items():
        backfilled[RAW_HISTORY_COLLECTION] += _backfill_summary_metadata(
            collections[RAW_HISTORY_COLLECTION], run_id, summary_metadata
        )
        backfilled[RUN_SUMMARY_COLLECTION] += _backfill_summary_metadata(
            collections[RUN_SUMMARY_COLLECTION], run_id, summary_metadata
        )
    result["summary_backfilled"] = backfilled
    return result


def render_query_text_from_snapshot(snapshot: Mapping[str, Any]) -> str:
    optimization_metric = snapshot.get("optimization_metric") or snapshot.get("goal_metric") or (
        (snapshot.get("objective") or {}).get("optimization_metric")
    )
    optimization_goal = snapshot.get("optimization_goal") or snapshot.get("goal_direction") or (
        (snapshot.get("objective") or {}).get("optimization_goal")
    )
    system_metrics = snapshot.get("system_metrics") or snapshot.get("current_system_metrics") or {}
    system_perf_metrics = snapshot.get("system_perf_metrics") or snapshot.get("perf_metrics") or {}
    lines = [
        "Match an initial unchanged system state for retrieval-backed OS tuning.",
        f"optimization_metric={optimization_metric}",
        f"optimization_goal={optimization_goal}",
        "system_metrics=" + json.dumps(system_metrics, sort_keys=True),
        "system_perf_metrics=" + json.dumps(system_perf_metrics, sort_keys=True),
    ]
    return "\n".join(lines)


def _query_collection(collection, query_embedding: Sequence[float], *, top_k: int, where: Optional[Dict[str, Any]], stage: str) -> List[Dict[str, Any]]:
    result = collection.query(
        query_embeddings=[list(query_embedding)],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    ids = result.get("ids") or [[]]
    documents = result.get("documents") or [[]]
    metadatas = result.get("metadatas") or [[]]
    distances = result.get("distances") or [[]]
    hits: List[Dict[str, Any]] = []
    for doc_id, document, metadata, distance in zip(ids[0], documents[0], metadatas[0], distances[0]):
        hits.append(
            {
                "id": doc_id,
                "document": document,
                "metadata": metadata or {},
                "distance": float(distance),
                "stage": stage,
            }
        )
    return hits


def _resolve_target_document(client, metadata: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    target_document_id = metadata.get("target_document_id")
    target_collection = metadata.get("target_collection")
    if not target_document_id or not target_collection:
        return None
    try:
        collection = client.get_collection(str(target_collection))
        result = collection.get(ids=[str(target_document_id)], include=["documents", "metadatas"])
    except Exception:
        return None

    ids = result.get("ids") or []
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    if not ids:
        return None
    return {
        "id": ids[0],
        "document": documents[0] if documents else "",
        "metadata": metadatas[0] if metadatas else {},
    }


def _merge_hits(hits: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_id: Dict[str, Dict[str, Any]] = {}
    for hit in hits:
        existing = best_by_id.get(hit["id"])
        if existing is None or hit["distance"] < existing["distance"]:
            best_by_id[hit["id"]] = hit

    merged = list(best_by_id.values())
    for hit in merged:
        hit["adjusted_distance"] = hit["distance"]
    merged.sort(key=lambda hit: (hit["distance"], hit["id"]))
    return merged


def query_store(
    *,
    store_path: str | Path,
    query_text: Optional[str] = None,
    query_json: Optional[str | Path | Mapping[str, Any]] = None,
    top_k: int = 5,
    embedding_provider: Optional[Any] = None,
) -> Dict[str, Any]:
    if (query_text is None) == (query_json is None):
        raise ValueError("Provide exactly one of query_text or query_json")

    snapshot_mode = query_json is not None
    if isinstance(query_json, Mapping):
        query_payload = dict(query_json)
    elif query_json is not None:
        query_payload = _load_json(Path(query_json))
    else:
        query_payload = None

    effective_query_text = query_text or render_query_text_from_snapshot(query_payload or {})
    provider = embedding_provider or GeminiEmbeddingProvider()
    query_vector = provider.embed_query(effective_query_text)

    client = _get_client(store_path)
    hits: List[Dict[str, Any]] = []

    anchor_kinds = ["first_entry_to_redacted", "first_entry_to_summary"]

    try:
        raw_collection = client.get_collection(RAW_HISTORY_COLLECTION)
        hits.extend(
            _query_collection(
                raw_collection,
                query_vector,
                top_k=top_k,
                where={"source_kind": "first_entry_to_redacted"},
                stage="first_entry_to_redacted",
            )
        )
    except Exception:
        pass

    try:
        summary_collection = client.get_collection(RUN_SUMMARY_COLLECTION)
        hits.extend(
            _query_collection(
                summary_collection,
                query_vector,
                top_k=top_k,
                where={"source_kind": "first_entry_to_summary"},
                stage="first_entry_to_summary",
            )
        )
    except Exception:
        pass

    merged_hits = _merge_hits(hits)[:top_k]
    for hit in merged_hits:
        hit["resolved_target"] = _resolve_target_document(client, hit.get("metadata", {}))
        resolved_target = hit.get("resolved_target") or {}
        resolved_metadata = resolved_target.get("metadata") or {}
        hit["run_summary_text"] = (
            hit.get("metadata", {}).get("run_summary_text")
            or resolved_metadata.get("run_summary_text")
        )
        hit["run_summary_model"] = (
            hit.get("metadata", {}).get("run_summary_model")
            or resolved_metadata.get("run_summary_model")
        )
    return {
        "query_text": effective_query_text,
        "snapshot_mode": snapshot_mode,
        "hits": merged_hits,
    }
