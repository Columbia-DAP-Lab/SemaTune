#!/usr/bin/env python3
"""Generate RAG-backed prior configs for latest-run memory experiments."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from google import genai
from google.genai import types

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from barebones_optimizer.memory.common import GEMINI_SUMMARY_MODEL  # noqa: E402
from barebones_optimizer.memory.store import GeminiEmbeddingProvider  # noqa: E402
from barebones_optimizer.memory.summary import GoogleGenAISummaryBackend  # noqa: E402
from scripts.evaluate_retry_full_redacted_retrieval import (  # noqa: E402
    RunRecord,
    _load_or_generate_run_summary,
    redact_run,
    render_first_entry_system_query_text,
)
from scripts.generate_system_memory_configs import sanitize_hidden_metric_gist  # noqa: E402


WORKLOADS = [
    "masstree_hi_p99",
    "mutilate_high",
    "mutilate_low",
    "otmetrics_p99",
    "sibench_hi_p99",
    "silo_hi_p99",
    "sphinx_tput_max",
    "sysbench_cpu_tput",
    "sysbench_oltp_rw_hi_p99",
    "tpcc_hi_p99",
    "twitter_p99",
    "wikipedia_p99",
    "xapian_hi_p99",
    "ycsb_hi_p99",
]
RETRY_RESULTS_GLOB = "results_*_retry*"


@dataclass(frozen=True)
class FamilySpec:
    name: str
    results_leaf: str
    base_config_suffix: str
    generated_tuner_leaf_prefix: str
    keep_app_metrics: bool
    include_app_metrics_in_raw_query: bool
    sanitize_prior: bool


APP_FAMILY = FamilySpec(
    name="app",
    results_leaf="llm_dual_app_metrics_final_actor",
    base_config_suffix="_llm_dual_app_metrics_final_actor.json",
    generated_tuner_leaf_prefix="llm_dual_app_metrics_memory",
    keep_app_metrics=True,
    include_app_metrics_in_raw_query=True,
    sanitize_prior=False,
)
INDIRECT_FAMILY = FamilySpec(
    name="indirect",
    results_leaf="llm_dual_indirect_all_mode3_final_actor",
    base_config_suffix="_llm_dual_indirect_all_mode3_final_actor.json",
    generated_tuner_leaf_prefix="llm_dual_indirect_all_mode3_memory",
    keep_app_metrics=False,
    include_app_metrics_in_raw_query=False,
    sanitize_prior=True,
)
FAMILIES = (APP_FAMILY, INDIRECT_FAMILY)

RETRIEVAL_MODES = ("raw_to_summary", "summary_to_summary")
SELECTIONS = ("top_1", "top_3", "last_1")


@dataclass(frozen=True)
class WorkloadCase:
    workload: str
    family: FamilySpec
    base_config_path: Path
    fixed_config_path: Path
    holdout_record: RunRecord
    corpus_records: Tuple[RunRecord, ...]


class GoogleGenAIPriorTextBackend:
    """Gemini-backed plain-text prior synthesizer."""

    def __init__(
        self,
        *,
        model_name: str = GEMINI_SUMMARY_MODEL,
        max_attempts: int = 3,
    ) -> None:
        self.model_name = model_name
        self.max_attempts = max(1, int(max_attempts))
        resolved_api_key = os.getenv("GEMINI_API_KEY")
        if not resolved_api_key:
            raise RuntimeError("Prior synthesis requires GEMINI_API_KEY.")
        self.client = genai.Client(api_key=resolved_api_key)

    def generate_text(
        self,
        *,
        system_instruction: str,
        prompt: str,
    ) -> Tuple[str, Dict[str, Any]]:
        last_error: Optional[Exception] = None
        last_response_text: Optional[str] = None
        for attempt in range(1, self.max_attempts + 1):
            attempt_prompt = prompt
            if attempt > 1:
                attempt_prompt += (
                    "\n\nThe previous response was empty or malformed. "
                    "Retry and return plain text only."
                )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[system_instruction, attempt_prompt],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    maxOutputTokens=2048,
                    responseMimeType="text/plain",
                ),
            )
            try:
                text = str(getattr(response, "text", "") or "").strip()
                if not text:
                    raise ValueError("empty prior synthesis response")
                usage = getattr(response, "usage_metadata", None)
                return text, {
                    "model_name": self.model_name,
                    "attempt_count": attempt,
                    "response_text": text,
                    "token_usage": {
                        "input_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
                        "output_tokens": getattr(usage, "candidates_token_count", None) if usage else None,
                        "total_tokens": getattr(usage, "total_token_count", None) if usage else None,
                    },
                }
            except Exception as exc:  # pragma: no cover - exercised in integration
                last_error = exc
                last_response_text = getattr(response, "text", None)
        raise RuntimeError(
            "Prior synthesis failed after "
            f"{self.max_attempts} attempts: {last_error}. "
            f"Last response preview: {str(last_response_text or '')[:400]}"
        ) from last_error


def cosine_distance(query_vector: np.ndarray, doc_vector: np.ndarray) -> float:
    query_norm = np.linalg.norm(query_vector)
    doc_norm = np.linalg.norm(doc_vector)
    if query_norm == 0.0 or doc_norm == 0.0:
        return 1.0
    similarity = float(np.dot(query_vector, doc_vector) / (query_norm * doc_norm))
    return 1.0 - similarity


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _normalize_fixed_schedule(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload)
    normalized["max_iterations"] = 50
    normalized["post_tuning_windows"] = 0
    return normalized


def _copy_staged_config(source_path: Path, destination_path: Path) -> None:
    payload = _load_json(source_path)
    if str(payload.get("tuner_type") or "").strip() == "fixed":
        payload = _normalize_fixed_schedule(payload)
        _write_json(destination_path, payload)
        return
    shutil.copy2(source_path, destination_path)


def _non_window_jsons(path: Path) -> List[Path]:
    return sorted(
        file_path
        for file_path in path.glob("*.json")
        if not file_path.name.startswith("window_")
    )


def _find_base_config(config_root: Path, workload: str, suffix: str) -> Path:
    candidates = sorted((config_root / workload).glob(f"*{suffix}"))
    if not candidates:
        raise FileNotFoundError(f"Missing base config for {workload} with suffix {suffix}")
    return candidates[0]


def _find_fixed_config(config_root: Path, workload: str) -> Path:
    candidates = sorted((config_root / workload).glob("*fixed.json"))
    if not candidates:
        raise FileNotFoundError(f"Missing fixed config for {workload}")
    return candidates[0]


def _make_run_record(path: Path, workload: str, *, holdout: bool) -> RunRecord:
    workload_dir = path.parent.parent
    retry_dir = workload_dir.parent
    return RunRecord(
        retry_dir=retry_dir,
        workload_dir=workload_dir,
        json_path=path,
        benchmark_family_label=workload,
        holdout=holdout,
    )


def discover_workload_cases(
    *,
    repo_root: Path,
    config_root: Path,
    workloads: Sequence[str],
    family: FamilySpec,
) -> List[WorkloadCase]:
    cases: List[WorkloadCase] = []
    for workload in workloads:
        result_files: List[Path] = []
        for retry_root in sorted(repo_root.glob(RETRY_RESULTS_GLOB)):
            candidate_dir = retry_root / workload / family.results_leaf
            if candidate_dir.is_dir():
                result_files.extend(_non_window_jsons(candidate_dir))
        result_files = sorted(result_files)
        if len(result_files) < 2:
            raise ValueError(
                f"{workload} / {family.name} requires at least 2 retry runs, found {len(result_files)}"
            )
        base_config_path = _find_base_config(config_root, workload, family.base_config_suffix)
        fixed_config_path = _find_fixed_config(config_root, workload)
        cases.append(
            WorkloadCase(
                workload=workload,
                family=family,
                base_config_path=base_config_path,
                fixed_config_path=fixed_config_path,
                holdout_record=_make_run_record(result_files[-1], workload, holdout=True),
                corpus_records=tuple(
                    _make_run_record(path, workload, holdout=False)
                    for path in result_files[:-1]
                ),
            )
        )
    return cases


def _load_run_context(
    record: RunRecord,
    family: FamilySpec,
    *,
    summary_backend: Any,
    summary_cache_dir: Path,
    context_cache: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    cache_key = str(record.json_path.resolve())
    cached = context_cache.get(cache_key)
    if cached is not None:
        return cached

    redacted = redact_run(record.json_path, keep_app_metrics=family.keep_app_metrics)
    summary_payload = _load_or_generate_run_summary(
        record,
        redacted,
        summary_backend=summary_backend,
        summary_cache_dir=summary_cache_dir,
        summary_cache={},
    )
    if not summary_payload:
        raise RuntimeError(f"Missing summary payload for {record.json_path}")
    cached = {
        "record": record,
        "redacted": redacted,
        "summary": summary_payload,
    }
    context_cache[cache_key] = cached
    return cached


def build_family_corpus(
    cases: Sequence[WorkloadCase],
    *,
    family: FamilySpec,
    summary_backend: Any,
    summary_cache_dir: Path,
    context_cache: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for case in cases:
        for record in case.corpus_records:
            context = _load_run_context(
                record,
                family,
                summary_backend=summary_backend,
                summary_cache_dir=summary_cache_dir,
                context_cache=context_cache,
            )
            summary_payload = context["summary"]
            docs.append(
                {
                    "id": f"{summary_payload['source_run_id']}::{record.benchmark_family_label}::{record.json_path.stem}::summary_text",
                    "metadata": {
                        "run_id": summary_payload["source_run_id"],
                        "benchmark_family_label": record.benchmark_family_label,
                        "source_json_path": str(record.json_path),
                        "run_summary_text": summary_payload.get("summary_text"),
                        "run_summary_model": summary_payload.get("model_name"),
                    },
                    "text": summary_payload.get("summary_text") or "",
                }
            )
    return docs


def rank_summary_hits(
    query_text: str,
    corpus_docs: Sequence[Mapping[str, Any]],
    corpus_vectors: Sequence[np.ndarray],
    *,
    embedding_provider: Any,
) -> List[Dict[str, Any]]:
    query_vector = np.asarray(embedding_provider.embed_query(query_text), dtype=float)
    hits: List[Dict[str, Any]] = []
    for doc, vector in zip(corpus_docs, corpus_vectors):
        metadata = dict(doc["metadata"])
        hits.append(
            {
                "benchmark_family_label": metadata.get("benchmark_family_label"),
                "source_json_path": metadata.get("source_json_path"),
                "run_id": metadata.get("run_id"),
                "distance": cosine_distance(query_vector, vector),
                "run_summary_text": metadata.get("run_summary_text"),
                "run_summary_model": metadata.get("run_summary_model"),
            }
        )
    hits.sort(key=lambda hit: (hit["distance"], str(hit["source_json_path"])))
    for index, hit in enumerate(hits, start=1):
        hit["rank"] = index
    return hits


def select_hits(ranked_hits: Sequence[Mapping[str, Any]], selection: str) -> List[Dict[str, Any]]:
    if selection == "top_1":
        return [dict(ranked_hits[0])] if ranked_hits else []
    if selection == "top_3":
        return [dict(hit) for hit in ranked_hits[:3]]
    if selection == "last_1":
        return [dict(ranked_hits[-1])] if ranked_hits else []
    raise ValueError(f"Unsupported selection: {selection}")


def build_raw_query_text(redacted: Mapping[str, Any], family: FamilySpec) -> str:
    return render_first_entry_system_query_text(
        redacted,
        include_app_metrics=family.include_app_metrics_in_raw_query,
    )


def build_summary_query_text(summary_payload: Mapping[str, Any]) -> str:
    return str(summary_payload.get("summary_text") or "").strip()


def build_single_prior_prompt(
    *,
    target_redacted: Mapping[str, Any],
    retrieval_mode: str,
    selection: str,
    selected_hit: Mapping[str, Any],
) -> str:
    objective = target_redacted["objective"]
    low_confidence_note = ""
    if selection == "last_1":
        low_confidence_note = (
            "\nThis source was intentionally chosen as a far-away memory baseline. "
            "Treat it as low-confidence and explicitly flag mismatches or weak transferability."
        )
    return f"""Create a warm-start prior for a new Linux tuning run.

