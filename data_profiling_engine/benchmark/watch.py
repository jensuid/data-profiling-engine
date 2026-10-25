"""Read-only progress watcher for a running benchmark ladder (M7-T2a).

The heavy 10M–100M RSS-curve ladder (TESTING §4.3 structural bullet 1) runs for
hours and the measured child (``benchmark measure`` → ``profile_dataset``)
emits no step signal — it is the in-process engine spine, not the store-backed
worker job that carries §5.5 progress events. So this watcher derives progress
from what the harness *does* leave behind, without touching the running process:

- **filesystem events** — ``run_suite`` runs tiers strictly in order
  (generate fixture → measure), so the mtime chain of
  ``{label}.{fmt}`` / ``{label}.config.json`` / ``{label}.config.{mode}.json`` /
  ``{label}.measure.json`` brackets every stage start and end;
- **completed measurements** — a finished stage's authoritative wall-clock comes
  from its own ``measure.json`` (``wall_clock_s``), and calibrates the estimate
  for the still-running stages (s/row for measurement, s/byte for generation);
- **the parent process age** (``ps`` elapsed time) brackets the very first
  generation stage, which has no predecessor on disk.

The percentage for an unfinished stage is therefore an **estimate** extrapolated
from measured stages — never a claimed exact value (ADR-015 discipline: an
approximate number is labelled as such, here via ``basis``). Nothing here is a
product metric; the product evidence is the ``results.json`` the ladder writes.

Usage (``python -m data_profiling_engine.benchmark watch …``):

    … watch --workdir .benchmarks/ladder --sizes 10000000,100000000
    … watch … --interval 30          # live polling, one line per tick
    … watch … --json                 # machine-readable

Single-pass mode (M7-T2c, ``.benchmarks/ladder/run_pass.sh``):

    … watch --pass top --rows 100000000 [--interval 60]

A bounded pass has no tier ladder to reconstruct, so the same discipline is
applied to a much shorter evidence chain: the pass verdict JSON
(``bounded_<N>m_<name>.json``) means *done*, a live ``measure_bounded.py``
process means *running*, and a child ``*_bounded.measure.json`` without a
verdict means the driver **died** — reported as ``stalled``, never as done. The
estimate is calibrated on the *same tier's* measured wall-clock from an earlier
ladder run, so it is a reference, not a guess. The pass mode additionally shows
the live size of the DuckDB scratch directory: that is the one signal that says
whether the ADR-021 ceiling is actually binding during the run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

#: Fallback rates from the recorded dev-mode baseline (TESTING §4.3.1:
#: 1 000 000 rows / 143.7 s standard mode; the 10M tier generated 642 MB in
#: ~89 s). Used only until the run itself has produced a measured stage to
#: calibrate on, and clearly labelled as an estimate in ``basis``.
_FALLBACK_MEASURE_S_PER_ROW = 143.7 / 1_000_000
_APPROX_BYTES_PER_ROW = 64.0
_FALLBACK_GEN_S_PER_BYTE = 89.0 / (10_000_000 * _APPROX_BYTES_PER_ROW)

_DONE, _ACTIVE, _PENDING, _UNKNOWN = "done", "active", "pending", "unknown"

#: Single-pass statuses (M7-T2c). ``stalled`` is a first-class state: a pass whose
#: driver died must never be reported as finished.
_PASS_RUNNING, _PASS_DONE = "running", "done"
_PASS_STALLED, _PASS_NOT_STARTED = "stalled", "not_started"


@dataclass
class LadderStage:
    """One harness stage (a tier's fixture generation or its measurement)."""

    label: str
    rows: int
    kind: str  # "generate" | "measure"
    status: str  # "done" | "active" | "pending" | "unknown"
    est_s: Optional[float] = None
    elapsed_s: Optional[float] = None
    basis: str = "estimate"  # "measured" | "estimate" | "extrapolated"
    detail: Dict[str, object] = field(default_factory=dict)

    @property
    def pct(self) -> Optional[float]:
        """Completion percentage, or ``None`` when it cannot be determined."""
        if self.status == _DONE:
            return 100.0
        if self.status != _ACTIVE or not self.est_s:
            return None
        return round(100.0 * min(self.elapsed_s or 0.0, self.est_s) / self.est_s, 1)

    def to_dict(self) -> Dict[str, object]:
        return {
            "label": self.label, "rows": self.rows, "kind": self.kind,
            "status": self.status, "pct": self.pct,
            "est_s": round(self.est_s, 1) if self.est_s else None,
            "elapsed_s": round(self.elapsed_s, 1) if self.elapsed_s else None,
            "basis": self.basis, "detail": self.detail,
        }


def tier_label(rows: int, fmt: str = "csv") -> str:
    """The fixture label ``run_suite`` uses for a row count (must stay in sync
    with :func:`data_profiling_engine.benchmark.harness.run_suite`)."""
    return f"{fmt}_{rows}_std"


def tier_paths(label: str, fmt: str, mode: str, workdir: Path) -> Dict[str, Path]:
    """The on-disk markers a tier writes, in stage order."""
    return {
        "fixture": workdir / f"{label}.{fmt}",
        "base_config": workdir / f"{label}.config.json",
        "run_config": workdir / f"{label}.config.{mode}.json",
        "measure": workdir / f"{label}.measure.json",
    }


def parse_ps_etime(raw: str) -> Optional[int]:
    """Parse a BSD ``ps -o etime=`` field ([DD-]HH:MM:SS) into seconds.

    ``None`` for anything unparseable — a monitor never guesses a duration."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        days = 0
        if "-" in raw:
            days_part, raw = raw.split("-", 1)
            days = int(days_part)
        parts = [int(p) for p in raw.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        h, m, s = parts[-3:]
        return ((days * 24 + h) * 60 + m) * 60 + s
    except ValueError:
        return None


def process_age_seconds(pattern: str) -> Optional[int]:
    """Age of the first live process whose command line contains ``pattern``.

    Returns ``None`` when no such process exists (the run finished, or was never
    started, or ``ps`` is unavailable). Uses ``pgrep``/``ps``, both present on
    macOS and Linux; on Windows ``pgrep`` is absent → ``None`` and the watcher
    degrades to mtime-only information rather than guessing."""
    try:
        found = subprocess.run(["pgrep", "-f", pattern],
                               capture_output=True, text=True, timeout=10)
        pids = [p for p in found.stdout.split() if p.strip().isdigit()]
        if not pids:
            return None
        out = subprocess.run(["ps", "-p", pids[0], "-o", "etime="],
                             capture_output=True, text=True, timeout=10)
        return parse_ps_etime(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _wall_from_measure(path: Path) -> Optional[float]:
    try:
        return float(json.loads(path.read_text(encoding="utf-8"))["wall_clock_s"])
    except (OSError, ValueError, KeyError):
        return None


def compute_overall(stages: Sequence[LadderStage]) -> Dict[str, object]:
    """Aggregate stages into an overall percentage (time-weighted).

    Every stage contributes its *estimated* duration as weight, so a stage whose
    estimate is missing contributes nothing rather than skewing the result.
    ``overall_pct`` is ``None`` when nothing is weightable (e.g. a fresh run with
    no completed stage and no live process)."""
    weightable = [s for s in stages if s.est_s]
    total = sum(s.est_s or 0.0 for s in weightable)
    if total <= 0:
        return {"overall_pct": None, "est_total_s": None, "eta_s": None}
    done = sum(
        (s.est_s or 0.0) if s.status == _DONE
        else min(s.elapsed_s or 0.0, s.est_s or 0.0) if s.status == _ACTIVE else 0.0
        for s in weightable
    )
    remaining = max(0.0, total - done)
    return {
        "overall_pct": round(100.0 * done / total, 1),
        "est_total_s": round(total, 1),
        "eta_s": round(remaining, 1),
    }


def build_stages(
    *,
    sizes: Sequence[int],
    workdir: "Path | str",
    fmt: str = "csv",
    mode: str = "standard",
    now: Optional[float] = None,
    run_started_age_s: Optional[float] = None,
) -> List[LadderStage]:
    """Reconstruct the per-tier stage list of a ladder run from the workdir.

    ``sizes`` is the ladder as invoked (any order; ascending is assumed for the
    stage sequence because ``run_suite`` iterates the given tuple — the caller
    passes it in invocation order). Calibration rates are learned from what has
    already completed on disk, so early tiers carry coarse estimates and later
    tiers get progressively tighter ones."""
    workdir = Path(workdir)
    now = time.time() if now is None else now
    stages: List[LadderStage] = []

    gen_s_per_byte = _FALLBACK_GEN_S_PER_BYTE
    measure_s_per_row = _FALLBACK_MEASURE_S_PER_ROW
    prev_end: Optional[float] = (now - run_started_age_s) if run_started_age_s else None

    for idx, rows in enumerate(sizes):
        label = tier_label(rows, fmt)
        p = tier_paths(label, fmt, mode, workdir)
        fixture, base_cfg, run_cfg, measure = (
            p["fixture"], p["base_config"], p["run_config"], p["measure"])

        fixture_exists = fixture.exists()
        fixture_bytes = fixture.stat().st_size if fixture_exists else 0
        gen_done = fixture_exists and base_cfg.exists()
        measure_done = measure.exists()
        fixture_mtime, base_cfg_mtime, run_cfg_mtime = (_mtime(fixture), _mtime(base_cfg), _mtime(run_cfg))
        measure_mtime = _mtime(measure)

        # -- generate stage ------------------------------------------------
        gen_start = prev_end if idx else (prev_end or base_cfg_mtime)
        if gen_done:
            gen_elapsed = (
                max(0.0, base_cfg_mtime - gen_start)
                if gen_start is not None and base_cfg_mtime is not None else None
            )
            gen_est = gen_elapsed if gen_elapsed else (fixture_bytes * gen_s_per_byte)
            if gen_elapsed and fixture_bytes:
                gen_s_per_byte = gen_elapsed / fixture_bytes
            gen = LadderStage(label, rows, "generate", _DONE, est_s=gen_est,
                              elapsed_s=gen_est,
                              basis="measured" if gen_elapsed else "extrapolated",
                              detail={"fixture_bytes": fixture_bytes,
                                      "fixture_mb": round(fixture_bytes / 1_048_576, 1)})
        elif fixture_exists:
            est = fixture_bytes * gen_s_per_byte if fixture_bytes else rows * _APPROX_BYTES_PER_ROW * gen_s_per_byte
            elapsed = (now - gen_start) if gen_start is not None else None
            gen = LadderStage(label, rows, "generate", _ACTIVE, est_s=est, elapsed_s=elapsed,
                              basis="extrapolated" if elapsed is not None else "estimate",
                              detail={"fixture_bytes": fixture_bytes,
                                      "fixture_mb": round(fixture_bytes / 1_048_576, 1)})
        else:
            expected = rows * _APPROX_BYTES_PER_ROW
            gen = LadderStage(label, rows, "generate", _PENDING, est_s=expected * gen_s_per_byte,
                              basis="extrapolated", detail={"expected_bytes": int(expected)})
        stages.append(gen)

        # -- measure stage -------------------------------------------------
        if measure_done:
            wall = _wall_from_measure(measure)
            est = wall if wall else measure_s_per_row * rows
            if wall:
                measure_s_per_row = wall / rows
            m = json.loads(measure.read_text(encoding="utf-8"))
            stages.append(LadderStage(label, rows, "measure", _DONE, est_s=est, elapsed_s=est,
                                      basis="measured",
                                      detail={"wall_clock_s": wall,
                                              "peak_rss_mb": round(
                                                  (m.get("peak_rss_bytes") or 0) / 1_048_576, 1)
                                                  if m.get("peak_rss_bytes") else None,
                                              "run_status": m.get("run_status")}))
        elif gen_done and run_cfg_mtime is not None:
            est = measure_s_per_row * rows
            stages.append(LadderStage(label, rows, "measure", _ACTIVE, est_s=est,
                                      elapsed_s=max(0.0, now - run_cfg_mtime),
                                      basis="extrapolated",
                                      detail={"s_per_row_ref": measure_s_per_row,
                                              "est_s": round(est, 1),
                                              "est_display_s": round(est, 1)}))
        elif gen_done:
            stages.append(LadderStage(label, rows, "measure", _UNKNOWN,
                                      basis="estimate", detail={"why": "run config not yet written"}))
        else:
            stages.append(LadderStage(label, rows, "measure", _PENDING,
                                      est_s=measure_s_per_row * rows, basis="extrapolated",
                                      detail={}))

        prev_end = measure_mtime or base_cfg_mtime or fixture_mtime

    return stages


def _humanize(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _bar(pct: Optional[float], width: int = 26) -> str:
    if pct is None:
        return "░" * width
    filled = int(round(width * pct / 100.0))
    return "▓" * filled + "░" * (width - filled)


def format_stages(stages: Sequence[LadderStage], summary: Dict[str, object],
                  *, workdir: Path, results_done: bool) -> str:
    lines = [
        f"ladder @ {workdir}  ({datetime.now().astimezone().strftime('%H:%M:%S')})  "
        f"percentages are TIME ESTIMATES extrapolated from measured stages",
    ]
    for s in stages:
        pct = s.pct
        seg = f"{pct:>5.1f}%" if pct is not None else "   -  "
        extra = ""
        if s.kind == "measure" and s.status == _DONE:
            extra = (f"wall={s.detail.get('wall_clock_s')}s rss={s.detail.get('peak_rss_mb')}MB "
                     f"{s.detail.get('run_status')}")
        elif s.kind == "measure" and s.status == _ACTIVE:
            extra = f"elapsed={_humanize(s.elapsed_s)} / est={_humanize(s.est_s)}"
        elif s.kind == "generate" and s.detail.get("fixture_mb"):
            extra = f"fixture={s.detail['fixture_mb']}MB"
        lines.append(f"  {s.label:<20} {s.kind:<9} {s.status:<8} {seg} {_bar(pct, 12)} {extra}")
    overall = summary.get("overall_pct")
    bar = _bar(overall)
    tail = "RUN COMPLETE" if results_done else f"eta ~{_humanize(summary.get('eta_s'))}"
    lines.append(f"  overall {('%5.1f%%' % overall) if overall is not None else '  ?  '} "
                 f"{bar}  {tail}")
    return "\n".join(lines)


def watch_ladder(
    *,
    sizes: Sequence[int],
    workdir: "Path | str",
    fmt: str = "csv",
    mode: str = "standard",
    json_out: bool = False,
) -> int:
    """Print one progress snapshot of a ladder run.

    Exit-code convention the CLI polls on: ``0`` the run finished
    (``results.json`` written), ``1`` it is still in flight, ``2`` no live
    ``baseline`` process and no results — i.e. it crashed or was interrupted, or
    the platform has no ``pgrep`` to tell (Windows) in which case the watcher
    keeps reporting ``1`` from the on-disk evidence instead of guessing."""
    workdir = Path(workdir)
    results = workdir / "results.json"
    finished = results.exists()
    age = None if finished else process_age_seconds("data_profiling_engine.benchmark baseline")
    stages = build_stages(sizes=sizes, workdir=workdir, fmt=fmt, mode=mode,
                          run_started_age_s=age)
    summary = compute_overall(stages)
    running = age is not None or (age is None and not finished and not shutil.which("pgrep"))
    payload = {
        "workdir": str(workdir), "sizes": list(sizes), "mode": mode, "fmt": fmt,
        "finished": finished, "live_process": age is not None,
        "stages": [s.to_dict() for s in stages],
        **summary,
    }
    if json_out:
        print(json.dumps(payload, indent=2), flush=True)
    else:
        # flush: ``--interval`` is normally piped/redirected to a log, where
        # block buffering would hide the indicator until the buffer fills.
        print(format_stages(stages, summary, workdir=workdir, results_done=finished), flush=True)
    if finished:
        return 0
    return 1 if running else 2


# -- single bounded pass (M7-T2c) -------------------------------------------


@dataclass
class PassProgress:
    """Progress of ONE bounded pass (``measure_bounded.py``, driven by
    ``.benchmarks/ladder/run_pass.sh``)."""

    rows: int
    pass_name: str
    status: str  # "running" | "done" | "stalled" | "not_started"
    elapsed_s: Optional[float] = None
    est_s: Optional[float] = None
    basis: str = "estimate"  # "measured" | "reference" | "estimate"
    spill_bytes: Optional[int] = None
    detail: Dict[str, object] = field(default_factory=dict)

    @property
    def pct(self) -> Optional[float]:
        """Completion percentage, or ``None`` when it cannot be determined."""
        if self.status == _PASS_DONE:
            return 100.0
        if self.status != _PASS_RUNNING or not self.est_s:
            return None
        return round(100.0 * min(self.elapsed_s or 0.0, self.est_s) / self.est_s, 1)

    def to_dict(self) -> Dict[str, object]:
        return {
            "rows": self.rows, "pass_name": self.pass_name, "status": self.status,
            "pct": self.pct,
            "elapsed_s": round(self.elapsed_s, 1) if self.elapsed_s else None,
            "est_s": round(self.est_s, 1) if self.est_s else None,
            "basis": self.basis, "spill_bytes": self.spill_bytes, "detail": self.detail,
        }


def pass_output_path(rows: int, pass_name: str, workdir: "Path | str") -> Path:
    """The verdict file ``run_pass.sh`` names for a pass (must stay in sync with
    that script's ``--out`` argument)."""
    return Path(workdir) / f"bounded_{rows // 1_000_000}m_{pass_name}.json"


def dir_bytes(path: Optional["Path | str"]) -> Optional[int]:
    """Total bytes of the files under ``path`` — a live spill probe.

    ``None`` when the directory does not exist, so an absent workspace reads as
    *unknown*, never as *zero bytes spilled*."""
    if path is None:
        return None
    root = Path(path)
    if not root.is_dir():
        return None
    total = 0
    for entry in root.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue  # a spill block DuckDB deleted mid-walk is not an error
    return total


def resolve_spill_dir() -> Optional[Path]:
    """Where the described pass spills: ``DPE_DUCKDB_TEMP_DIR`` or the engine
    default (ADR-021). Resolved **without creating anything** — a monitor must
    not change the state it observes."""
    try:
        from data_profiling_engine import duckdb_session
        from data_profiling_engine.storage import paths
    except ImportError:  # pragma: no cover - package is always importable here
        return None
    raw = os.environ.get(duckdb_session.TEMP_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    return paths.default_temp_root() / duckdb_session.SCRATCH_SUBDIR


def build_pass_progress(
    *,
    rows: int,
    pass_name: str,
    workdir: "Path | str" = ".benchmarks/ladder",
    fmt: str = "csv",
    mode: str = "standard",
    now: Optional[float] = None,
    process_age_s: Optional[int] = None,
    spill_bytes: Optional[int] = None,
) -> PassProgress:
    """Reconstruct one pass's state from its (short) on-disk evidence chain.

    Precedence: verdict file → live process → child emit without verdict
    (``stalled``: the driver died) → ``not_started``. The estimate for a running
    pass is the **same tier's** measured wall-clock from an earlier ladder run
    (identical fixture and knobs), labelled ``reference`` — a monitor never
    invents a duration (ADR-015 discipline)."""
    workdir = Path(workdir)
    now = time.time() if now is None else now
    label = tier_label(rows, fmt)
    out = pass_output_path(rows, pass_name, workdir)
    emit = workdir / f"{label}_bounded.measure.json"
    reference = _wall_from_measure(workdir / f"{label}.measure.json")

    if out.exists():
        try:
            payload = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        wall = payload.get("wall_clock_s")
        rss = payload.get("peak_rss_bytes")
        return PassProgress(
            rows=rows, pass_name=pass_name, status=_PASS_DONE,
            elapsed_s=wall, est_s=wall if wall else reference, basis="measured",
            spill_bytes=spill_bytes,
            detail={
                "wall_clock_s": wall,
                "peak_rss_mib": round(rss / 1_048_576, 1) if rss else None,
                "n_errors": payload.get("n_errors"),
                "run_status": (payload.get("extra") or {}).get("run_status"),
                "ceilings": payload.get("ceilings") or {},
                "output": str(out),
            },
        )

    if process_age_s is not None:
        return PassProgress(
            rows=rows, pass_name=pass_name, status=_PASS_RUNNING,
            elapsed_s=float(process_age_s), est_s=reference,
            basis="reference" if reference else "estimate", spill_bytes=spill_bytes,
            detail={
                "child_measured": emit.exists(),
                "reference_s": reference,
                "output": str(out),
            },
        )

    stalled = emit.exists()
    return PassProgress(
        rows=rows, pass_name=pass_name,
        status=_PASS_STALLED if stalled else _PASS_NOT_STARTED,
        est_s=reference, basis="reference" if reference else "estimate",
        spill_bytes=spill_bytes,
        detail={"child_measured": stalled, "output": str(out)},
    )


def format_pass(progress: PassProgress) -> str:
    pct = progress.pct
    seg = f"{pct:>5.1f}%" if pct is not None else "   -  "
    d = progress.detail
    lines = [
        f"pass '{progress.pass_name}' @ {progress.rows:,} rows  "
        f"({datetime.now().astimezone().strftime('%H:%M:%S')})  "
        f"percentages are TIME ESTIMATES",
    ]
    if progress.status == _PASS_DONE:
        lines.append(
            f"  status    done     100.0% {_bar(100.0, 12)} wall={d.get('wall_clock_s')}s "
            f"rss={d.get('peak_rss_mib')}MiB errors={d.get('n_errors')} {d.get('run_status')}"
        )
    elif progress.status == _PASS_RUNNING:
        lines.append(
            f"  status    running  {seg} {_bar(pct, 12)} "
            f"elapsed={_humanize(progress.elapsed_s)} / est={_humanize(progress.est_s)} "
            f"({progress.basis})"
        )
    else:
        lines.append(
            f"  status    {progress.status:<9} {seg} {'░' * 12} "
            "no live measurement process and no verdict file"
        )
    if progress.spill_bytes is not None:
        mib = progress.spill_bytes / 1_048_576
        note = "ceiling is binding" if progress.spill_bytes else "working set under the ceiling"
        lines.append(f"  spill     {mib:.1f} MiB in the scratch dir ({note})")
    ceilings = d.get("ceilings") or {}
    if ceilings.get("memory_limit"):
        lines.append(
            f"  ceiling   memory_limit={ceilings.get('memory_limit')} "
            f"spill_cap={ceilings.get('max_temp_directory_size')}"
        )
    return "\n".join(lines)


def watch_pass(
    *,
    rows: int,
    pass_name: str,
    workdir: "Path | str" = ".benchmarks/ladder",
    fmt: str = "csv",
    mode: str = "standard",
    json_out: bool = False,
    spill_dir: Optional["Path | str"] = None,
) -> int:
    """Print one progress snapshot of a single bounded pass.

    Same exit-code convention as :func:`watch_ladder`: ``0`` the verdict file
    exists, ``1`` a live measurement process, ``2`` neither — i.e. stalled, never
    started, or a platform without ``pgrep`` where the watcher refuses to guess."""
    workdir = Path(workdir)
    out = pass_output_path(rows, pass_name, workdir)
    age = None
    if not out.exists():
        age = process_age_seconds(f"measure_bounded.py --rows {rows}")
    if spill_dir is None:
        spill_dir = resolve_spill_dir()
    progress = build_pass_progress(
        rows=rows, pass_name=pass_name, workdir=workdir, fmt=fmt, mode=mode,
        process_age_s=age, spill_bytes=dir_bytes(spill_dir),
    )
    if json_out:
        payload = {
            "workdir": str(workdir), "spill_dir": str(spill_dir) if spill_dir else None,
            **progress.to_dict(),
        }
        print(json.dumps(payload, indent=2), flush=True)
    else:
        print(format_pass(progress), flush=True)
    if progress.status == _PASS_DONE:
        return 0
    return 1 if progress.status == _PASS_RUNNING else 2


__all__ = [
    "LadderStage",
    "tier_label",
    "tier_paths",
    "parse_ps_etime",
    "process_age_seconds",
    "build_stages",
    "compute_overall",
    "format_stages",
    "watch_ladder",
    "PassProgress",
    "pass_output_path",
    "dir_bytes",
    "resolve_spill_dir",
    "build_pass_progress",
    "format_pass",
    "watch_pass",
]
