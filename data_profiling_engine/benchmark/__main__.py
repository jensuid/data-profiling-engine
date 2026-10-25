"""Benchmark CLI (M7-T1).

    python -m data_profiling_engine.benchmark generate \\
        --rows 1000000 --columns 8 --format csv --out .benchmarks/csv_1000000_std.csv [--spec-json spec.json]

    python -m data_profiling_engine.benchmark measure \\
        --input F.csv --config C.json --emit metrics.json        # (harness child)

    python -m data_profiling_engine.benchmark baseline \\
        --sizes 1000000,5000000 --columns 8 --format csv \\
        --workdir .benchmarks --out .benchmarks/results.json

``baseline`` is the release-gate entry: it runs the row-scaling RSS curve plus
the exact-vs-sampled wall-clock pair and writes a self-describing results doc
(machine context + workload + guarantees, TESTING §4.2/§4.4). The heavy
10M–100M ladder is scheduled — invoke this explicitly, never in a per-commit
pytest run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from data_profiling_engine.benchmark.generator import DatasetSpec
from data_profiling_engine.benchmark.harness import (
    measure_and_emit,
    run_suite,
)


def _parse_sizes(raw: str) -> List[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dpe-benchmark",
        description="Data Profiling Engine — synthetic workload generator + benchmark harness (M7-T1).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write one synthetic CSV/Parquet fixture + §9 config")
    gen.add_argument("--rows", type=int, required=True)
    gen.add_argument("--columns", type=int, default=8)
    gen.add_argument("--format", choices=("csv", "parquet"), default="csv")
    gen.add_argument("--cardinality", choices=("low", "medium", "high"), default="medium")
    gen.add_argument("--missingness", type=float, default=0.1)
    gen.add_argument("--distribution", choices=("normal", "skewed"), default="normal")
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--out", required=True, help="fixture path (extension forced to --format)")

    meas = sub.add_parser("measure", help="run one profiling pass, emit metrics (harness child)")
    meas.add_argument("--input", required=True)
    meas.add_argument("--config", required=True)
    meas.add_argument("--emit", required=True)

    base = sub.add_parser("baseline", help="run the row-scaling + degrade matrix")
    base.add_argument("--sizes", default="1000000,5000000", help="comma-separated row counts")
    base.add_argument("--columns", type=int, default=8)
    base.add_argument("--format", choices=("csv", "parquet"), default="csv")
    base.add_argument("--cardinality", choices=("low", "medium", "high"), default="medium")
    base.add_argument("--missingness", type=float, default=0.1)
    base.add_argument("--distribution", choices=("normal", "skewed"), default="normal")
    base.add_argument("--mode", default="standard")
    base.add_argument("--workdir", default=".benchmarks")
    base.add_argument("--out", default=".benchmarks/results.json")
    base.add_argument(
        "--no-degrade", action="store_true",
        help="skip the exact-vs-sampled wall-clock pair (RSS scaling only)",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        from data_profiling_engine.benchmark.generator import generate_dataset, write_dataset_config

        spec = DatasetSpec(
            rows=args.rows, columns=args.columns, cardinality=args.cardinality,
            missingness=args.missingness, distribution=args.distribution, seed=args.seed,
        )
        out = generate_dataset(spec, args.out, fmt=args.format)
        cfg = write_dataset_config(spec, str(out.with_suffix(".config.json")))
        print(f"generated {out} ({out.stat().st_size} bytes); config {cfg}")
        return 0

    if args.command == "measure":
        payload = measure_and_emit(args.input, args.config, args.emit)
        print(json.dumps(payload))
        return 0

    if args.command == "baseline":
        results = run_suite(
            sizes=tuple(_parse_sizes(args.sizes)),
            columns=args.columns, fmt=args.format, cardinality=args.cardinality,
            missingness=args.missingness, distribution=args.distribution,
            mode=args.mode, workdir=args.workdir, degrade_vs_exact=not args.no_degrade,
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        _print_summary(results, out)
        return 0

    return 2  # pragma: no cover - argparse enforces the subcommand


def _print_summary(results: dict, out: Path) -> None:
    machine = results["machine"]
    print(f"machine: {machine['system']} {machine['machine']} "
          f"py{machine['python']} ram~{machine['total_ram_gb']}GB cpu{machine['cpu_count']}")
    for case in results["cases"]:
        rss = f"{case['peak_rss_mb']}MB" if case["peak_rss_mb"] is not None else "n/a"
        print(f"  {case['label']:<20} rows={case['row_count']:<10} "
              f"wall={case['wall_clock_s']:>8.3f}s rss={rss:>10} src={case['source_mb']}MB")
    print("guarantees:")
    for name, val in results["guarantees"].items():
        print(f"  {name}: {json.dumps(val)}")
    print(f"results -> {out}")


if __name__ == "__main__":
    sys.exit(main())