Target objective:
- optimization_metric: {objective['optimization_metric']}
- optimization_goal: {objective['optimization_goal']}

Retrieval mode: {retrieval_mode}
Selection: {selection}
Retrieved distance: {selected_hit['distance']:.6f}
{low_confidence_note}

Retrieved summary:
{selected_hit['run_summary_text']}

Write a plain-text prior for `previous_run_gist` that:
- captures reusable parameter relationships, promising/risky regions, and early-exploration guidance,
- treats the retrieved run as informative but not authoritative,
- avoids workload names, benchmark names, file paths, run ids, and provenance chatter,
- calls out uncertainty if evidence may not transfer cleanly,
- stays concise enough to serve as an LLM warm-start prior."""


def build_top3_prior_prompt(
    *,
    target_redacted: Mapping[str, Any],
    retrieval_mode: str,
    selected_hits: Sequence[Mapping[str, Any]],
) -> str:
    objective = target_redacted["objective"]
    summary_blocks: List[str] = []
    for index, hit in enumerate(selected_hits, start=1):
        summary_blocks.append(
            f"Retrieved summary {index} (distance={hit['distance']:.6f}):\n{hit['run_summary_text']}"
        )
    return f"""Create one combined warm-start prior for a new Linux tuning run from three retrieved prior summaries.

