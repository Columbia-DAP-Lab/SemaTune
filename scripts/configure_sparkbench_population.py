#!/usr/bin/env python3
"""Install population-only Spark settings into the pinned DCPerf tree."""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "# SEMATUNE_SPARKBENCH_POPULATION_SETTINGS"
NEEDLE = "    for x in PLATFORMS:\n"
BLOCK = f'''    {MARKER}
    if os.environ.get("SEMATUNE_SPARKBENCH_POPULATE") == "1":
        STANDALONE_CONFIGS["default-default"]["spark.default.parallelism"] = str(
            sys_configs["cores"]
        )
        STANDALONE_CONFIGS["default-default"][
            "spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version"
        ] = "2"

'''


def configure(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return
    if "import os\n" not in text:
        text = text.replace("import socket\n", "import os\nimport socket\n", 1)
    if NEEDLE not in text:
        raise ValueError(f"unsupported DCPerf Spark config layout: {path}")
    text = text.replace(NEEDLE, BLOCK + NEEDLE, 1)
    path.write_text(text, encoding="utf-8")
    print(f"SPARKBENCH_POPULATION_CONFIG: PASS ({path})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        configure(path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
