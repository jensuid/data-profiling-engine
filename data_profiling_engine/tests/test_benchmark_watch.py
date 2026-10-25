"""Tests for the read-only ladder progress watcher (M7-T2a helper).

The watcher's numbers are TIME ESTIMATES, so what is pinned here is the maths
and the on-disk evidence chain — never a wall-clock promise. One test guards the
label/format parity with ``harness.run_suite``, because the whole design rests
on the watcher reconstructing exactly the files the harness writes.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from data_profiling_engine.benchmark import harness
from data_profiling_engine.benchmark.watch import (
    LadderStage,
    build_pass_progress,
    build_stages,
    compute_overall,
    dir_bytes,
    format_pass,
    format_stages,
    parse_ps_etime,
    pass_output_path,
    read_spill_peak,
    spill_sample_log,
    tier_label,
    tier_paths,
    watch_pass,
)

_ROWS_SMALL, _ROWS_BIG = 10_000_000, 100_000_000


# -- ps etime parsing ---------------------------------------------------------

def test_parse_ps_etime_handles_all_bsd_shapes():
    assert parse_ps_etime("03:42") == 222
    assert parse_ps_etime("01:30:05") == 5405
    assert parse_ps_etime("2-04:06:08") == 2 * 86400 + 4 * 3600 + 6 * 60 + 8
    assert parse_ps_etime("  12:34:56  ") == 45296


def test_parse_ps_etime_rejects_noise():
    assert parse_ps_etime("") is None
    assert parse_ps_etime("   ") is None
    assert parse_ps_etime("not-a-time") is None  # never a guessed duration


# -- stage percentage maths ---------------------------------------------------

def test_stage_pct_done_active_pending():
    assert LadderStage("l", 1, "measure", "done").pct == 100.0
    active = LadderStage("l", 1, "measure", "active", est_s=200.0, elapsed_s=50.0)
    assert active.pct == 25.0
    # an active stage that outlived its estimate saturates at 100, never above
    overdue = LadderStage("l", 1, "measure", "active", est_s=200.0, elapsed_s=900.0)
    assert overdue.pct == 100.0
    assert LadderStage("l", 1, "measure", "pending", est_s=200.0).pct is None
    assert LadderStage("l", 1, "measure", "unknown").pct is None
    # no estimate → no percentage, rather than a fabricated one
    assert LadderStage("l", 1, "measure", "active", est_s=None, elapsed_s=10).pct is None


def test_compute_overall_is_time_weighted():
    stages = [
        LadderStage("a", 10, "generate", "done", est_s=100.0, elapsed_s=100.0),
        LadderStage("a", 10, "measure", "done", est_s=1000.0, elapsed_s=1000.0),
        LadderStage("b", 20, "generate", "done", est_s=200.0, elapsed_s=200.0),
        LadderStage("b", 20, "measure", "active", est_s=800.0, elapsed_s=400.0),
    ]
    out = compute_overall(stages)
    assert out["est_total_s"] == 2100.0
    # (100+1000+200+400) / 2100
    assert out["overall_pct"] == round(100.0 * 1700 / 2100, 1)
    assert out["eta_s"] == 400.0


def test_compute_overall_without_weightable_stage():
    out = compute_overall([LadderStage("a", 1, "measure", "unknown")])
    assert out == {"overall_pct": None, "est_total_s": None, "eta_s": None}


# -- harness label parity (the watcher must read what the harness writes) -----

def test_tier_label_matches_harness_fixture_naming(tmp_path):
    label = tier_label(_ROWS_SMALL)
    assert label == "csv_10000000_std"
    input_path, base_config = harness.fixture_paths(label, "csv", tmp_path)
    paths = tier_paths(label, "csv", "standard", tmp_path)
    assert paths["fixture"] == input_path
    assert paths["base_config"] == base_config


# -- on-disk reconstruction --------------------------------------------------

def _write_measure(path: Path, *, wall: float, rows: int, rss: int = 709_861_376) -> None:
    path.write_text(json.dumps({
        "wall_clock_s": wall, "peak_rss_bytes": rss, "row_count": rows,
        "mode": "standard", "run_status": "SUCCEEDED",
    }), encoding="utf-8")


def _stamp(path: Path, at: float) -> None:
    os.utime(path, (at, at))


def test_build_stages_all_pending_on_empty_workdir(tmp_path):
    stages = build_stages(sizes=(_ROWS_SMALL, _ROWS_BIG), workdir=tmp_path, now=time.time())
    assert [s.kind for s in stages] == ["generate", "measure"] * 2
    assert all(s.status == "pending" for s in stages)
    # estimates come from the recorded-baseline fallbacks, never None
    assert all(s.est_s and s.est_s > 0 for s in stages)
    # the bigger tier must be estimated more expensive in both kinds
    gen = [s for s in stages if s.kind == "generate"]
    meas = [s for s in stages if s.kind == "measure"]
    assert gen[1].est_s > gen[0].est_s and meas[1].est_s > meas[0].est_s


def test_build_stages_completed_tier_is_measured_and_calibrates_the_next(tmp_path):
    now = 1_700_000_000.0
    label = tier_label(_ROWS_SMALL)
    paths = tier_paths(label, "csv", "standard", tmp_path)
    paths["fixture"].write_bytes(b"x" * 1000)
    paths["base_config"].write_text("{}", encoding="utf-8")
    paths["run_config"].write_text("{}", encoding="utf-8")
    _write_measure(paths["measure"], wall=1310.0, rows=_ROWS_SMALL)
    for p in (paths["fixture"], paths["base_config"], paths["run_config"], paths["measure"]):
        _stamp(p, now - 600)

    stages = build_stages(sizes=(_ROWS_SMALL, _ROWS_BIG), workdir=tmp_path, now=now,
                          run_started_age_s=7000.0)
    small_gen, small_meas, big_gen, big_meas = stages
    # generation bracketed by the parent's start (now-7000) and the config mtime
    assert small_gen.status == "done" and small_gen.basis == "measured"
    assert small_gen.est_s == 6400.0
    assert small_meas.status == "done" and small_meas.detail["wall_clock_s"] == 1310.0
    assert small_meas.detail["peak_rss_mb"] == 677.0
    assert small_meas.detail["run_status"] == "SUCCEEDED"
    # calibration: 1310 s / 10M rows → 100M tier estimated at 13 100 s, not the
    # coarse fallback, and strictly larger than the small tier's own measure
    assert big_meas.status == "pending"
    assert big_meas.est_s == pytest.approx(13100.0)
    assert big_gen.status == "pending"
    assert big_gen.est_s > 0


def test_build_stages_active_measure_stage(tmp_path):
    now = 1_700_000_000.0
    small, big = tier_label(_ROWS_SMALL), tier_label(_ROWS_BIG)
    for label, rows in ((small, _ROWS_SMALL), (big, _ROWS_BIG)):
        p = tier_paths(label, "csv", "standard", tmp_path)
        p["fixture"].write_bytes(b"y" * 500)
        p["base_config"].write_text("{}", encoding="utf-8")
        p["run_config"].write_text("{}", encoding="utf-8")
        _stamp(p["fixture"], now - 5000)
        _stamp(p["base_config"], now - 5000)
        _stamp(p["run_config"], now - 1200)  # child spawned 20 min ago
    _write_measure(tier_paths(small, "csv", "standard", tmp_path)["measure"],
                   wall=1310.0, rows=_ROWS_SMALL)
    _stamp(tier_paths(small, "csv", "standard", tmp_path)["measure"], now - 5000)

    stages = build_stages(sizes=(_ROWS_SMALL, _ROWS_BIG), workdir=tmp_path, now=now)
    big_meas = stages[3]
    assert big_meas.status == "active"
    assert big_meas.elapsed_s == 1200.0
    assert big_meas.pct == round(100.0 * 1200 / 13100, 1)
    assert compute_overall(stages)["overall_pct"] is not None


def test_build_stages_active_generate_stage_reports_bytes_written(tmp_path):
    now = 1_700_000_000.0
    label = tier_label(_ROWS_BIG)
    p = tier_paths(label, "csv", "standard", tmp_path)
    p["fixture"].write_bytes(b"z" * 1_000)  # half-written fixture, no config yet
    _stamp(p["fixture"], now - 100)

    stages = build_stages(sizes=(_ROWS_BIG,), workdir=tmp_path, now=now,
                          run_started_age_s=300.0)
    gen = stages[0]
    assert gen.status == "active"
    assert gen.detail["fixture_bytes"] == 1_000
    assert gen.elapsed_s == 300.0  # bracketed by the live parent's age
    # measurement cannot start before the fixture exists → pending, not faked
    assert stages[1].status == "pending"


def test_format_stages_marks_completion(tmp_path):
    stages = [LadderStage("csv_1_std", 1, "measure", "done", est_s=10.0, elapsed_s=10.0)]
    text = format_stages(stages, compute_overall(stages), workdir=tmp_path, results_done=True)
    assert "RUN COMPLETE" in text
    assert "csv_1_std" in text and "100.0%" in text
    assert "ESTIMATE" in text  # the estimate disclaimer is never dropped
    running = format_stages(stages, {"overall_pct": 50.0, "eta_s": 60.0},
                            workdir=tmp_path, results_done=False)
    assert "eta ~" in running


# -- single bounded pass (M7-T2c) --------------------------------------------

def _write_pass_verdict(path: Path, *, wall: float = 9843.7,
                        rss: int = 4_139_982_848, errors: int = 0) -> None:
    path.write_text(json.dumps({
        "wall_clock_s": wall, "peak_rss_bytes": rss, "n_errors": errors,
        "extra": {"run_status": "SUCCEEDED"},
        "ceilings": {"memory_limit": "4294967296 B",
                     "max_temp_directory_size": "8589934592 B"},
    }), encoding="utf-8")


def test_pass_output_path_matches_the_runner_filename() -> None:
    """The watcher reconstructs the file ``run_pass.sh`` writes; a drift here
    would make every pass look ``not_started`` (the M7-T2a label-parity trap)."""
    assert pass_output_path(_ROWS_BIG, "top", ".x").name == "bounded_100m_top.json"
    assert pass_output_path(_ROWS_SMALL, "floor", ".x").name == "bounded_10m_floor.json"


def test_build_pass_progress_done_reads_the_measured_verdict(tmp_path: Path) -> None:
    _write_pass_verdict(pass_output_path(_ROWS_BIG, "top", tmp_path))
    p = build_pass_progress(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path)
    assert p.status == "done" and p.pct == 100.0 and p.basis == "measured"
    assert p.est_s == 9843.7
    assert p.detail["peak_rss_mib"] == 3948.2
    assert p.detail["n_errors"] == 0 and p.detail["run_status"] == "SUCCEEDED"
    assert p.detail["ceilings"]["memory_limit"] == "4294967296 B"


def test_build_pass_progress_running_calibrates_on_the_same_tier(tmp_path: Path) -> None:
    """Estimate comes from the SAME tier's earlier measured run (identical
    fixture), and is labelled ``reference`` — not a promise."""
    _write_measure(tmp_path / f"{tier_label(_ROWS_BIG)}.measure.json", wall=9843.7,
                   rows=_ROWS_BIG)
    p = build_pass_progress(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                            process_age_s=1800)
    assert p.status == "running"
    assert p.est_s == 9843.7 and p.basis == "reference"
    assert p.pct == round(100.0 * 1800 / 9843.7, 1)
    assert p.detail["child_measured"] is False


def test_build_pass_progress_never_invents_an_estimate(tmp_path: Path) -> None:
    p = build_pass_progress(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                            process_age_s=100)
    assert p.status == "running" and p.est_s is None
    assert p.pct is None  # no duration known → no percentage, never a guess


def test_build_pass_progress_marks_a_dead_driver_as_stalled(tmp_path: Path) -> None:
    """Child emitted its measurement but the verdict file is missing: the driver
    died. Must never be reported as finished."""
    (tmp_path / f"{tier_label(_ROWS_BIG)}_bounded.measure.json").write_text(
        "{}", encoding="utf-8")
    p = build_pass_progress(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path)
    assert p.status == "stalled" and p.pct is None
    assert p.detail["child_measured"] is True


def test_build_pass_progress_not_started(tmp_path: Path) -> None:
    p = build_pass_progress(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path)
    assert p.status == "not_started" and p.pct is None


def test_dir_bytes_sums_nested_files_and_reports_unknown(tmp_path: Path) -> None:
    nested = tmp_path / "scratch" / "sub"
    nested.mkdir(parents=True)
    (tmp_path / "scratch" / "a.tmp").write_bytes(b"x" * 100)
    (nested / "b.tmp").write_bytes(b"y" * 50)
    assert dir_bytes(tmp_path / "scratch") == 150
    # an absent workspace means unknown, never "zero bytes spilled"
    assert dir_bytes(tmp_path / "missing") is None
    assert dir_bytes(None) is None


def test_format_pass_reports_spill_and_the_disclaimer(tmp_path: Path) -> None:
    p = build_pass_progress(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                            process_age_s=100, spill_bytes=150 * 1_048_576)
    text = format_pass(p)
    assert "TIME ESTIMATES" in text
    assert "150.0 MiB" in text and "ceiling is binding" in text
    # unknown duration → a dash, never a fabricated percentage
    assert "   -  " in text

    empty = format_pass(build_pass_progress(rows=_ROWS_BIG, pass_name="top",
                                            workdir=tmp_path, process_age_s=0,
                                            spill_bytes=0))
    assert "working set under the ceiling" in empty


def test_watch_pass_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("data_profiling_engine.benchmark.watch.process_age_seconds",
                        lambda pattern: 600)
    assert watch_pass(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                      spill_dir=scratch) == 1
    assert "running" in capsys.readouterr().out

    monkeypatch.setattr("data_profiling_engine.benchmark.watch.process_age_seconds",
                        lambda pattern: None)
    assert watch_pass(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                      spill_dir=scratch) == 2

    _write_pass_verdict(pass_output_path(_ROWS_BIG, "top", tmp_path))
    monkeypatch.setattr("data_profiling_engine.benchmark.watch.process_age_seconds",
                        lambda pattern: 600)  # a still-listed pid cannot mask a verdict
    assert watch_pass(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                      spill_dir=scratch) == 0
    assert "done" in capsys.readouterr().out


def test_watch_pass_reads_the_spill_dir_the_pass_recorded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pass records the workspace it actually used; trusting this process's
    default instead reported "0 MiB spilled" for a pass that had spilled 87.7 MiB
    (measured on the M7-T2c 100M pass, whose runner overrides the temp dir)."""
    pass_scratch = tmp_path / "pass_scratch"
    pass_scratch.mkdir()
    (pass_scratch / "block.tmp").write_bytes(b"x" * 4096)
    pass_output_path(_ROWS_BIG, "top", tmp_path).write_text(json.dumps({
        "wall_clock_s": 8847.8, "peak_rss_bytes": 3_904_925_696, "n_errors": 0,
        "extra": {"run_status": "SUCCEEDED"},
        "ceilings": {"memory_limit": "4294967296 B",
                     "temp_directory": str(pass_scratch),
                     "max_temp_directory_size": "8589934592 B"},
    }), encoding="utf-8")

    assert watch_pass(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                      json_out=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spill_dir"] == str(pass_scratch)
    assert payload["spill_bytes"] == 4096


def test_read_spill_peak_takes_the_high_water_mark(tmp_path: Path) -> None:
    log = tmp_path / "spill_samples_top.log"
    log.write_text("0\t/tmp/x\n89760\t/tmp/x\n1024 /tmp/x\n", encoding="utf-8")
    assert read_spill_peak(log) == (89760, 3)
    assert read_spill_peak(tmp_path / "missing.log") is None
    log.write_text("", encoding="utf-8")
    assert read_spill_peak(log) is None


def test_watch_pass_prefers_the_sampler_over_the_emptied_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DuckDB deletes its spill blocks when the run ends, so a done pass reads 0 B
    on disk; the sampler log is the only surviving record (87.7 MiB measured)."""
    emptied = tmp_path / "scratch_after"
    emptied.mkdir()
    pass_output_path(_ROWS_BIG, "top", tmp_path).write_text(json.dumps({
        "wall_clock_s": 8847.8, "peak_rss_bytes": 3_904_925_696, "n_errors": 0,
        "extra": {"run_status": "SUCCEEDED"},
        "ceilings": {"temp_directory": str(emptied)},
    }), encoding="utf-8")
    spill_sample_log(tmp_path, "top").write_text(
        "0\t/x\n89760\t/x\n89760\t/x\n", encoding="utf-8")

    assert watch_pass(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path,
                      json_out=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spill_bytes"] == 89760 * 1024
    assert payload["detail"]["spill_source"] == "samples"
    assert payload["detail"]["spill_samples"] == 3


def test_format_pass_does_not_claim_zero_when_the_sampler_saw_spill(
    tmp_path: Path,
) -> None:
    progress = build_pass_progress(rows=_ROWS_BIG, pass_name="top", workdir=tmp_path)
    progress.status = "done"
    progress.spill_bytes = 89760 * 1024
    progress.detail.update({"spill_source": "samples", "spill_samples": 1734})
    out = format_pass(progress)
    assert "87.7 MiB peak over 1734 samples" in out
    assert "working set under the ceiling" not in out