Target objective:
- optimization_metric: {objective['optimization_metric']}
- optimization_goal: {objective['optimization_goal']}

Retrieval mode: {retrieval_mode}
Selection: top_3

Retrieved summaries are ordered from nearest to less-near.

{chr(10).join(summary_blocks)}

Write a plain-text prior for `previous_run_gist` that:
- emphasizes consensus across the retrieved summaries,
- preserves disagreements only as caveats or branches to revalidate early,
- highlights reusable parameter relationships, promising/risky regions, and early-exploration guidance,
- avoids workload names, benchmark names, file paths, run ids, and provenance chatter,
- stays concise enough to serve as an LLM warm-start prior."""


PRIOR_SYSTEM_INSTRUCTION = (
    "You rewrite retrieved Linux tuning run summaries into a warm-start prior for a future run. "
    "Focus on transferable parameter relationships, promising/risky regions, and early validation guidance. "
    "Do not mention workload names, application names, benchmark names, file paths, or run ids. "
    "Do not invent hidden primary-metric values or rewards. Return plain text only."
)


def synthesize_prior_text(
    *,
    prior_backend: Any,
    target_redacted: Mapping[str, Any],
    family: FamilySpec,
    retrieval_mode: str,
    selection: str,
    selected_hits: Sequence[Mapping[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    if selection == "top_3":
        prompt = build_top3_prior_prompt(
            target_redacted=target_redacted,
            retrieval_mode=retrieval_mode,
            selected_hits=selected_hits,
        )
    else:
        prompt = build_single_prior_prompt(
            target_redacted=target_redacted,
            retrieval_mode=retrieval_mode,
            selection=selection,
            selected_hit=selected_hits[0],
        )
    prior_text, metadata = prior_backend.generate_text(
        system_instruction=PRIOR_SYSTEM_INSTRUCTION,
        prompt=prompt,
    )
    if family.sanitize_prior:
        prior_text = sanitize_hidden_metric_gist(
            prior_text,
            str(target_redacted["objective"]["optimization_metric"]),
        )
    return prior_text, metadata


def generated_config_filename(base_config_path: Path, family: FamilySpec, retrieval_mode: str, selection: str) -> str:
    base_prefix = base_config_path.name[: -len(family.base_config_suffix)]
    return (
        f"rag_{base_prefix}_{family.generated_tuner_leaf_prefix}_{selection}_{retrieval_mode}_final_actor.json"
    )


def generated_results_leaf(family: FamilySpec, retrieval_mode: str, selection: str) -> str:
    return f"rag_{family.generated_tuner_leaf_prefix}_{selection}_{retrieval_mode}_final_actor"


def write_generated_config(
    *,
    base_config_path: Path,
    workload: str,
    family: FamilySpec,
    retrieval_mode: str,
    selection: str,
    prior_text: str,
) -> Path:
    config = _load_json(base_config_path)
    filename = generated_config_filename(base_config_path, family, retrieval_mode, selection)
    output_path = base_config_path.parent / filename
    tuner_leaf = generated_results_leaf(family, retrieval_mode, selection)
    config["results_dir"] = f"results/{workload}/{tuner_leaf}/"
    config["previous_run_gist"] = prior_text
    output_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return output_path


def write_selection_artifacts(
    *,
    selection_dir: Path,
    selected_hits: Sequence[Mapping[str, Any]],
    prior_text: str,
    prior_metadata: Mapping[str, Any],
    config_path: Path,
) -> None:
    selection_dir.mkdir(parents=True, exist_ok=True)
    for index, hit in enumerate(selected_hits, start=1):
        (selection_dir / f"retrieved_summary_{index}.txt").write_text(
            str(hit.get("run_summary_text") or "") + "\n",
            encoding="utf-8",
        )
    _write_json(selection_dir / "selected_hits.json", {"selected_hits": list(selected_hits)})
    (selection_dir / "prior.txt").write_text(prior_text.strip() + "\n", encoding="utf-8")
    _write_json(
        selection_dir / "prior_metadata.json",
        {
            "config_path": str(config_path),
            "prior_model_name": prior_metadata.get("model_name"),
            "attempt_count": prior_metadata.get("attempt_count"),
            "token_usage": prior_metadata.get("token_usage"),
        },
    )


def maybe_load_cached_prior(selection_dir: Path) -> Optional[Tuple[str, Dict[str, Any]]]:
    prior_path = selection_dir / "prior.txt"
    metadata_path = selection_dir / "prior_metadata.json"
    if not prior_path.exists() or not metadata_path.exists():
        return None
    return prior_path.read_text(encoding="utf-8").strip(), _load_json(metadata_path)


def stage_configs_for_batch(
    *,
    stage_dir: Path,
    workload: str,
    generated_configs: Sequence[Path],
    base_config_paths: Sequence[Path],
) -> None:
    target_dir = stage_dir / workload
    target_dir.mkdir(parents=True, exist_ok=True)
    for source_path in [*generated_configs, *base_config_paths]:
        _copy_staged_config(source_path, target_dir / source_path.name)


def build_retrieval_manifest(
    *,
    holdout_record: RunRecord,
    family: FamilySpec,
    retrieval_mode: str,
    query_text: str,
    ranked_hits: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    selected = {
        selection: select_hits(ranked_hits, selection)
        for selection in SELECTIONS
    }
    return {
        "workload": holdout_record.benchmark_family_label,
        "family": family.name,
        "retrieval_mode": retrieval_mode,
        "holdout_json_path": str(holdout_record.json_path),
        "query_text": query_text,
        "ranked_candidates": list(ranked_hits),
        "selected": selected,
    }


def generate_rag_prior_configs(
    *,
    repo_root: Path,
    workloads: Sequence[str],
    artifacts_root: Path,
    stage_dir: Path,
    embedding_provider: Any,
    summary_backend: Any,
    prior_backend: Any,
) -> Dict[str, Any]:
    config_root = repo_root / "config" / "full_param"
    artifacts_root.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    summary_cache_root = artifacts_root / "summary_cache"
    context_caches: Dict[str, Dict[str, Dict[str, Any]]] = {
        family.name: {} for family in FAMILIES
    }
    summary_cache_dirs = {
        family.name: summary_cache_root / family.name for family in FAMILIES
    }

    family_cases = {
        family.name: discover_workload_cases(
            repo_root=repo_root,
            config_root=config_root,
            workloads=workloads,
            family=family,
        )
        for family in FAMILIES
    }

    family_corpus_docs: Dict[str, List[Dict[str, Any]]] = {}
    family_corpus_vectors: Dict[str, List[np.ndarray]] = {}
    for family in FAMILIES:
        docs = build_family_corpus(
            family_cases[family.name],
            family=family,
            summary_backend=summary_backend,
            summary_cache_dir=summary_cache_dirs[family.name],
            context_cache=context_caches[family.name],
        )
        family_corpus_docs[family.name] = docs
        family_corpus_vectors[family.name] = [
            np.asarray(vector, dtype=float)
            for vector in embedding_provider.embed_documents([doc["text"] for doc in docs])
        ]

    generated_config_paths: List[str] = []
    manifests: List[Dict[str, Any]] = []
    batch_stage_files: List[str] = []

    for family in FAMILIES:
        for case in family_cases[family.name]:
            workload_dir = artifacts_root / family.name / case.workload
            workload_dir.mkdir(parents=True, exist_ok=True)
            holdout_context = _load_run_context(
                case.holdout_record,
                family,
                summary_backend=summary_backend,
                summary_cache_dir=summary_cache_dirs[family.name],
                context_cache=context_caches[family.name],
            )
            target_manifest = {
                "workload": case.workload,
                "family": family.name,
                "base_config_path": str(case.base_config_path),
                "fixed_config_path": str(case.fixed_config_path),
                "holdout_json_path": str(case.holdout_record.json_path),
                "corpus_size": len(family_corpus_docs[family.name]),
                "optimization_metric": holdout_context["redacted"]["objective"]["optimization_metric"],
                "optimization_goal": holdout_context["redacted"]["objective"]["optimization_goal"],
            }
            _write_json(workload_dir / "target_run_manifest.json", target_manifest)
            (workload_dir / "holdout_summary.txt").write_text(
                str(holdout_context["summary"].get("summary_text") or "").strip() + "\n",
                encoding="utf-8",
            )
            (workload_dir / "holdout_raw_query.txt").write_text(
                build_raw_query_text(holdout_context["redacted"], family).strip() + "\n",
                encoding="utf-8",
            )

            family_generated_configs: List[Path] = []
            for retrieval_mode in RETRIEVAL_MODES:
                if retrieval_mode == "raw_to_summary":
                    query_text = build_raw_query_text(holdout_context["redacted"], family)
                else:
                    query_text = build_summary_query_text(holdout_context["summary"])

                ranked_hits = rank_summary_hits(
                    query_text,
                    family_corpus_docs[family.name],
                    family_corpus_vectors[family.name],
                    embedding_provider=embedding_provider,
                )
                retrieval_dir = workload_dir / retrieval_mode
                retrieval_manifest = build_retrieval_manifest(
                    holdout_record=case.holdout_record,
                    family=family,
                    retrieval_mode=retrieval_mode,
                    query_text=query_text,
                    ranked_hits=ranked_hits,
                )
                _write_json(retrieval_dir / "retrieval_manifest.json", retrieval_manifest)

                for selection in SELECTIONS:
                    selected_hits = select_hits(ranked_hits, selection)
                    if not selected_hits:
                        raise RuntimeError(
                            f"No hits available for {case.workload} / {family.name} / {retrieval_mode} / {selection}"
                        )
                    selection_dir = retrieval_dir / selection
                    cached_prior = maybe_load_cached_prior(selection_dir)
                    if cached_prior is None:
                        prior_text, prior_metadata = synthesize_prior_text(
                            prior_backend=prior_backend,
                            target_redacted=holdout_context["redacted"],
                            family=family,
                            retrieval_mode=retrieval_mode,
                            selection=selection,
                            selected_hits=selected_hits,
                        )
                    else:
                        prior_text, prior_metadata = cached_prior

                    config_path = write_generated_config(
                        base_config_path=case.base_config_path,
                        workload=case.workload,
                        family=family,
                        retrieval_mode=retrieval_mode,
                        selection=selection,
                        prior_text=prior_text,
                    )
                    family_generated_configs.append(config_path)
                    generated_config_paths.append(str(config_path))
                    write_selection_artifacts(
                        selection_dir=selection_dir,
                        selected_hits=selected_hits,
                        prior_text=prior_text,
                        prior_metadata=prior_metadata,
                        config_path=config_path,
                    )
                    manifests.append(
                        {
                            "workload": case.workload,
                            "family": family.name,
                            "retrieval_mode": retrieval_mode,
                            "selection": selection,
                            "config_path": str(config_path),
                            "results_dir_leaf": generated_results_leaf(family, retrieval_mode, selection),
                            "selected_source_paths": [hit["source_json_path"] for hit in selected_hits],
                        }
                    )

            base_stage_paths = [case.base_config_path, case.fixed_config_path]
            stage_configs_for_batch(
                stage_dir=stage_dir,
                workload=case.workload,
                generated_configs=family_generated_configs,
                base_config_paths=base_stage_paths if family.name == "app" else [case.base_config_path],
            )

    # Add indirect base configs + fixed configs after app pass to avoid duplicate copies.
    for case in family_cases[INDIRECT_FAMILY.name]:
        target_dir = stage_dir / case.workload
        target_dir.mkdir(parents=True, exist_ok=True)
        for source_path in [case.base_config_path]:
            destination = target_dir / source_path.name
            if not destination.exists():
                _copy_staged_config(source_path, destination)
        fixed_destination = target_dir / case.fixed_config_path.name
        if not fixed_destination.exists():
            _copy_staged_config(case.fixed_config_path, fixed_destination)

    for staged_file in sorted(stage_dir.rglob("*.json")):
        batch_stage_files.append(str(staged_file))

    batch_command = f"./run_configs_batch.sh {stage_dir} 5 --all"
    manifest_payload = {
        "workloads": list(workloads),
        "generated_config_paths": sorted(generated_config_paths),
        "generated_config_count": len(generated_config_paths),
        "batch_stage_dir": str(stage_dir),
        "batch_stage_file_count": len(batch_stage_files),
        "batch_command": batch_command,
        "entries": manifests,
    }
    _write_json(artifacts_root / "generated_config_manifest.json", manifest_payload)
    (artifacts_root / "generated_config_paths.txt").write_text(
        "\n".join(sorted(generated_config_paths)) + "\n",
        encoding="utf-8",
    )
    (artifacts_root / "batch_command.sh").write_text(batch_command + "\n", encoding="utf-8")
    return manifest_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Repository root containing config/full_param and results_*_retry*.",
    )
    parser.add_argument(
        "--artifacts-root",
        default=str(REPO_ROOT / "generated" / "rag_prior_configs"),
        help="Root for generated sidecar artifacts and manifests.",
    )
    parser.add_argument(
        "--stage-dir",
        default="",
        help="Optional batch staging directory. Defaults to <artifacts-root>/batch_stage.",
    )
    parser.add_argument(
        "--workloads",
        default=",".join(WORKLOADS),
        help="Comma-separated workload list. Defaults to the 14 retry workloads in the plan.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root)
    artifacts_root = Path(args.artifacts_root)
    stage_dir = Path(args.stage_dir) if args.stage_dir else artifacts_root / "batch_stage"
    workloads = [item.strip() for item in str(args.workloads).split(",") if item.strip()]

    summary_backend = GoogleGenAISummaryBackend()
    prior_backend = GoogleGenAIPriorTextBackend()
    embedding_provider = GeminiEmbeddingProvider()

    manifest = generate_rag_prior_configs(
        repo_root=repo_root,
        workloads=workloads,
        artifacts_root=artifacts_root,
        stage_dir=stage_dir,
        embedding_provider=embedding_provider,
        summary_backend=summary_backend,
        prior_backend=prior_backend,
    )

    print(f"Generated configs: {manifest['generated_config_count']}")
    print(f"Batch stage files: {manifest['batch_stage_file_count']}")
    print(f"Artifacts root: {artifacts_root}")
    print(f"Batch command: {manifest['batch_command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
