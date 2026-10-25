"""Benchmark CLI (M7-T1).

    python -m data_profiling_engine.benchmark generate \\
        --rows 1000000 --columns 8 --format csv --out .benchmarks/csv_1000000_std.csv [--spec-json spec.json]

    python -m data_profiling_engine.benchmark measure \\
        --input F.csv --config C.json --emit metrics.json        # (harness child)

    python -m data_profiling_engine.benchmark baseline \\
        --sizes 1000000,5000000 --columns 8 --format csv \\
        --workdir .benchmarks --out .benchmarks/results.json

    python -m data_profiling_engine.benchmark guarantees \\
        [--rows 5000] --workdir .benchmarks --out .benchmarks/guarantees.json

    python -m data_profiling_engine.benchmark watch \\
        --workdir .benchmarks/ladder --sizes 10000000,100000000 [--interval 30] [--json]

    python -m data_profiling_engine.benchmark watch \\
        --pass-name top --rows 100000000 --workdir .benchmarks/ladder [--interval 60]

``baseline`` is the release-gate entry: it runs the row-scaling RSS curve plus
the exact-vs-sampled wall-clock pair and writes a self-describing results doc
(machine context + workload + guarantees, TESTING §4.2/§4.4). The heavy
10M–100M ladder is scheduled — invoke this explicitly, never in a per-commit
pytest run.

``guarantees`` (M7-T2b) drives the real store-backed worker through
:meth:`JobManager.start_profile_job` and measures the two §4.2 rows the
in-process baseline cannot reach — **cancellation latency** and **worker-crash
recovery** (TESTING §4.3 binding structural guarantees). Dev-mode by design:
multiprocessing spawn + SQLite mirror behave identically in the packaged
build, so the same command runs at the release gate; there is no numeric
wall-clock target, only the bounded/not-bounded and consistent/inconsistent
verdicts the pure functions in :mod:`cancel_recovery` compute.
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

    guar = sub.add_parser(
        "guarantees",
        help="measure §4.2 cancellation latency + crash recovery via the real worker (M7-T2b)",
    )
    guar.add_argument("--rows", type=int, default=5_000,
                      help="fixture rows — big enough for a RUNNING window on slow CI")
    guar.add_argument("--columns", type=int, default=5)
    guar.add_argument("--cardinality", choices=("low", "medium", "high"), default="medium")
    guar.add_argument("--missingness", type=float, default=0.1)
    guar.add_argument("--distribution", choices=("normal", "skewed"), default="normal")
    guar.add_argument("--seed", type=int, default=7)
    guar.add_argument("--workdir", default=".benchmarks/guarantees")
    guar.add_argument("--out", default=".benchmarks/guarantees/results.json")

    watch = sub.add_parser(
        "watch",
        help="read-only % -done indicator for a long-running ladder (estimate, not a product metric)",
    )
    watch.add_argument("--sizes", default="10000000,100000000", help="same ladder as the baseline run")
    watch.add_argument("--columns", type=int, default=8, help="accepted for symmetry with `baseline`; unused")
    watch.add_argument("--format", dest="fmt", choices=("csv", "parquet"), default="csv")
    watch.add_argument("--mode", default="standard")
    watch.add_argument("--workdir", default=".benchmarks/ladder")
    watch.add_argument("--interval", type=float, default=0.0,
                       help="poll every N seconds until results.json appears (0 = one snapshot)")
    watch.add_argument("--json", action="store_true", dest="as_json")
    watch.add_argument("--pass-name", dest="pass_name", default=None, metavar="NAME",
                       help="track ONE bounded pass (M7-T2c run_pass.sh, e.g. 'top') "
                            "instead of the tier ladder")
    watch.add_argument("--rows", type=int, default=None,
                       help="tier of the tracked pass (default: the last --sizes entry)")
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

    if args.command == "guarantees":
        return _run_guarantees(args)

    if args.command == "watch":
        from data_profiling_engine.benchmark.watch import watch_ladder, watch_pass

        import time as _time

        sizes = tuple(_parse_sizes(args.sizes))
        if args.pass_name:
            rows = args.rows if args.rows is not None else sizes[-1]
            code = watch_pass(
                rows=rows, pass_name=args.pass_name, workdir=args.workdir,
                fmt=args.fmt, mode=args.mode, json_out=args.as_json,
            )
            while code == 1 and args.interval > 0:
                _time.sleep(args.interval)
                code = watch_pass(
                    rows=rows, pass_name=args.pass_name, workdir=args.workdir,
                    fmt=args.fmt, mode=args.mode, json_out=args.as_json,
                )
            return code
        code = watch_ladder(
            sizes=sizes, workdir=args.workdir, fmt=args.fmt, mode=args.mode,
            json_out=args.as_json,
        )
        if args.interval <= 0:
            return code
        while code == 1:  # live polling: one snapshot per tick
            _time.sleep(args.interval)
            code = watch_ladder(
                sizes=sizes, workdir=args.workdir, fmt=args.fmt, mode=args.mode,
                json_out=args.as_json,
            )
        return code

    return 2  # pragma: no cover - argparse enforces the subcommand


def _run_guarantees(args) -> int:
    """M7-T2b: generate a small fixture once, drive both measurements through
    a store-backed :class:`JobManager`, emit a self-describing results JSON.
    Never raises into the CLI — a stuck worker surfaces as an
    ``insufficient_data``/``deadline_exceeded`` verdict rather than a traceback."""
    from data_profiling_engine.benchmark.cancel_recovery import (
        measure_cancellation_latency,
        measure_worker_crash_recovery,
    )
    from data_profiling_engine.benchmark.harness import _machine_context, ensure_fixture
    from data_profiling_engine.bridge.worker.manager import JobManager
    from data_profiling_engine.storage.metadata_store import MetadataStore

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    spec = DatasetSpec(
        rows=args.rows, columns=args.columns, cardinality=args.cardinality,
        missingness=args.missingness, distribution=args.distribution, seed=args.seed,
    )
    input_path, base_config = ensure_fixture(spec, "guarantees", workdir, "csv")
    config_document = json.loads(base_config.read_text(encoding="utf-8"))

    data_root = (workdir / "data_root").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    store = MetadataStore(data_root=data_root)

    cancel = measure_cancellation_latency(
        manager=JobManager(metadata_store=store),
        dataset_id="guarantees_cancel",
        source_path=input_path,
        config_document=config_document,
        run_id="run_cancel",
        allowed_roots=[str(workdir)],
        data_root=str(data_root),
    )
    recovery = measure_worker_crash_recovery(
        manager=JobManager(metadata_store=store),
        store=store,
        dataset_id="guarantees_crash",
        source_path=input_path,
        config_document=config_document,
        run_id="run_crash",
        allowed_roots=[str(workdir)],
        data_root=str(data_root),
    )

    results = {
        "machine": _machine_context(),
        "workload": {
            "rows": args.rows, "columns": args.columns, "cardinality": args.cardinality,
            "missingness": args.missingness, "distribution": args.distribution,
            "seed": args.seed, "fmt": "csv",
        },
        "guarantees": {
            "cancellation_latency": cancel,
            "worker_crash_recovery": recovery,
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"cancellation: {cancel['verdict']} (latency={cancel.get('latency_s')}s)")
    print(f"recovery    : {recovery['verdict']} (detection={recovery.get('crash_detection_s')}s)")
    print(f"results -> {out}")
    # A non-zero exit signals a release-gate regression (verdicts other than
    # the passing ones never silently ship).
    ok = cancel["verdict"] == "bounded" and recovery["verdict"] == "consistent"
    return 0 if ok else 1


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
