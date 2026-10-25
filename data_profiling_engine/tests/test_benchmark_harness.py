"""M7-T1 — Benchmark harness + synthetic generator tests (TESTING §4).

Fast by construction: this covers the harness *wiring* and the pure structural
guarantee functions — it never runs the multi-second profiling path or the heavy
10M–100M ladder (that is the scheduled/release `python -m
data_profiling_engine.benchmark baseline` run, TESTING §1 "scheduled + release
gate"). Assertions are independent-reference where a value is checkable
(row count via DuckDB ``count(*)``, distinct bounds, config schema validation,
determinism by byte compare); the guarantee verdicts are checked against
hand-built :class:`CaseResult` inputs so the classification logic is pinned
without a real run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import duckdb
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.benchmark import harness
from data_profiling_engine.benchmark.generator import (
    DatasetSpec,
    column_names,
    generate_dataset,
    write_dataset_config,
)
from data_profiling_engine.benchmark.harness import (
    PLATEAU_MAX_EXPONENT,
    CaseResult,
    _RssPeakSampler,
    _peak_rss_bytes,
    _peak_rss_method,
    evaluate_rss_boundedness,
    evaluate_rss_plateau,
    evaluate_sampled_cheaper,
    fixture_paths,
)

# ---- generator: spec validation ------------------------------------------

def test_spec_rejects_bad_parameters():
    with pytest.raises(ValueError):
        DatasetSpec(rows=0, columns=8)
    with pytest.raises(ValueError):
        DatasetSpec(rows=100, columns=4)          # below the 5 core kinds
    with pytest.raises(ValueError):
        DatasetSpec(rows=100, columns=8, missingness=0.9)   # > 0.4 band
    with pytest.raises(ValueError):
        DatasetSpec(rows=100, columns=8, cardinality="extreme")
    with pytest.raises(ValueError):
        DatasetSpec(rows=100, columns=8, distribution="bimodal")


# ---- generator: shape + determinism --------------------------------------

def _count(con, path, fmt):
    src = f"read_csv('{path}', header=true)" if fmt == "csv" else f"read_parquet('{path}')"
    return int(con.execute(f"SELECT count(*) FROM {src}").fetchone()[0])


@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_generator_produces_expected_shape(tmp_path, fmt):
    spec = DatasetSpec(rows=500, columns=8, cardinality="medium",
                       missingness=0.2, distribution="normal", seed=3)
    out = generate_dataset(spec, tmp_path / f"d.{fmt}", fmt=fmt)
    assert out.suffix == f".{fmt}"
    con = duckdb.connect()
    assert _count(con, out, fmt) == 500                      # rows exact
    names = column_names(spec)
    assert len(names) == 8
    assert names[:5] == ["pk_id", "amount", "stage", "category", "event_date"]
    # every declared column is physically present, in file order
    present = [d[0] for d in con.execute(f"SELECT * FROM {_src(out, fmt)} LIMIT 0").description]
    assert present == names
    # missingness applied: amount has some nulls but not all
    non_null, total = con.execute(
        f"SELECT count(amount), count(*) FROM {_src(out, fmt)}"
    ).fetchone()
    assert 0 < non_null < total
    # cardinality bounded to the medium tier (<= 1000 distinct, and <= rows)
    distinct = con.execute(
        f"SELECT count(DISTINCT category) FROM {_src(out, fmt)}"
    ).fetchone()[0]
    assert 2 <= distinct <= 1000


def _src(path: Path, fmt: str) -> str:
    return f"read_csv('{path}', header=true)" if fmt == "csv" else f"read_parquet('{path}')"


def test_generator_is_deterministic_and_seed_decorrelates(tmp_path):
    spec = DatasetSpec(rows=200, columns=5, seed=11)
    a = generate_dataset(spec, tmp_path / "a.csv", fmt="csv")
    b = generate_dataset(spec, tmp_path / "b.csv", fmt="csv")
    assert a.read_bytes() == b.read_bytes()          # same spec → byte-identical
    other = DatasetSpec(rows=200, columns=5, seed=12)
    c = generate_dataset(other, tmp_path / "c.csv", fmt="csv")
    assert a.read_bytes() != c.read_bytes()          # different seed → different data


def test_generator_parquet_preserves_native_date_type(tmp_path):
    # the CSV path coerces to string on read; Parquet keeps a real DATE so the
    # temporal family sees native types (CONTRACTS physical vs logical types)
    spec = DatasetSpec(rows=100, columns=5)
    out = generate_dataset(spec, tmp_path / "d.parquet", fmt="parquet")
    con = duckdb.connect()
    typ = con.execute(f"SELECT typeof(event_date) FROM read_parquet('{out}') LIMIT 1").fetchone()[0]
    assert typ.upper() == "DATE"


# ---- config emitted for a fixture is schema-valid ------------------------

def test_write_dataset_config_validates(tmp_path):
    spec = DatasetSpec(rows=100, columns=6)
    cfg_path = write_dataset_config(spec, tmp_path / "cfg.json")
    document = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert S.validate("dataset_config.schema.json", document) == []
    # both referenced columns actually exist in the generated fixture
    names = set(column_names(spec))
    for check in document["quality_checks"]:
        assert check["column"] in names


# ---- harness: fixture paths are deterministic ----------------------------

def test_fixture_paths_stable(tmp_path):
    inp, cfg = fixture_paths("csv_1000_std", "csv", tmp_path)
    assert inp == tmp_path / "csv_1000_std.csv"
    assert cfg == tmp_path / "csv_1000_std.config.json"


def test_peak_rss_reports_bytes_or_none():
    rss = _peak_rss_bytes()
    # POSIX → positive int (bytes); Windows → None. Never a negative/zero int.
    assert rss is None or rss > 0


# ---- peak-RSS mechanism label + Windows fallback sampler (M7-T3) ----------

def test_peak_rss_method_label_is_honest():
    # POSIX high-water wins over psutil; a sampled peak is never labelled exact;
    # neither library available → an explicit "unavailable", never a silent 0.
    assert _peak_rss_method(resource_available=True, psutil_available=True) == "resource.ru_maxrss"
    assert _peak_rss_method(resource_available=False, psutil_available=True) == "psutil_sampled_peak"
    assert _peak_rss_method(resource_available=False, psutil_available=False) == "unavailable"


def test_peak_rss_method_matches_this_platform():
    # Whatever is importable here must be exactly what the label reports — the
    # recorded peak is never attributed to a mechanism that is not loaded.
    method = _peak_rss_method(
        resource_available=harness._resource is not None,
        psutil_available=harness._psutil is not None,
    )
    if harness._resource is not None:
        assert method == "resource.ru_maxrss"
        assert _peak_rss_bytes() is not None
    else:  # pragma: no cover - exercised only on Windows
        assert method in {"psutil_sampled_peak", "unavailable"}


def test_rss_sampler_is_inert_without_psutil(monkeypatch):
    # No 'resource' AND no psutil → the sampler yields None (an honest gap),
    # never a fabricated 0 that would wrongly flip the RSS verdicts.
    monkeypatch.setattr(harness, "_psutil", None)
    sampler = _RssPeakSampler()
    assert sampler.start() is sampler
    assert sampler.stop() is None


@pytest.mark.skipif(harness._psutil is None, reason="psutil not installed (only required on Windows)")
def test_rss_sampler_reports_positive_peak():
    sampler = _RssPeakSampler(interval_s=0.01).start()
    buf = bytearray(8 * 1_048_576)  # touch memory so there is a non-trivial RSS
    peak = sampler.stop()
    del buf
    assert isinstance(peak, int) and peak > 0


# ---- structural guarantee: RSS boundedness (pure logic) ------------------

def _case(rows: int, rss_mb: Optional[float], wall: float = 1.0) -> CaseResult:
    return CaseResult(
        label=f"c{rows}", spec={}, fmt="csv", mode="standard", sampled=False,
        wall_clock_s=wall, peak_rss_bytes=(int(rss_mb * 1_048_576) if rss_mb is not None else None),
        source_bytes=0, row_count=rows,
    )


def test_rss_bounded_when_sublinear():
    # rows grow 10x while RSS grows 1.1x → bounded (the no-full-materialization claim)
    verdict = evaluate_rss_boundedness([_case(1_000_000, 200.0), _case(10_000_000, 220.0)])
    assert verdict["verdict"] == "bounded"
    assert verdict["row_ratio"] == 10.0
    assert verdict["rss_ratio"] < 2.0


def test_rss_linear_growth_detected():
    # RSS tracking row count ~1:1 → the full-materialization regression signal
    verdict = evaluate_rss_boundedness([_case(1_000_000, 200.0), _case(10_000_000, 2000.0)])
    assert verdict["verdict"] == "linear_growth"


def test_rss_insufficient_data_when_windows_or_single():
    assert evaluate_rss_boundedness([_case(1000, 100.0)])["verdict"] == "insufficient_data"
    # all RSS None (Windows) → insufficient, never a false "bounded"
    assert evaluate_rss_boundedness([_case(1000, None), _case(2000, None)])["verdict"] == "insufficient_data"


# ---- structural guarantee: RSS plateau (stricter than boundedness) --------

def test_rss_plateau_recovers_a_known_power_law():
    """Independent reference: RSS built as rows**0.2 must be measured as 0.2."""
    exponent = 0.2
    cases = [
        _case(1_000_000, 200.0),
        _case(10_000_000, 200.0 * 10 ** exponent),
        _case(100_000_000, 200.0 * 100 ** exponent),
    ]
    verdict = evaluate_rss_plateau(cases)
    assert verdict["growth_exponent"] == pytest.approx(exponent, abs=1e-3)
    assert verdict["verdict"] == "plateau"
    assert verdict["rss_multiplier_per_decade"] == pytest.approx(10 ** exponent, abs=1e-3)


def test_rss_plateau_rejects_the_m7_t2a_curve_as_growing():
    """The regression this check exists for: 677 MB @ 10M vs 3 948 MB @ 100M
    passed ``bounded`` (ratio 5.83 < 10) yet grows as rows**0.766 — the fitted
    exponent and the 1B-row forecast recorded in TESTING §4.3.3."""
    cases = [_case(10_000_000, 677.0), _case(100_000_000, 3948.2)]
    verdict = evaluate_rss_plateau(cases)
    assert verdict["verdict"] == "growing"
    assert verdict["growth_exponent"] == pytest.approx(0.766, abs=0.01)
    # extrapolate the fit one more decade: 3 948 MiB * 10**0.766 ≈ 22 GiB — the
    # number that makes "bounded" an unacceptable answer on a 16 GB machine
    assert verdict["forecast_rss_gib_at_1b_rows"] > 16.0


def test_rss_plateau_boundary_is_inclusive_and_configurable():
    at_limit = evaluate_rss_plateau(
        [_case(1_000_000, 100.0), _case(10_000_000, 100.0 * 10 ** 0.25)],
        max_exponent=PLATEAU_MAX_EXPONENT,
    )
    assert at_limit["growth_exponent"] == pytest.approx(0.25, abs=1e-3)
    assert at_limit["verdict"] == "plateau"
    stricter = evaluate_rss_plateau(
        [_case(1_000_000, 100.0), _case(10_000_000, 400.0)], max_exponent=0.5
    )  # exponent = log2(4)/log2(10) = 0.602 > 0.5
    assert stricter["verdict"] == "growing"


def test_rss_plateau_flags_a_run_above_the_engine_ceiling():
    """A flat curve is only trustworthy if it actually stayed inside the ceiling
    the engine promised (ADR-021); otherwise the limit silently did not apply."""
    cases = [_case(1_000_000, 100.0), _case(10_000_000, 105.0)]
    assert evaluate_rss_plateau(cases, ceiling_bytes=512 * 1_048_576)["verdict"] == "plateau"
    over = evaluate_rss_plateau(cases, ceiling_bytes=102 * 1_048_576)
    assert over["verdict"] == "over_ceiling"
    assert over["ceiling_mb"] == pytest.approx(102.0)


def test_rss_plateau_insufficient_data_never_guesses():
    assert evaluate_rss_plateau([_case(1000, 100.0)])["verdict"] == "insufficient_data"
    assert evaluate_rss_plateau([_case(1000, None), _case(2000, None)])["verdict"] == (
        "insufficient_data"
    )
    # identical row counts → no scaling to fit, even though there are 2 points
    flat_rows = evaluate_rss_plateau([_case(1000, 100.0), _case(1000, 400.0)])
    assert flat_rows["verdict"] == "insufficient_data"
    assert flat_rows["reason"] == "single_row_count"


# ---- structural guarantee: degrade strictly cheaper (pure logic) ---------

def test_sampled_cheaper_verdict():
    exact = _case(5_000_000, 300.0, wall=40.0)
    degrade = _case(5_000_000, 150.0, wall=12.0)
    verdict = evaluate_sampled_cheaper(exact, degrade)
    assert verdict["verdict"] == "cheaper"
    assert verdict["saved_s"] == pytest.approx(28.0)
    assert verdict["speedup"] == pytest.approx(3.33, abs=0.01)


def test_sampled_not_cheaper_flagged():
    exact = _case(5_000_000, 300.0, wall=10.0)
    degrade = _case(5_000_000, 150.0, wall=25.0)
    assert evaluate_sampled_cheaper(exact, degrade)["verdict"] == "not_cheaper"
