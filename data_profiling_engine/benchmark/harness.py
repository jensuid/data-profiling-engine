"""Benchmark measurement harness (M7-T1; TESTING_BENCHMARKS §4.2 / §4.3).

Drives the real engine spine (:func:`orchestration.rollup.profile_dataset` —
the exact entry the ``profiler-cli`` and M5 worker use) against generated
fixtures and captures the §4.2 metrics that matter for the architecture claim:

- **wall-clock** — total run, parent-timed (includes connector snapshot + the
  full discovery → metrics → quality → findings → rollup path).
- **peak RSS** — of the profiling process. Measured in a *fresh child process*
  per case so ``ru_maxrss`` is that run's high-water mark, not the maximum of
  every run in the process tree — the only way the §4.3 "RSS stays flat as
  rows grow" curve comparison is honest.
- **disk** — source-file bytes (the profiling run itself streams; DuckDB spills
  to its own temp, not materialised in Python).

Every case runs out-of-process for the same reason the app runs heavy work off
the UI thread (ADR-006): a crash or a memory blow-up in one measured run must
not contaminate the next, and the number we report must be attributable to that
single run.

The two **binding structural guarantees** (TESTING §4.3) are computed here as
pure functions so a CI job can gate on them without a wall-clock target:

- :func:`evaluate_rss_boundedness` — peak RSS must grow far slower than row
  count (sub-linear; a full-materialisation bug grows ~linearly).
- :func:`evaluate_sampled_cheaper` — the degrade path (sampled/approximate) must
  be strictly faster than exact on the same large fixture.

Platform note: peak RSS uses POSIX ``resource.getrusage`` (macOS reports bytes,
Linux kilobytes). On Windows ``resource`` is absent → ``peak_rss_bytes`` is
``None`` and the RSS guarantee is skipped there until the packaged Windows
baseline (deferred to M7/Phase-3 per STATE); wall-clock + disk still record.
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:  # POSIX only; absent on Windows (see module docstring)
    import resource as _resource
except ImportError:  # pragma: no cover - exercised only on Windows
    _resource = None  # type: ignore[assignment]

from data_profiling_engine.benchmark.generator import (
    DatasetSpec,
    generate_dataset,
    spec_dict,
    write_dataset_config,
)

_PKG = "data_profiling_engine.benchmark"


# -- child-process measurement ------------------------------------------------

def _peak_rss_bytes() -> Optional[int]:
    """High-water RSS of *this* process, normalised to bytes.

    macOS ``ru_maxrss`` is already bytes; Linux reports kilobytes (multiply by
    1024). ``None`` on platforms without ``resource`` (Windows)."""
    if _resource is None:
        return None
    peak = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(peak)
    return int(peak) * 1024


def measure_and_emit(input_path: "Path | str", config_path: "Path | str", emit_path: "Path | str") -> Dict[str, object]:
    """Run one profiling pass in the current (child) process and write the
    metrics JSON to ``emit_path``. This is the ``measure`` subcommand body — it
    exists so the parent can time a fully isolated run and read back its peak
    RSS without any cross-run contamination."""
    from data_profiling_engine.cli import load_config
    from data_profiling_engine.orchestration.rollup import profile_dataset

    config = load_config(config_path)
    out = Path(emit_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    result_ref = out.with_suffix(".profile.json")

    t0 = time.perf_counter()
    bundle = profile_dataset(
        "bench_ds", input_path, config, run_id="bench-run",
        result_refs={"profile_json": str(result_ref)},
    )
    wall = time.perf_counter() - t0

    payload: Dict[str, object] = {
        "wall_clock_s": round(wall, 4),
        "peak_rss_bytes": _peak_rss_bytes(),
        "row_count": _row_count(bundle),
        "column_count": bundle.profile.column_count,
        "mode": str(config.profiling.mode),
        "sampling_enabled": bool(config.profiling.sampling and config.profiling.sampling.enabled),
        "run_status": str(bundle.run.status) if bundle.run.status is not None else None,
        "n_errors": len(bundle.errors),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _row_count(bundle) -> Optional[int]:
    rc = bundle.profile.row_count
    return getattr(rc, "value", None) if rc is not None else None


# -- parent-process case runner ----------------------------------------------

@dataclass
class CaseResult:
    """One measured benchmark case: the workload that ran, the method the
    planner used, and the §4.2 metrics captured."""

    label: str
    spec: Dict[str, object]
    fmt: str
    mode: str
    sampled: bool
    wall_clock_s: float
    peak_rss_bytes: Optional[int]
    source_bytes: int
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    n_errors: int = 0
    extra: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "label": self.label,
            "spec": self.spec,
            "fmt": self.fmt,
            "mode": self.mode,
            "sampled": self.sampled,
            "wall_clock_s": self.wall_clock_s,
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_rss_mb": (
                round(self.peak_rss_bytes / 1_048_576, 1)
                if self.peak_rss_bytes is not None else None
            ),
            "source_bytes": self.source_bytes,
            "source_mb": round(self.source_bytes / 1_048_576, 1),
            "row_count": self.row_count,
            "column_count": self.column_count,
            "n_errors": self.n_errors,
            "extra": self.extra,
        }


def _write_overridden_config(
    spec: DatasetSpec, base_config: Path, *, mode: str, sampling: Optional[Dict[str, object]]
) -> Path:
    """Load the base §9 config the generator wrote and layer the run's
    ``profiling.mode`` / optional ``profiling.sampling`` window onto it, then
    re-validate through the CLI loader before returning the path (invalid config
    is rejected here, not inside the timed child — AGENTS §5)."""
    document = json.loads(base_config.read_text(encoding="utf-8"))
    document["profiling"]["mode"] = mode
    if sampling is not None:
        document["profiling"]["sampling"] = sampling
    else:
        document["profiling"].pop("sampling", None)
    path = base_config.with_name(f"{base_config.stem}.{mode}{'_sampled' if sampling else ''}.json")
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    from data_profiling_engine.schemas import validate
    errors = validate("dataset_config.schema.json", document)
    if errors:
        raise ValueError("overridden benchmark config failed schema validation: " + "; ".join(errors))
    return path


def fixture_paths(label: str, fmt: str, workdir: "Path | str") -> Tuple[Path, Path]:
    """The deterministic ``(input, base_config)`` paths a label's fixture lives
    at, so two cases (e.g. exact vs sampled) can share ONE generated file
    instead of regenerating the same large fixture twice."""
    workdir = Path(workdir)
    return workdir / f"{label}.{fmt}", workdir / f"{label}.config.json"


def ensure_fixture(spec: DatasetSpec, label: str, workdir: "Path | str", fmt: str) -> Tuple[Path, Path]:
    """Generate a fixture + base §9 config once and return their paths."""
    input_path, base_config = fixture_paths(label, fmt, workdir)
    Path(workdir).mkdir(parents=True, exist_ok=True)
    generate_dataset(spec, input_path, fmt=fmt)
    write_dataset_config(spec, base_config)
    return input_path, base_config


def measure_case(
    *,
    label: str,
    spec: DatasetSpec,
    workdir: "Path | str",
    fmt: str = "csv",
    mode: str = "standard",
    sampling: Optional[Dict[str, object]] = None,
    input_path: Optional["Path | str"] = None,
    base_config: Optional["Path | str"] = None,
) -> CaseResult:
    """Run one *isolated child* profiling pass and return its metrics.

    The child is invoked via ``python -m data_profiling_engine.benchmark
    measure`` so peak RSS is scoped to this single run (see module docstring).
    ``sampling`` is the §9 window dict (``{enabled, strategy, size, seed}``);
    pass ``None`` for the exact/approximate reference run. When
    ``input_path``/``base_config`` are given the fixture is reused as-is (the
    exact-vs-sampled pair shares one file); otherwise the fixture is generated
    at ``label``-derived paths."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    if input_path is None or base_config is None:
        input_path, base_config = ensure_fixture(spec, label, workdir, fmt)
    else:
        input_path, base_config = Path(input_path), Path(base_config)

    run_config = _write_overridden_config(spec, base_config, mode=mode, sampling=sampling)

    emit = workdir / f"{label}.measure.json"
    if emit.exists():
        emit.unlink()
    cmd = [sys.executable, "-m", _PKG, "measure",
           "--input", str(input_path), "--config", str(run_config), "--emit", str(emit)]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    parent_wall = time.perf_counter() - t0
    if proc.returncode != 0 or not emit.exists():
        raise RuntimeError(
            f"benchmark measure failed (rc={proc.returncode}) for {label}: "
            f"{proc.stderr.strip()[:500] or proc.stdout.strip()[:500]}"
        )
    metrics = json.loads(emit.read_text(encoding="utf-8"))
    return CaseResult(
        label=label,
        spec=spec_dict(spec),
        fmt=fmt,
        mode=mode,
        sampled=sampling is not None,
        wall_clock_s=float(metrics["wall_clock_s"]),
        peak_rss_bytes=metrics.get("peak_rss_bytes"),
        source_bytes=input_path.stat().st_size,
        row_count=metrics.get("row_count"),
        column_count=metrics.get("column_count"),
        n_errors=int(metrics.get("n_errors", 0)),
        extra={
            "parent_wall_clock_s": round(parent_wall, 4),
            "process_startup_overhead_s": round(parent_wall - float(metrics["wall_clock_s"]), 4),
            "run_status": metrics.get("run_status"),
            "planner_sampling_enabled": metrics.get("sampling_enabled"),
        },
    )


