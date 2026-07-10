#!/usr/bin/env python3
"""Visualize stored memory embeddings with PCA."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

import chromadb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-path", required=True, help="Persistent Chroma directory.")
    parser.add_argument(
        "--collections",
        nargs="+",
        default=["memory_raw_histories", "memory_run_summaries"],
        help="Collections to include in the plot.",
    )
    parser.add_argument(
        "--output-png",
        required=True,
        help="Where to write the PCA scatter plot PNG.",
    )
    parser.add_argument(
        "--output-csv",
        required=True,
        help="Where to write the projected coordinates CSV.",
    )
    parser.add_argument(
        "--label-points",
        action="store_true",
        help="Annotate points with source_kind and iteration range.",
    )
    return parser.parse_args()


def load_points(store_path: str, collections: List[str]) -> pd.DataFrame:
    client = chromadb.PersistentClient(path=store_path)
    rows: List[Dict[str, object]] = []

    for collection_name in collections:
        collection = client.get_collection(collection_name)
        payload = collection.get(include=["embeddings", "metadatas", "documents"])
        ids = payload.get("ids", [])
        embeddings = payload.get("embeddings", [])
        metadatas = payload.get("metadatas", [])
        documents = payload.get("documents", [])

        for doc_id, embedding, metadata, document in zip(ids, embeddings, metadatas, documents):
            meta = metadata or {}
            rows.append(
                {
                    "id": doc_id,
                    "collection": collection_name,
                    "source_kind": meta.get("source_kind", "unknown"),
                    "phase": meta.get("phase", "unknown"),
                    "run_id": meta.get("run_id", "unknown"),
                    "optimization_metric": meta.get("optimization_metric", "unknown"),
                    "optimization_goal": meta.get("optimization_goal", "unknown"),
                    "iteration_start": meta.get("iteration_start", -1),
                    "iteration_end": meta.get("iteration_end", -1),
                    "preview": (document or "")[:180].replace("\n", " | "),
                    "embedding": np.asarray(embedding, dtype=float),
                }
            )

    if not rows:
        raise RuntimeError("No stored documents found in the requested collections.")
    return pd.DataFrame(rows)


def project_points(df: pd.DataFrame) -> pd.DataFrame:
    matrix = np.vstack(df["embedding"].to_list())
    if matrix.shape[0] < 2:
        raise RuntimeError("Need at least 2 stored documents for PCA visualization.")

    pca = PCA(n_components=2)
    coords = pca.fit_transform(matrix)

    result = df.drop(columns=["embedding"]).copy()
    result["pc1"] = coords[:, 0]
    result["pc2"] = coords[:, 1]
    result.attrs["explained_variance_ratio"] = pca.explained_variance_ratio_
    return result


def plot_points(df: pd.DataFrame, output_png: Path, label_points: bool) -> None:
    fig, ax = plt.subplots(figsize=(11, 8))

    color_cycle = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    source_kinds = sorted(df["source_kind"].unique())
    color_map = {
        source_kind: color_cycle[idx % len(color_cycle)]
        for idx, source_kind in enumerate(source_kinds)
    }
    marker_map = {
        "memory_raw_histories": "o",
        "memory_run_summaries": "s",
    }

    for (collection, source_kind), group in df.groupby(["collection", "source_kind"]):
        ax.scatter(
            group["pc1"],
            group["pc2"],
            s=90,
            alpha=0.8,
            label=f"{collection}:{source_kind}",
            c=color_map[source_kind],
            marker=marker_map.get(collection, "o"),
            edgecolors="black",
            linewidths=0.4,
        )

        if label_points:
            for _, row in group.iterrows():
                label = row["source_kind"]
                if int(row["iteration_start"]) >= 0:
                    label += f" ({row['iteration_start']}..{row['iteration_end']})"
                ax.annotate(
                    label,
                    (row["pc1"], row["pc2"]),
                    fontsize=8,
                    alpha=0.85,
                    xytext=(4, 4),
                    textcoords="offset points",
                )

    evr = df.attrs.get("explained_variance_ratio")
    if evr is not None and len(evr) >= 2:
        xlabel = f"PC1 ({evr[0] * 100:.1f}% var)"
        ylabel = f"PC2 ({evr[1] * 100:.1f}% var)"
    else:
        xlabel = "PC1"
        ylabel = "PC2"

    ax.set_title("Memory Store Embeddings Projected with PCA")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.25)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def main() -> int:
    args = parse_args()
    output_png = Path(args.output_png)
    output_csv = Path(args.output_csv)

    df = load_points(args.store_path, args.collections)
    projected = project_points(df)

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_png.parent.mkdir(parents=True, exist_ok=True)

    projected.to_csv(output_csv, index=False)
    plot_points(projected, output_png, label_points=args.label_points)

    print(f"Wrote PCA CSV: {output_csv}")
    print(f"Wrote PCA plot: {output_png}")
    print(f"Points visualized: {len(projected)}")
    print(
        "Source kinds: "
        + ", ".join(f"{kind}={count}" for kind, count in projected["source_kind"].value_counts().items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
