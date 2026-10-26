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

:func:`evaluate_rss_plateau` adds the stricter reading the M7-T2a ladder forced
(TESTING §4.3.3): sub-linear growth is *not* the same as bounded growth. The
ladder's 677 MiB → 3 948 MiB for 10M → 100M rows passed ``bounded`` yet fits
rows^0.766, which extrapolates to ~22.5 GiB at 1B rows, so the plateau verdict
fits the growth exponent instead of only comparing endpoint ratios. It is
reported **alongside** ``rss_boundedness``, not instead of it: a ceiling can only
flatten memory above its own value, so the plateau is the stricter diagnostic
(TESTING §4.3) while ``bounded`` stays the binding gate.

Platform note: peak RSS uses POSIX ``resource.getrusage`` (macOS reports bytes,
Linux kilobytes), which is the process's true high-water mark. On Windows
``resource`` is absent, so the measured child falls back to a ``psutil``
self-sampler that polls RSS at :data:`RSS_SAMPLE_INTERVAL_S` and reports the
maximum — a *sampled* peak, honestly labelled ``peak_rss_method =
"psutil_sampled_peak"`` (ADR-015: never present a sampled value as the exact
high-water). If neither ``resource`` nor ``psutil`` is available,
``peak_rss_bytes`` stays ``None`` and the RSS guarantees report
``insufficient_data``; wall-clock + disk still record.
"""
from __future__ import annotations

import json
import math
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

try:  # Windows RSS fallback sampler; optional on every platform
    import psutil as _psutil
except ImportError:  # pragma: no cover - only required where 'resource' is absent
    _psutil = None  # type: ignore[assignment]

#: How often the ``psutil`` fallback samples RSS (seconds). Small enough to
#: catch a plateau over a multi-second DuckDB pass at negligible overhead, but
#: still a *sampling* interval — hence the sampled-peak label, not a true max.
RSS_SAMPLE_INTERVAL_S = 0.05

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


def _peak_rss_method(*, resource_available: bool, psutil_available: bool) -> str:
    """Which mechanism produced (or will produce) ``peak_rss_bytes`` — recorded
    so a Windows *sampled* peak is never mistaken for the POSIX high-water mark
    (ADR-015). Pure + unit-testable without importing either library."""
    if resource_available:
        return "resource.ru_maxrss"
    if psutil_available:
        return "psutil_sampled_peak"
    return "unavailable"


class _RssPeakSampler:
    """Poll this process's RSS (plus any child's) in a background thread and
    report the peak — the Windows stand-in for ``resource.ru_maxrss``.

    DuckDB's Python binding computes in-process, so the measured profile pass
    lives in *this* process and self-sampling suffices; children are summed to
    cover a case that ever spawns one. When ``psutil`` is unavailable the sampler
    is inert and :meth:`stop` returns ``None``, so the caller keeps the honest
    ``unavailable`` path instead of a fabricated zero."""

    def __init__(self, interval_s: float = RSS_SAMPLE_INTERVAL_S) -> None:
        self._interval_s = interval_s
        self._proc = None
        self._peak_bytes = 0
        self._stop_evt = None
        self._thread = None

    def start(self) -> "_RssPeakSampler":
        if _psutil is None:
            return self
        import threading

        self._proc = _psutil.Process()
        self._peak_bytes = 0
        self._stop_evt = threading.Event()

        def _loop() -> None:
            # Sample-then-wait (do-while): a pass shorter than one interval still
            # gets at least one RSS reading. The old wait-then-sample order raced:
            # on Windows the thread never sampled before stop() and the peak came
            # back None (M7-T3, test_rss_sampler_reports_positive_peak).
            while True:
                total = 0
                try:
                    total = self._proc.memory_info().rss or 0
                    for child in self._proc.children(recursive=True):
                        try:
                            total += child.memory_info().rss or 0
                        except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                            pass
                except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                    pass
                if total > self._peak_bytes:
                    self._peak_bytes = total
                if self._stop_evt.wait(self._interval_s):
                    return

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> Optional[int]:
        if _psutil is None or self._thread is None or self._stop_evt is None:
            return None
        self._stop_evt.set()
        self._thread.join(timeout=2 * self._interval_s + 1.0)
        return self._peak_bytes or None


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

    # POSIX: the child self-reports its true high-water via resource.ru_maxrss.
    # Windows (no resource): wrap the same call in a psutil sampled peak.
    sampler = _RssPeakSampler()
    if _resource is None:
        sampler.start()
    t0 = time.perf_counter()
    bundle = profile_dataset(
        "bench_ds", input_path, config, run_id="bench-run",
        result_refs={"profile_json": str(result_ref)},
    )
    wall = time.perf_counter() - t0
    peak_rss_bytes = _peak_rss_bytes() if _resource is not None else sampler.stop()

    payload: Dict[str, object] = {
        "wall_clock_s": round(wall, 4),
        "peak_rss_bytes": peak_rss_bytes,
        "peak_rss_method": _peak_rss_method(
            resource_available=_resource is not None,
            psutil_available=_psutil is not None,
        ),
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
            "peak_rss_method": metrics.get("peak_rss_method"),
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


#: RSS growth exponent (RSS ∝ rows^b) that still counts as a plateau: at b=0.25
#: a 10x data growth costs 1.78x memory and 100x costs 3.16x — bounded in
#: practice. TESTING §4.2 asks that peak memory "stay flat"; this is the
#: quantified form.
PLATEAU_MAX_EXPONENT = 0.25

#: Row count the plateau verdict extrapolates to, as a scale sanity check.
PLATEAU_FORECAST_ROWS = 1_000_000_000


def evaluate_rss_plateau(
    cases: List[CaseResult],
    *,
    max_exponent: float = PLATEAU_MAX_EXPONENT,
    ceiling_bytes: Optional[int] = None,
) -> Dict[str, object]:
    """Fit the RSS-vs-rows growth exponent and judge whether memory plateaus.

    :func:`evaluate_rss_boundedness` only compares the two endpoints and passes
    anything sub-linear; the M7-T2a ladder showed how weak that is (rows^0.65 is
    "bounded" by that test yet reaches ~17 GB at a billion rows). This fits
    ``log2(RSS) = b * log2(rows) + c`` by least squares over every measured point
    and reports ``b`` — the memory cost per decade of data — plus the forecast at
    :data:`PLATEAU_FORECAST_ROWS`. ``*_mb``/``*_gib`` keys follow the harness's existing
    binary-unit convention (``rss_mb_*`` are MiB, as in evaluate_rss_boundedness).

    Verdicts: ``insufficient_data`` (fewer than two measurable points, e.g. on
    Windows), ``over_ceiling`` (the largest run exceeds ``ceiling_bytes``, i.e.
    the buffer-pool limit is not being honored), ``plateau`` when ``b <=
    max_exponent``, otherwise ``growing``."""
    measured = [c for c in cases if c.peak_rss_bytes and c.row_count]
    if len(measured) < 2:
        return {"verdict": "insufficient_data", "n": len(measured)}
    xs = [math.log2(float(c.row_count or 1)) for c in measured]
    ys = [math.log2(float(c.peak_rss_bytes or 1)) for c in measured]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance == 0.0:  # every case at the same row count: no scaling to fit
        return {"verdict": "insufficient_data", "n": n, "reason": "single_row_count"}
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
    intercept = mean_y - slope * mean_x
    smallest = min(measured, key=lambda c: c.row_count or 0)
    largest = max(measured, key=lambda c: c.row_count or 0)
    decade = 10.0 ** slope  # memory cost of ×10 the rows (2**slope would be ×2)
    forecast = 2.0 ** (slope * math.log2(PLATEAU_FORECAST_ROWS) + intercept)
    result: Dict[str, object] = {
        # epsilon so a curve that sits exactly on the boundary is not flipped by
        # float noise in the fit
        "verdict": "plateau" if slope <= max_exponent + 1e-9 else "growing",
        "n": n,
        "growth_exponent": round(slope, 3),
        "max_exponent": max_exponent,
        "rss_multiplier_per_decade": round(decade, 3),
        "rss_mb_smallest": round(smallest.peak_rss_bytes / 1_048_576, 1),  # type: ignore[arg-type]
        "rss_mb_largest": round(largest.peak_rss_bytes / 1_048_576, 1),  # type: ignore[arg-type]
        "forecast_rss_gib_at_1b_rows": round(forecast / 1_073_741_824, 2),
    }
    if ceiling_bytes:
        result["ceiling_mb"] = round(ceiling_bytes / 1_048_576, 1)
        if largest.peak_rss_bytes > ceiling_bytes:  # type: ignore[union-attr]
            result["verdict"] = "over_ceiling"
    return result


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

    guarantees: Dict[str, object] = {
        "rss_boundedness": evaluate_rss_boundedness(cases),
        "rss_plateau": evaluate_rss_plateau(cases, ceiling_bytes=_engine_ceiling_bytes()),
    }

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


def _engine_ceiling_bytes() -> Optional[int]:
    """The buffer-pool ceiling the engine now installs (ADR-021), or ``None`` if
    it cannot be determined — the plateau check then skips the ceiling compare.
    Imported lazily so measuring stays independent of engine internals."""
    try:
        from data_profiling_engine import duckdb_session

        return duckdb_session.default_memory_limit_bytes()
    except Exception:  # noqa: BLE001 - a monitor never fabricates a limit
        return None


def _machine_context() -> Dict[str, object]:
    import multiprocessing

    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": multiprocessing.cpu_count(),
        "total_ram_gb": _total_ram_gb(),
        "rss_measurement": _peak_rss_method(
            resource_available=_resource is not None, psutil_available=_psutil is not None
        ),
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