# -- structural guarantee evaluation (pure; unit-testable) -------------------

def evaluate_rss_boundedness(cases: List[CaseResult]) -> Dict[str, object]:
    """Compare the largest vs smallest RSS across a row-scaling series.

    Returns the row-count growth factor vs the RSS growth factor; the §4.3
    structural guarantee holds when RSS grows **sub-linearly** in row count —
    ``rss_ratio < row_ratio`` by a clear margin (a full-materialisation bug
    tracks ``row_ratio`` ~1:1). Cases with ``peak_rss_bytes is None`` (Windows)
    are excluded, so the verdict is ``insufficient_data`` until the packaged
    Windows baseline runs."""
    measured = [c for c in cases if c.peak_rss_bytes and c.row_count]
    if len(measured) < 2:
        return {"verdict": "insufficient_data", "n": len(measured)}
    small = min(measured, key=lambda c: c.row_count or 0)
    large = max(measured, key=lambda c: c.row_count or 0)
    row_ratio = (large.row_count or 1) / max(1, small.row_count or 1)
    rss_ratio = large.peak_rss_bytes / max(1, small.peak_rss_bytes)
    verdict = "bounded" if rss_ratio < row_ratio else "linear_growth"
    return {
        "verdict": verdict,
        "rows_small": small.row_count,
        "rows_large": large.row_count,
        "row_ratio": round(row_ratio, 2),
        "rss_mb_small": round(small.peak_rss_bytes / 1_048_576, 1),
        "rss_mb_large": round(large.peak_rss_bytes / 1_048_576, 1),
        "rss_ratio": round(rss_ratio, 3),
    }


