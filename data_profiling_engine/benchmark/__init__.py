"""Benchmark subsystem (M7-T1; TESTING_BENCHMARKS §4).

Synthetic workload generator + measurement harness that drives the same engine
spine the ``profiler-cli`` and the M5 worker drive
(:func:`orchestration.rollup.profile_dataset`). Nothing here runs in the shipped
app; it is release-gate / scheduled tooling (TESTING §1 benchmark layer), not a
per-commit unit test — the heavy 10M–100M matrix is invoked explicitly via
``python -m data_profiling_engine.benchmark``.
"""
from __future__ import annotations

from data_profiling_engine.benchmark.generator import (
    DatasetSpec,
    generate_dataset,
    write_dataset_config,
)
from data_profiling_engine.benchmark.harness import (
    CaseResult,
    evaluate_rss_boundedness,
    evaluate_sampled_cheaper,
    measure_case,
    run_suite,
)

__all__ = [
    "DatasetSpec",
    "generate_dataset",
    "write_dataset_config",
    "CaseResult",
    "measure_case",
    "run_suite",
    "evaluate_rss_boundedness",
    "evaluate_sampled_cheaper",
]
