#!/usr/bin/env python3
"""Write per-workload leave-one-run-out retrieval reports with multiple representations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.memory.store import GeminiEmbeddingProvider  # noqa: E402
from barebones_optimizer.memory.summary import GoogleGenAISummaryBackend  # noqa: E402
from scripts.evaluate_retry_full_redacted_retrieval import (  # noqa: E402
    _load_or_generate_run_summary,
    build_first_signature_documents,
    build_holdout_queries,
    discover_retry_workloads,
    redact_run,
    split_holdouts,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repository root to scan for retry directories.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where per-workload report files should be written.",
    )
    parser.add_argument(
        "--summary-cache-dir",
        default="",
        help="Optional cache directory for per-run summaries.",
    )
    parser.add_argument(
        "--representation",
        choices=["first_signature", "summary_text"],
        default="first_signature",
        help="Representation used for both corpus docs and holdout queries.",
    )
    parser.add_argument(
        "--keep-app-metrics",
        action="store_true",
        help="Keep and use application metrics in redaction/summaries for this experiment.",
    )
    return parser.parse_args()


def cosine_distance(query_vector: np.ndarray, doc_vector: np.ndarray) -> float:
    query_norm = np.linalg.norm(query_vector)
    doc_norm = np.linalg.norm(doc_vector)
    if query_norm == 0.0 or doc_norm == 0.0:
        return 1.0
    similarity = float(np.dot(query_vector, doc_vector) / (query_norm * doc_norm))
    return 1.0 - similarity


def _format_hit_block(hit: Mapping[str, Any], *, rank_label: str) -> str:
    return "\n".join(
        [
            f"### {rank_label}",
            f"- retrieved_workload: `{hit['benchmark_family_label']}`",
            f"- source_json_path: `{hit['source_json_path']}`",
            f"- run_id: `{hit['run_id']}`",
            f"- distance: `{hit['distance']:.6f}`",
            "",
            "Summary:",
            hit["run_summary_text"] or "(missing summary)",
        ]
    )


def build_summary_text_documents(
    records,
    *,
    keep_app_metrics: bool,
    summary_backend,
    summary_cache_dir: Path,
    summary_cache: Dict[str, Dict[str, Any]],
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
        if not summary_payload:
            raise RuntimeError(f"Missing summary payload for {record.json_path}")
        docs.append(
            {
                "id": f"{summary_payload['source_run_id']}::{record.benchmark_family_label}::{record.json_path.stem}::summary_text",
                "metadata": {
                    "run_id": summary_payload["source_run_id"],
                    "source_kind": "summary_text",
                    "benchmark_family_label": record.benchmark_family_label,
                    "retry_dir": record.retry_dir.name,
                    "workload_dir": record.workload_dir.name,
                    "source_json_path": str(record.json_path),
                    "holdout": False,
                    "keep_app_metrics": bool(keep_app_metrics),
                    "run_summary_text": summary_payload.get("summary_text"),
                    "run_summary_model": summary_payload.get("model_name"),
                },
                "text": summary_payload.get("summary_text") or "",
            }
        )
    return docs


def build_summary_text_queries(
    records,
    *,
    keep_app_metrics: bool,
    summary_backend,
    summary_cache_dir: Path,
    summary_cache: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    queries: List[Dict[str, Any]] = []
    for record in records:
        redacted = redact_run(record.json_path, keep_app_metrics=keep_app_metrics)
        summary_payload = _load_or_generate_run_summary(
            record,
            redacted,
            summary_backend=summary_backend,
            summary_cache_dir=summary_cache_dir,
            summary_cache=summary_cache,
        )
        if not summary_payload:
            raise RuntimeError(f"Missing summary payload for {record.json_path}")
        queries.append(
            {
                "record": record,
                "redacted": redacted,
                "query_text": summary_payload.get("summary_text") or "",
            }
        )
    return queries


def build_ranked_hits(
    corpus_docs: Sequence[Mapping[str, Any]],
    corpus_vectors: Sequence[np.ndarray],
    holdout_queries: Sequence[Mapping[str, Any]],
    *,
    embedding_provider: GeminiEmbeddingProvider,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in holdout_queries:
        query_vector = np.asarray(
            embedding_provider.embed_query(item["query_text"]),
            dtype=float,
        )
        record = item["record"]
        scored_hits: List[Dict[str, Any]] = []
        for doc, vector in zip(corpus_docs, corpus_vectors):
            metadata = dict(doc["metadata"])
            if metadata.get("source_json_path") == str(record.json_path):
                continue
            scored_hits.append(
                {
                    "benchmark_family_label": metadata.get("benchmark_family_label"),
                    "source_json_path": metadata.get("source_json_path"),
                    "run_id": metadata.get("run_id"),
                    "distance": cosine_distance(query_vector, vector),
                    "run_summary_text": metadata.get("run_summary_text"),
                    "run_summary_model": metadata.get("run_summary_model"),
                }
            )
        scored_hits.sort(key=lambda hit: (hit["distance"], str(hit["source_json_path"])))
        rows.append(
            {
                "holdout_workload": record.benchmark_family_label,
                "holdout_json_path": str(record.json_path),
                "top3": scored_hits[:3],
                "farthest": scored_hits[-1] if scored_hits else None,
            }
        )
    return rows


def write_workload_reports(rows: Sequence[Mapping[str, Any]], *, output_dir: Path) -> None:
    grouped: Dict[str, List[Mapping[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["holdout_workload"]), []).append(row)

    for workload, workload_rows in grouped.items():
        workload_rows = sorted(workload_rows, key=lambda row: str(row["holdout_json_path"]))
        lines = [
            f"# Retrieval Report: {workload}",
            "",
            f"Holdout runs: {len(workload_rows)}",
            "",
        ]
        for row in workload_rows:
            lines.extend(
                [
                    f"## Holdout: `{row['holdout_json_path']}`",
                    "",
                ]
            )
            for idx, hit in enumerate(row["top3"], start=1):
                lines.append(_format_hit_block(hit, rank_label=f"Top {idx}"))
                lines.append("")
            farthest = row.get("farthest")
            if farthest:
                lines.append(_format_hit_block(farthest, rank_label="Farthest Match"))
                lines.append("")
        destination = output_dir / f"{workload}.md"
        destination.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_index(
    rows: Sequence[Mapping[str, Any]],
    *,
    output_dir: Path,
    summary_cache_dir: Path,
    representation: str,
    keep_app_metrics: bool,
) -> None:
    top1_correct = 0
    top3_correct = 0
    confusion: Dict[str, int] = {}
    for row in rows:
        top3 = row["top3"]
        top1_label = top3[0]["benchmark_family_label"] if top3 else None
        top3_labels = [hit["benchmark_family_label"] for hit in top3]
        if top1_label == row["holdout_workload"]:
            top1_correct += 1
        else:
            confusion_key = f"{row['holdout_workload']} -> {top1_label}"
            confusion[confusion_key] = confusion.get(confusion_key, 0) + 1
        if row["holdout_workload"] in top3_labels:
            top3_correct += 1

    workload_names = sorted({str(row["holdout_workload"]) for row in rows})
    index_lines = [
        "# Retry CV Summary Reports",
        "",
        "Mode: leave-one-run-out cross-validation",
        f"Representation: {representation}",
        f"Keep app metrics: {keep_app_metrics}",
        f"Holdouts evaluated: {len(rows)}",
        f"Top-1 accuracy: {top1_correct / len(rows):.4f}" if rows else "Top-1 accuracy: 0.0000",
        f"Top-3 accuracy: {top3_correct / len(rows):.4f}" if rows else "Top-3 accuracy: 0.0000",
        f"Summary cache dir: `{summary_cache_dir}`",
        "",
        "## Per-workload reports",
    ]
    for workload in workload_names:
        index_lines.append(f"- [{workload}]({workload}.md)")
    index_lines.extend(["", "## Top-1 confusions"])
    if confusion:
        for key, value in sorted(confusion.items()):
            index_lines.append(f"- {key}: {value}")
    else:
        index_lines.append("- none")
    (output_dir / "README.md").write_text("\n".join(index_lines).rstrip() + "\n", encoding="utf-8")

    summary_payload = {
        "cv_mode": "all_runs",
        "representation": representation,
        "keep_app_metrics": bool(keep_app_metrics),
        "holdout_count": len(rows),
        "top1_accuracy": (top1_correct / len(rows)) if rows else 0.0,
        "top3_accuracy": (top3_correct / len(rows)) if rows else 0.0,
        "summary_cache_dir": str(summary_cache_dir),
        "confusions": confusion,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary_payload, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    root = Path(args.repo_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_cache_dir = (
        Path(args.summary_cache_dir)
        if args.summary_cache_dir
        else output_dir / "summary_cache"
    )
    summary_cache_dir.mkdir(parents=True, exist_ok=True)

    discovery = discover_retry_workloads(root)
    corpus, holdouts = split_holdouts(discovery, cv_mode="all_runs")

    summary_backend = GoogleGenAISummaryBackend()
    summary_cache: Dict[str, Dict[str, Any]] = {}
    if args.representation == "summary_text":
        corpus_docs = build_summary_text_documents(
            corpus,
            keep_app_metrics=args.keep_app_metrics,
            summary_backend=summary_backend,
            summary_cache_dir=summary_cache_dir,
            summary_cache=summary_cache,
        )
        holdout_queries = build_summary_text_queries(
            holdouts,
            keep_app_metrics=args.keep_app_metrics,
            summary_backend=summary_backend,
            summary_cache_dir=summary_cache_dir,
            summary_cache=summary_cache,
        )
    else:
        corpus_docs = build_first_signature_documents(
            corpus,
            keep_app_metrics=args.keep_app_metrics,
            include_app_metrics=args.keep_app_metrics,
            summary_backend=summary_backend,
            summary_cache_dir=summary_cache_dir,
            summary_cache=summary_cache,
        )
        holdout_queries = build_holdout_queries(
            holdouts,
            keep_app_metrics=args.keep_app_metrics,
            include_app_metrics=args.keep_app_metrics,
        )

    embedding_provider = GeminiEmbeddingProvider()
    corpus_vectors = [
        np.asarray(vector, dtype=float)
        for vector in embedding_provider.embed_documents([doc["text"] for doc in corpus_docs])
    ]
    rows = build_ranked_hits(
        corpus_docs,
        corpus_vectors,
        holdout_queries,
        embedding_provider=embedding_provider,
    )

    write_workload_reports(rows, output_dir=output_dir)
    write_index(
        rows,
        output_dir=output_dir,
        summary_cache_dir=summary_cache_dir,
        representation=args.representation,
        keep_app_metrics=args.keep_app_metrics,
    )

    print(f"Wrote per-workload reports to: {output_dir}")
    print(f"Representation: {args.representation}")
    print(f"Keep app metrics: {args.keep_app_metrics}")
    print(f"Holdouts evaluated: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