def evaluate_sampled_cheaper(exact: CaseResult, degrade: CaseResult) -> Dict[str, object]:
    """Assert the degrade path (sampled/approximate) is strictly faster in
    wall-clock than the exact reference on the same fixture (§4.3 binding)."""
    saved = exact.wall_clock_s - degrade.wall_clock_s
    return {
        "verdict": "cheaper" if degrade.wall_clock_s < exact.wall_clock_s else "not_cheaper",
        "exact_wall_clock_s": exact.wall_clock_s,
        "degrade_wall_clock_s": degrade.wall_clock_s,
        "saved_s": round(saved, 4),
        "speedup": round(exact.wall_clock_s / max(1e-9, degrade.wall_clock_s), 2),
    }


# -- suite orchestration ------------------------------------------------------

_DEFAULT_SAMPLING = {"enabled": True, "strategy": "reservoir", "size": 200_000, "seed": 1337}


def run_suite(
    *,
    sizes: Tuple[int, ...],
    columns: int = 8,
    fmt: str = "csv",
    cardinality: str = "medium",
    missingness: float = 0.1,
    distribution: str = "normal",
    mode: str = "standard",
    workdir: "Path | str",
    degrade_vs_exact: bool = True,
) -> Dict[str, object]:
    """Run a row-scaling series (§4.3 RSS curve) and, at the largest size, an
    exact-vs-sampled wall-clock pair. Returns a JSON-serialisable results doc
    carrying hardware context so the recorded baseline is self-describing
    (TESTING §4.4: numbers + hardware are product evidence).

    ``sizes`` is the row-count ladder; keep them a small set — the heavy
    10M–100M ladder is the scheduled/release run, invoked explicitly here."""
    workdir = Path(workdir)
    cases: List[CaseResult] = []
    fixtures: Dict[int, Tuple[Path, Path]] = {}

    for rows in sizes:
        spec = DatasetSpec(
            rows=rows, columns=columns, cardinality=cardinality,
            missingness=missingness, distribution=distribution,
        )
        label = f"{fmt}_{rows}_std"
        input_path, base_config = ensure_fixture(spec, label, workdir, fmt)
        fixtures[rows] = (input_path, base_config)
        cases.append(measure_case(
            label=label, spec=spec, workdir=workdir,
            fmt=fmt, mode=mode, sampling=None,
            input_path=input_path, base_config=base_config,
        ))

    guarantees: Dict[str, object] = {"rss_boundedness": evaluate_rss_boundedness(cases)}

    if degrade_vs_exact and sizes:
        big = max(sizes)
        spec = DatasetSpec(
            rows=big, columns=columns, cardinality=cardinality,
            missingness=missingness, distribution=distribution,
        )
        shared_input, shared_config = fixtures[big]
        exact = measure_case(
            label=f"{fmt}_{big}_exact", spec=spec, workdir=workdir,
            fmt=fmt, mode="deep", sampling=None,
            input_path=shared_input, base_config=shared_config,
        )
        sampled = measure_case(
            label=f"{fmt}_{big}_sampled", spec=spec, workdir=workdir,
            fmt=fmt, mode="standard", sampling=dict(_DEFAULT_SAMPLING),
            input_path=shared_input, base_config=shared_config,
        )
        cases.extend([exact, sampled])
        guarantees["sampled_cheaper_than_exact"] = evaluate_sampled_cheaper(exact, sampled)

    return {
        "machine": _machine_context(),
        "workload": {
            "fmt": fmt, "columns": columns, "cardinality": cardinality,
            "missingness": missingness, "distribution": distribution,
            "sizes": list(sizes), "sampling": _DEFAULT_SAMPLING,
        },
        "cases": [c.to_dict() for c in cases],
        "guarantees": guarantees,
    }


def _machine_context() -> Dict[str, object]:
    import multiprocessing

    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": multiprocessing.cpu_count(),
        "total_ram_gb": _total_ram_gb(),
        "rss_measurement": "resource.getrusage(RUSAGE_SELF).ru_maxrss (child process)" if _resource else "unavailable on this platform",
    }


def _total_ram_gb() -> Optional[float]:
    """Best-effort total physical RAM in GiB for the machine-context block.

    macOS does not expose ``SC_PHYS_PAGES`` via ``os.sysconf``, so try ``sysctl
    hw.memsize`` (bytes) there; fall back to ``/proc/meminfo`` on Linux; ``None``
    when neither is available (never a guessed number)."""
    try:
        if sys.platform == "darwin":
            raw = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
            if raw.isdigit():
                return round(int(raw) / 1_073_741_824, 1)
        else:
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) * 1024 / 1_073_741_824, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


__all__ = [
    "CaseResult",
    "measure_and_emit",
    "measure_case",
    "ensure_fixture",
    "fixture_paths",
    "run_suite",
    "evaluate_rss_boundedness",
    "evaluate_sampled_cheaper",
]
