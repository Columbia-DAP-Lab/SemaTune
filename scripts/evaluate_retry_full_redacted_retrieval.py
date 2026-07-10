#!/usr/bin/env python3
"""Leave-one-out retry retrieval experiment on full redacted runs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import chromadb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.memory.redaction import redact_history_data  # noqa: E402
from barebones_optimizer.memory.store import GeminiEmbeddingProvider  # noqa: E402
from barebones_optimizer.memory.summary import (  # noqa: E402
    GoogleGenAISummaryBackend,
    summarize_redacted_data,
)


TARGET_TUNER_SUBDIR = "llm_dual_app_metrics_final_actor"
FULL_REDACTED_COLLECTION_NAME = "eval_retry_full_redacted"
FIRST_SIGNATURE_COLLECTION_NAME = "eval_retry_first_signature"


@dataclass(frozen=True)
class RunRecord:
    retry_dir: Path
    workload_dir: Path
    json_path: Path
    benchmark_family_label: str
    holdout: bool


def discover_retry_workloads(root: Path) -> List[Tuple[Path, Path, List[Path]]]:
    rows: List[Tuple[Path, Path, List[Path]]] = []
    search_roots = [root]
    archived_results_root = root / "all_results"
    if archived_results_root.is_dir():
        search_roots.append(archived_results_root)

    retry_dirs = {
        path.resolve(): path
        for search_root in search_roots
        for path in search_root.glob("results_*_retry*")
        if path.is_dir()
    }
    for retry_dir in sorted(retry_dirs.values()):
        for workload_dir in sorted([p for p in retry_dir.iterdir() if p.is_dir()]):
            tux_dir = workload_dir / TARGET_TUNER_SUBDIR
            if not tux_dir.is_dir():
                continue
            jsons = sorted(tux_dir.glob("*.json"))
            if not jsons:
                continue
            rows.append((retry_dir, workload_dir, jsons))
    return rows


def _all_run_records(rows: Sequence[Tuple[Path, Path, List[Path]]]) -> List[RunRecord]:
    records: List[RunRecord] = []
    for retry_dir, workload_dir, jsons in rows:
        label = workload_dir.name
        for json_path in jsons:
            records.append(
                RunRecord(
                    retry_dir=retry_dir,
                    workload_dir=workload_dir,
                    json_path=json_path,
                    benchmark_family_label=label,
                    holdout=False,
                )
            )
    return records


def split_holdouts(
    rows: Sequence[Tuple[Path, Path, List[Path]]],
    *,
    cv_mode: str = "latest_per_workload",
) -> Tuple[List[RunRecord], List[RunRecord]]:
    if cv_mode == "all_runs":
        records = _all_run_records(rows)
        indexed = [RunRecord(**{**record.__dict__, "holdout": False}) for record in records]
        holdouts = [RunRecord(**{**record.__dict__, "holdout": True}) for record in records]
        return indexed, holdouts

    corpus: List[RunRecord] = []
    holdouts: List[RunRecord] = []
    for retry_dir, workload_dir, jsons in rows:
        label = workload_dir.name
        holdout_json = jsons[-1]
        holdouts.append(
            RunRecord(
                retry_dir=retry_dir,
                workload_dir=workload_dir,
                json_path=holdout_json,
                benchmark_family_label=label,
                holdout=True,
            )
        )
        for json_path in jsons[:-1]:
            corpus.append(
                RunRecord(
                    retry_dir=retry_dir,
                    workload_dir=workload_dir,
                    json_path=json_path,
                    benchmark_family_label=label,
                    holdout=False,
                )
            )
    return corpus, holdouts


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def redact_run(path: Path, *, keep_app_metrics: bool = False) -> Dict[str, Any]:
    return redact_history_data(load_json(path), keep_app_metrics=keep_app_metrics)


def _summary_cache_path(record: RunRecord, cache_dir: Path) -> Path:
    safe_name = f"{record.benchmark_family_label}__{record.json_path.stem}_summary_cache.json"
    return cache_dir / safe_name


def _load_or_generate_run_summary(
    record: RunRecord,
    redacted: Mapping[str, Any],
    *,
    summary_backend: Optional[Any] = None,
    summary_cache_dir: Optional[Path] = None,
    summary_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    cache_key = str(record.json_path.resolve())
    if summary_cache is not None and cache_key in summary_cache:
        return summary_cache[cache_key]

    cache_path = None
    if summary_cache_dir is not None:
        summary_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = _summary_cache_path(record, summary_cache_dir)
        if cache_path.exists():
            payload = load_json(cache_path)
            if summary_cache is not None:
                summary_cache[cache_key] = payload
            return payload

    if summary_backend is None:
        return None

    payload = summarize_redacted_data(redacted, backend=summary_backend)
    if cache_path is not None:
        cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if summary_cache is not None:
        summary_cache[cache_key] = payload
    return payload


def extract_full_redacted_document(redacted: Mapping[str, Any]) -> Dict[str, Any]:
    doc = dict((redacted.get("rag_projection") or {}).get("full_redacted") or {})
    if not doc:
        raise ValueError("Missing full_redacted document in rag_projection")
    return doc


def render_first_entry_system_query_text(
    redacted: Mapping[str, Any],
    *,
    include_app_metrics: bool = False,
) -> str:
    first_entry = redacted["first_history_entry"]
    lines = [
        "Match this first observed system signature.",
        "system_metrics=" + json.dumps(first_entry.get("system_metrics", {}), sort_keys=True),
        "system_perf_metrics=" + json.dumps(first_entry.get("system_perf_metrics", {}), sort_keys=True),
    ]
    if include_app_metrics and first_entry.get("app_metrics"):
        lines.append("app_metrics=" + json.dumps(first_entry.get("app_metrics", {}), sort_keys=True))
    return "\n".join(lines)


def build_first_signature_documents(
    records: Sequence[RunRecord],
    *,
    keep_app_metrics: bool = False,
    include_app_metrics: bool = False,
    summary_backend: Optional[Any] = None,
    summary_cache_dir: Optional[Path] = None,
    summary_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for record in records:
        redacted = redact_run(record.json_path, keep_app_metrics=keep_app_metrics)
        summary_payload = _load_or_generate_run_summary(
            record,
            redacted,
            summary_backend=summary_backend,
            summary_cache_dir=summary_cache_dir,
            summary_cache=summary_cache,
        )
        unique_suffix = f"{record.benchmark_family_label}::{record.json_path.stem}"
        docs.append(
            {
                "id": f"{redacted['run_id']}::{unique_suffix}::first_signature",
                "collection": FIRST_SIGNATURE_COLLECTION_NAME,
                "metadata": {
                    "run_id": redacted["run_id"],
                    "source_kind": "first_signature",
                    "benchmark_family_label": record.benchmark_family_label,
                    "retry_dir": record.retry_dir.name,
                    "workload_dir": record.workload_dir.name,
                    "source_json_path": str(record.json_path),
                    "holdout": False,
                    "keep_app_metrics": bool(keep_app_metrics),
                    "include_app_metrics_in_text": bool(include_app_metrics),
                    "run_summary_text": (summary_payload or {}).get("summary_text"),
                    "run_summary_model": (summary_payload or {}).get("model_name"),
                },
                "text": render_first_entry_system_query_text(
                    redacted,
                    include_app_metrics=include_app_metrics,
                ),
            }
        )
    return docs


def build_corpus_documents(
    records: Sequence[RunRecord],
    *,
    keep_app_metrics: bool = False,
    summary_backend: Optional[Any] = None,
    summary_cache_dir: Optional[Path] = None,
    summary_cache: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for record in records:
        redacted = redact_run(record.json_path, keep_app_metrics=keep_app_metrics)
        summary_payload = _load_or_generate_run_summary(
            record,
            redacted,
            summary_backend=summary_backend,
            summary_cache_dir=summary_cache_dir,
            summary_cache=summary_cache,
        )
        doc = extract_full_redacted_document(redacted)
        unique_suffix = f"{record.benchmark_family_label}::{record.json_path.stem}"
        doc["id"] = f"{redacted['run_id']}::{unique_suffix}::full_redacted"
        doc["metadata"] = {
            **dict(doc.get("metadata") or {}),
            "benchmark_family_label": record.benchmark_family_label,
            "retry_dir": record.retry_dir.name,
            "workload_dir": record.workload_dir.name,
            "source_json_path": str(record.json_path),
            "holdout": False,
            "keep_app_metrics": bool(keep_app_metrics),
            "run_summary_text": (summary_payload or {}).get("summary_text"),
            "run_summary_model": (summary_payload or {}).get("model_name"),
        }
        docs.append(doc)
    return docs


def build_holdout_queries(
    records: Sequence[RunRecord],
    *,
    keep_app_metrics: bool = False,
    include_app_metrics: bool = False,
) -> List[Dict[str, Any]]:
    queries: List[Dict[str, Any]] = []
    for record in records:
        redacted = redact_run(record.json_path, keep_app_metrics=keep_app_metrics)
        queries.append(
            {
                "record": record,
                "redacted": redacted,
                "query_text": render_first_entry_system_query_text(
                    redacted,
                    include_app_metrics=include_app_metrics,
                ),
            }
        )
    return queries


def _collection(client, name: str):
    return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def build_experiment_store(
    docs: Sequence[Mapping[str, Any]],
    *,
    store_path: Path,
    collection_name: str,
    embedding_provider: Optional[Any] = None,
) -> None:
    provider = embedding_provider or GeminiEmbeddingProvider()
    client = chromadb.PersistentClient(path=str(store_path))
    collection = _collection(client, collection_name)
    vectors = provider.embed_documents([doc["text"] for doc in docs])
    collection.upsert(
        ids=[doc["id"] for doc in docs],
        documents=[doc["text"] for doc in docs],
        metadatas=[doc["metadata"] for doc in docs],
        embeddings=vectors,
    )


def query_experiment_store(
    query_text: str,
    *,
    store_path: Path,
    collection_name: str,
    top_k: int = 1,
    embedding_provider: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    provider = embedding_provider or GeminiEmbeddingProvider()
    client = chromadb.PersistentClient(path=str(store_path))
    collection = client.get_collection(collection_name)
    result = collection.query(
        query_embeddings=[provider.embed_query(query_text)],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    ids = result.get("ids") or [[]]
    docs = result.get("documents") or [[]]
    metas = result.get("metadatas") or [[]]
    dists = result.get("distances") or [[]]
    rows: List[Dict[str, Any]] = []
    for doc_id, document, metadata, distance in zip(ids[0], docs[0], metas[0], dists[0]):
        rows.append(
            {
                "id": doc_id,
                "document": document,
                "metadata": metadata or {},
                "distance": float(distance),
            }
        )
    return rows


def score_retrieval(
    holdout_queries: Sequence[Mapping[str, Any]],
    *,
    store_path: Path,
    collection_name: str,
    top_k: int = 3,
    self_exclude: bool = False,
    embedding_provider: Optional[Any] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    correct_top1 = 0
    correct_top3 = 0
    confusion: Dict[str, int] = {}

    for item in holdout_queries:
        record = item["record"]
        search_k = top_k + 1 if self_exclude else top_k
        hits = query_experiment_store(
            item["query_text"],
            store_path=store_path,
            collection_name=collection_name,
            top_k=search_k,
            embedding_provider=embedding_provider,
        )
        if self_exclude:
            holdout_path = str(record.json_path)
            hits = [
                hit
                for hit in hits
                if hit.get("metadata", {}).get("source_json_path") != holdout_path
            ]
        hits = hits[:top_k]
        top = hits[0] if hits else None
        retrieved_label = (top or {}).get("metadata", {}).get("benchmark_family_label")
        top_k_labels = [
            hit.get("metadata", {}).get("benchmark_family_label")
            for hit in hits
        ]
        is_top1_correct = retrieved_label == record.benchmark_family_label
        is_top3_correct = record.benchmark_family_label in top_k_labels
        if is_top1_correct:
            correct_top1 += 1
        if is_top3_correct:
            correct_top3 += 1
        else:
            confusion_key = f"{record.benchmark_family_label} -> {retrieved_label}"
            confusion[confusion_key] = confusion.get(confusion_key, 0) + 1

        rows.append(
            {
                "holdout_workload": record.benchmark_family_label,
                "holdout_json_path": str(record.json_path),
                "retrieved_workload": retrieved_label,
                "retrieved_run_id": (top or {}).get("metadata", {}).get("run_id"),
                "retrieved_json_path": (top or {}).get("metadata", {}).get("source_json_path"),
                "retrieved_summary_text": (top or {}).get("metadata", {}).get("run_summary_text"),
                "distance": (top or {}).get("distance"),
                "top1_correct": bool(is_top1_correct),
                "top3_correct": bool(is_top3_correct),
                "top3_labels": json.dumps(top_k_labels),
                "top3_run_ids": json.dumps(
                    [hit.get("metadata", {}).get("run_id") for hit in hits]
                ),
                "top3_json_paths": json.dumps(
                    [hit.get("metadata", {}).get("source_json_path") for hit in hits]
                ),
                "top3_summary_texts": json.dumps(
                    [hit.get("metadata", {}).get("run_summary_text") for hit in hits]
                ),
            }
        )

    top1_accuracy = (correct_top1 / len(holdout_queries)) if holdout_queries else 0.0
    top3_accuracy = (correct_top3 / len(holdout_queries)) if holdout_queries else 0.0
    summary = {
        "collection_name": collection_name,
        "self_exclude": bool(self_exclude),
        "holdout_count": len(holdout_queries),
        "correct_count_top1": correct_top1,
        "correct_count_top3": correct_top3,
        "top1_accuracy": top1_accuracy,
        "top3_accuracy": top3_accuracy,
        "confusions": confusion,
    }
    return rows, summary


def build_pca_dataframe(
    corpus_docs: Sequence[Mapping[str, Any]],
    holdout_queries: Sequence[Mapping[str, Any]],
    *,
    embedding_provider: Optional[Any] = None,
) -> pd.DataFrame:
    provider = embedding_provider or GeminiEmbeddingProvider()
    corpus_texts = [doc["text"] for doc in corpus_docs]
    holdout_texts = [item["query_text"] for item in holdout_queries]
    corpus_vectors = provider.embed_documents(corpus_texts)
    holdout_vectors = provider.embed_documents(holdout_texts)

    rows: List[Dict[str, Any]] = []
    for doc, vector in zip(corpus_docs, corpus_vectors):
        rows.append(
            {
                "kind": "corpus",
                "benchmark_family_label": doc["metadata"]["benchmark_family_label"],
                "json_path": doc["metadata"]["source_json_path"],
                "doc_id": doc["id"],
                "source_kind": doc["metadata"]["source_kind"],
                "embedding": np.asarray(vector, dtype=float),
            }
        )
    for item, vector in zip(holdout_queries, holdout_vectors):
        record = item["record"]
        rows.append(
            {
                "kind": "holdout",
                "benchmark_family_label": record.benchmark_family_label,
                "json_path": str(record.json_path),
                "doc_id": f"holdout::{record.json_path.name}",
                "source_kind": "first_entry_query",
                "embedding": np.asarray(vector, dtype=float),
            }
        )
    df = pd.DataFrame(rows)
    matrix = np.vstack(df["embedding"].to_list())
    pca = PCA(n_components=2)
    coords = pca.fit_transform(matrix)
    out = df.drop(columns=["embedding"]).copy()
    out["pc1"] = coords[:, 0]
    out["pc2"] = coords[:, 1]
    out.attrs["explained_variance_ratio"] = pca.explained_variance_ratio_
    return out


def plot_pca(df: pd.DataFrame, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 9))
    color_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    labels = sorted(df["benchmark_family_label"].unique())
    color_map = {label: color_cycle[i % len(color_cycle)] for i, label in enumerate(labels)}
    marker_map = {"corpus": "o", "holdout": "X"}

    for (kind, label), group in df.groupby(["kind", "benchmark_family_label"]):
        ax.scatter(
            group["pc1"],
            group["pc2"],
            c=color_map[label],
            marker=marker_map[kind],
            alpha=0.85,
            s=80 if kind == "corpus" else 140,
            edgecolors="black",
            linewidths=0.4,
            label=f"{kind}:{label}",
        )

    evr = df.attrs.get("explained_variance_ratio")
    if evr is not None and len(evr) >= 2:
        ax.set_xlabel(f"PC1 ({evr[0] * 100:.1f}% var)")
        ax.set_ylabel(f"PC2 ({evr[1] * 100:.1f}% var)")
    else:
        ax.set_xlabel("PC1")
        ax.set_ylabel("PC2")
    ax.set_title("Retry Full-Redacted Retrieval Corpus + Holdout Queries (PCA)")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7, loc="best", ncols=2)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def write_summary_text(
    full_summary: Mapping[str, Any],
    full_result_rows: Sequence[Mapping[str, Any]],
    first_signature_summary: Mapping[str, Any],
    first_signature_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "Leave-One-Out Retry Retrieval on Full Redacted Runs",
        f"Holdouts: {full_summary['holdout_count']}",
        "",
        "Full redacted retrieval:",
        f"- Top-1 correct: {full_summary['correct_count_top1']}",
        f"- Top-3 correct: {full_summary['correct_count_top3']}",
        f"- Top-1 accuracy: {full_summary['top1_accuracy']:.4f}",
        f"- Top-3 accuracy: {full_summary['top3_accuracy']:.4f}",
        "Full redacted confusions:",
    ]
    if full_summary["confusions"]:
        for key, value in sorted(full_summary["confusions"].items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("First signature retrieval:")
    lines.append(f"- Top-1 correct: {first_signature_summary['correct_count_top1']}")
    lines.append(f"- Top-3 correct: {first_signature_summary['correct_count_top3']}")
    lines.append(f"- Top-1 accuracy: {first_signature_summary['top1_accuracy']:.4f}")
    lines.append(f"- Top-3 accuracy: {first_signature_summary['top3_accuracy']:.4f}")
    lines.append("First signature confusions:")
    if first_signature_summary["confusions"]:
        for key, value in sorted(first_signature_summary["confusions"].items()):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("Per-holdout full-redacted results:")
    for row in full_result_rows:
        lines.append(
            f"- {row['holdout_workload']} -> {row['retrieved_workload']} "
            f"(top1_correct={row['top1_correct']}, top3_correct={row['top3_correct']}, distance={row['distance']})"
        )
    lines.append("")
    lines.append("Per-holdout first-signature results:")
    for row in first_signature_rows:
        lines.append(
            f"- {row['holdout_workload']} -> {row['retrieved_workload']} "
            f"(top1_correct={row['top1_correct']}, top3_correct={row['top3_correct']}, distance={row['distance']})"
        )
    return "\n".join(lines)


def combine_result_rows(
    full_rows: Sequence[Mapping[str, Any]],
    first_signature_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    by_holdout = {
        row["holdout_json_path"]: {
            "holdout_workload": row["holdout_workload"],
            "holdout_json_path": row["holdout_json_path"],
        }
        for row in full_rows
    }
    for row in full_rows:
        item = by_holdout[row["holdout_json_path"]]
        item.update(
            {
                "full_redacted_retrieved_workload": row["retrieved_workload"],
                "full_redacted_retrieved_run_id": row["retrieved_run_id"],
                "full_redacted_retrieved_json_path": row["retrieved_json_path"],
                "full_redacted_retrieved_summary_text": row["retrieved_summary_text"],
                "full_redacted_distance": row["distance"],
                "full_redacted_top1_correct": row["top1_correct"],
                "full_redacted_top3_correct": row["top3_correct"],
                "full_redacted_top3_labels": row["top3_labels"],
                "full_redacted_top3_summary_texts": row["top3_summary_texts"],
            }
        )
    for row in first_signature_rows:
        item = by_holdout[row["holdout_json_path"]]
        item.update(
            {
                "first_signature_retrieved_workload": row["retrieved_workload"],
                "first_signature_retrieved_run_id": row["retrieved_run_id"],
                "first_signature_retrieved_json_path": row["retrieved_json_path"],
                "first_signature_retrieved_summary_text": row["retrieved_summary_text"],
                "first_signature_distance": row["distance"],
                "first_signature_top1_correct": row["top1_correct"],
                "first_signature_top3_correct": row["top3_correct"],
                "first_signature_top3_labels": row["top3_labels"],
                "first_signature_top3_summary_texts": row["top3_summary_texts"],
            }
        )
    return [by_holdout[key] for key in sorted(by_holdout.keys())]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help=(
            "Repository or results root to scan for retry directories; a repository "
            "root also searches its all_results directory."
        ),
    )
    parser.add_argument(
        "--store-path",
        required=True,
        help="Temporary Chroma path for the evaluation collection.",
    )
    parser.add_argument("--results-csv", required=True)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--summary-txt", required=True)
    parser.add_argument("--pca-csv", required=True)
    parser.add_argument("--pca-png", required=True)
    parser.add_argument(
        "--summary-cache-dir",
        default="",
        help="Optional cache directory for per-run summary metadata.",
    )
    parser.add_argument(
        "--limit-workloads",
        type=int,
        default=0,
        help="Optional limit for smoke-mode runs.",
    )
    parser.add_argument(
        "--cv-mode",
        choices=["latest_per_workload", "all_runs"],
        default="latest_per_workload",
        help="Cross-validation mode: latest-per-workload holdout or leave-one-run-out over all runs.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.repo_root)
    discovery = discover_retry_workloads(root)
    if args.limit_workloads > 0:
        discovery = discovery[: args.limit_workloads]
    corpus, holdouts = split_holdouts(discovery, cv_mode=args.cv_mode)

    summary_cache_dir = (
        Path(args.summary_cache_dir)
        if args.summary_cache_dir
        else Path(args.store_path).parent / "summary_cache"
    )
    summary_backend = GoogleGenAISummaryBackend()
    summary_cache: Dict[str, Dict[str, Any]] = {}

    corpus_docs = build_corpus_documents(
        corpus,
        summary_backend=summary_backend,
        summary_cache_dir=summary_cache_dir,
        summary_cache=summary_cache,
    )
    corpus_first_signature_docs = build_first_signature_documents(
        corpus,
        summary_backend=summary_backend,
        summary_cache_dir=summary_cache_dir,
        summary_cache=summary_cache,
    )
    holdout_queries = build_holdout_queries(holdouts)
    store_path = Path(args.store_path)
    store_path.mkdir(parents=True, exist_ok=True)
    build_experiment_store(
        corpus_docs,
        store_path=store_path,
        collection_name=FULL_REDACTED_COLLECTION_NAME,
    )
    build_experiment_store(
        corpus_first_signature_docs,
        store_path=store_path,
        collection_name=FIRST_SIGNATURE_COLLECTION_NAME,
    )
    full_result_rows, full_summary = score_retrieval(
        holdout_queries,
        store_path=store_path,
        collection_name=FULL_REDACTED_COLLECTION_NAME,
        self_exclude=(args.cv_mode == "all_runs"),
    )
    first_signature_rows, first_signature_summary = score_retrieval(
        holdout_queries,
        store_path=store_path,
        collection_name=FIRST_SIGNATURE_COLLECTION_NAME,
        self_exclude=(args.cv_mode == "all_runs"),
    )

    pca_df = build_pca_dataframe(corpus_first_signature_docs, holdout_queries)
    pca_csv = Path(args.pca_csv)
    pca_png = Path(args.pca_png)
    results_csv = Path(args.results_csv)
    summary_json = Path(args.summary_json)
    summary_txt = Path(args.summary_txt)

    for path in [pca_csv, pca_png, results_csv, summary_json, summary_txt]:
        path.parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(combine_result_rows(full_result_rows, first_signature_rows)).to_csv(results_csv, index=False)
    pca_df.to_csv(pca_csv, index=False)
    plot_pca(pca_df, pca_png)
    summary_payload = {
        "cv_mode": args.cv_mode,
        "workload_count": len(discovery),
        "corpus_run_count": len(corpus),
        "holdout_run_count": len(holdouts),
        "summary_cache_dir": str(summary_cache_dir),
        "full_redacted": full_summary,
        "first_signature": first_signature_summary,
    }
    summary_json.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")
    summary_txt.write_text(
        write_summary_text(full_summary, full_result_rows, first_signature_summary, first_signature_rows) + "\n",
        encoding="utf-8",
    )

    print(f"Discovered workloads: {len(discovery)}")
    print(f"CV mode: {args.cv_mode}")
    print(f"Corpus runs indexed: {len(corpus)}")
    print(f"Holdouts evaluated: {len(holdouts)}")
    print(f"Full redacted top-1 accuracy: {full_summary['top1_accuracy']:.4f}")
    print(f"Full redacted top-3 accuracy: {full_summary['top3_accuracy']:.4f}")
    print(f"First signature top-1 accuracy: {first_signature_summary['top1_accuracy']:.4f}")
    print(f"First signature top-3 accuracy: {first_signature_summary['top3_accuracy']:.4f}")
    print(f"Wrote results CSV: {results_csv}")
    print(f"Wrote summary JSON: {summary_json}")
    print(f"Wrote summary TXT: {summary_txt}")
    print(f"Wrote PCA CSV: {pca_csv}")
    print(f"Wrote PCA plot: {pca_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
