"""M4-T3 tests — CSV issue export (ENGINE §10; SECURITY §4; ADR-015 method
honesty; FAIL ≠ ERROR).

Two layers, mirroring the HTML report tests:
  * end-to-end over the checked-in golden fixture bundle (real engine output —
    rows come from what M2/M4-T1 pinned, nothing recomputed);
  * synthetic contract bundles for the narrow guarantees (header verbatim,
    PASS/ERROR exclusion, WARN inclusion, sampled→seed in the observed cell,
    not_computed→reason, one row per merged rule, comma-safe quoting,
    PII-by-construction, determinism, writers + ArtifactStore canonical ref).
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List

import pytest

from data_profiling_engine.cli import run_profile
from data_profiling_engine.orchestration.rollup import (
    SCHEMA_VERSION,
    ProfileBundle,
    profile_dataset,
)
from data_profiling_engine.reporting.csv_export import (
    ISSUE_CSV_COLUMNS,
    emit_issue_csv_artifact,
    render_issue_csv,
    write_issue_csv,
)
from data_profiling_engine.schemas.models import (
    CheckError,
    CheckThreshold,
    ColumnProfile,
    DatasetProfile,
    Finding,
    FindingFact,
    FindingScope,
    MetricValue,
    Missingness,
    ProfilingRun,
    Provenance,
    QualityCheckResult,
    RunConfig,
    RunDataset,
    RunEngine,
    RunStatus,
    RunTiming,
    SourceSnapshot,
    Uniqueness,
)
from data_profiling_engine.storage.artifact_store import ArtifactStore
from data_profiling_engine.tests import golden_harness as gh


# ---------------------------------------------------------------------------
# fixtures / synthetic builders (same shape discipline as test_reporting_html)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def golden_bundle() -> ProfileBundle:
    cfg = gh.load_config("sales_deals")
    return profile_dataset(
        "sales_deals", gh.GOLDEN_DIR / "sales_deals.csv", cfg, "run-csv",
        allowed_roots=[gh.GOLDEN_DIR],
    )


def _mv(value: Any, method: str = "exact", **params: Any) -> MetricValue:
    prov: Dict[str, Any] = {"method": method}
    reason = params.pop("reason", None)
    if reason is not None:
        prov["reason"] = reason
    if params:
        prov["method_params"] = params
    mv = MetricValue.model_construct(value=value, provenance=Provenance(**prov))
    if value is None:
        mv.model_fields_set.discard("value")
    return mv


def _synthetic_run(run_id: str = "run-syn") -> ProfilingRun:
    return ProfilingRun.model_validate(dict(
        schema_version=SCHEMA_VERSION, run_id=run_id,
        dataset=RunDataset(
            dataset_id="ds_syn", source_kind="file", connector="csv",
            source_ref="/data/sales.csv",
            source_snapshot=SourceSnapshot(
                path="/data/sales.csv", size_bytes=1234,
                modified_at="2026-10-01T00:00:00Z",
                content_hash="sha256:" + "a" * 64,
            ),
        ),
        config=RunConfig(config_version="1", config_hash="sha256:" + "b" * 64,
                         profile_mode="standard"),
        engine=RunEngine(engine_version="0.1.0", duckdb_version="1.1.3",
                         python_version="3.12.7"),
        timing=RunTiming(started_at="2026-10-24T09:00:00Z",
                         finished_at="2026-10-24T09:00:05Z", duration_ms=5000),
        status=RunStatus.SUCCEEDED,
    ))


def _col(name: str, logical: str = "string") -> ColumnProfile:
    return ColumnProfile.model_validate(dict(
        schema_version=SCHEMA_VERSION, column=name, ordinal=0,
        physical_type="VARCHAR", logical_type=logical, semantic_type="other",
        missingness=Missingness(null_count=3, blank_count=0, sentinel_count=0,
                                missing_rate=_mv(0.03)),
        uniqueness=Uniqueness(distinct_count=_mv(97), is_unique_candidate=False),
    ))


def _bundle(columns: List[ColumnProfile], checks, findings) -> ProfileBundle:
    run = _synthetic_run()
    profile = DatasetProfile.model_validate(dict(
        schema_version=SCHEMA_VERSION, run_id=run.run_id, row_count=_mv(100),
        column_count=len(columns), columns=columns,
    ))
    return ProfileBundle(run=run, profile=profile, checks=tuple(checks),
                         findings=tuple(findings), recommendations=(), errors=())


def _check(rule_id: str, status: str, *, column: str = "x", severity: str = "high",
           observed: Any = None, threshold: Any = None, failing: Any = 5,
           error: Any = None) -> QualityCheckResult:
    kwargs: Dict[str, Any] = dict(
        schema_version=SCHEMA_VERSION, run_id="run-syn", rule_id=rule_id,
        rule_version="1", dimension="completeness", status=status,
        severity=severity, column=column, executed_at="2026-10-24T09:00:04Z",
    )
    if observed is not None:
        kwargs["observed"] = observed
    if threshold is not None:
        kwargs["threshold"] = threshold
    if failing is not None:
        kwargs["failing_record_count"] = failing
    if error is not None:
        kwargs["error"] = error
    return QualityCheckResult.model_validate(kwargs)


def _finding(fid: str, *, column: Any = "x", severity: str = "high",
             refs: List[str], affected: Any = None,
             facts: Any = None) -> Finding:
    scope_kwargs: Dict[str, Any] = {"column": column}
    if affected is not None:
        scope_kwargs["affected_records"] = affected
    return Finding.model_validate(dict(
        schema_version=SCHEMA_VERSION, finding_id=fid,
        fingerprint="sha256:" + "d" * 64, run_id="run-syn",
        title=f"{fid} title", summary=f"{fid} summary", category="completeness",
        severity=severity, confidence="high", scope=FindingScope(**scope_kwargs),
        facts=facts or [FindingFact(metric_ref="metric:x.missingness.missing_rate",
                                    value=0.5, provenance=Provenance(method="exact"))],
        interpretation=None, hypotheses=[], evidence_refs=refs,
    ))


def _parse(text: str) -> List[List[str]]:
    return list(csv.reader(io.StringIO(text)))


# ---------------------------------------------------------------------------
# header contract (ENGINE §10 verbatim)
# ---------------------------------------------------------------------------

class TestHeader:
    def test_columns_verbatim_engine_section_10(self):
        assert ISSUE_CSV_COLUMNS == (
            "finding_id", "rule_id", "column", "status", "severity",
            "observed", "threshold", "affected_records", "evidence_ref",
        )

    def test_header_is_first_line_even_with_no_rows(self):
        bundle = _bundle([_col("x")], [], [])
        rows = _parse(render_issue_csv(bundle))
        assert rows == [list(ISSUE_CSV_COLUMNS)]


# ---------------------------------------------------------------------------
# end-to-end over the real golden bundle
# ---------------------------------------------------------------------------

class TestGoldenExport:
    def test_only_failing_checks_become_rows(self, golden_bundle):
        rows = _parse(render_issue_csv(golden_bundle))
        assert rows[0] == list(ISSUE_CSV_COLUMNS)
        data = rows[1:]
        # golden: deal_id_unique FAIL, value_not_null FAIL, stage_domain PASS.
        # profile-extreme findings (is_new/stage dups) carry no rule: ref ⇒ no row.
        rule_ids = {r[1] for r in data}
        assert rule_ids == {"deal_id_unique", "value_not_null"}
        assert "stage_domain" not in rule_ids
        assert len(data) == 2

    def test_row_field_join_matches_source_objects(self, golden_bundle):
        rows = {r[1]: r for r in _parse(render_issue_csv(golden_bundle))[1:]}
        dup = rows["deal_id_unique"]
        header = list(ISSUE_CSV_COLUMNS)
        rec = dict(zip(header, dup))
        assert rec["status"] == "FAIL"
        assert rec["column"] == "deal_id"
        assert rec["severity"] == "critical"
        assert rec["observed"].startswith("12") and "exact" in rec["observed"]
        assert rec["threshold"] == "unique"
        assert rec["affected_records"] == "12"
        assert "rule:deal_id_unique" in rec["evidence_ref"]

    def test_deterministic_for_same_bundle(self, golden_bundle):
        assert render_issue_csv(golden_bundle) == render_issue_csv(golden_bundle)


# ---------------------------------------------------------------------------
# narrow guarantees (synthetic bundles, one behaviour each)
# ---------------------------------------------------------------------------

class TestSelectionRules:
    def test_warn_check_is_exported(self):
        check = _check("warn_rule", "WARN", observed=_mv(0.4),
                       threshold=CheckThreshold.model_validate({"operator": "<=", "value": 0.2}))
        finding = _finding("F-001", refs=["metric:warn_rule", "rule:warn_rule"])
        rows = _parse(render_issue_csv(_bundle([_col("x")], [check], [finding])))
        assert len(rows) == 2
        assert rows[1][3] == "WARN"

    def test_pass_check_yields_no_row(self):
        check = _check("ok_rule", "PASS", observed=_mv(0))
        rows = _parse(render_issue_csv(
            _bundle([_col("x")], [check],
                    [_finding("F-001", refs=["rule:ok_rule"])])))
        assert len(rows) == 1  # header only

    def test_error_check_yields_no_row(self):
        # FAIL ≠ ERROR: an un-executable check is not a proven data problem.
        check = _check("boom_rule", "ERROR", observed=None, failing=None,
                       error=CheckError.model_validate({"code": "METRIC_EXEC_FAILED",
                                                        "message": "boom"}))
        rows = _parse(render_issue_csv(
            _bundle([_col("x")], [check],
                    [_finding("F-001", refs=["rule:boom_rule"])])))
        assert len(rows) == 1

    def test_profile_extreme_finding_has_no_rule_ref_yields_no_row(self):
        finding = _finding("F-001", refs=["metric:x.categorical.frequencies"])
        rows = _parse(render_issue_csv(_bundle([_col("x")], [], [finding])))
        assert len(rows) == 1

    def test_merged_finding_emits_one_row_per_rule(self):
        # a finding that absorbed two rules on the same column+category: both
        # FAIL ⇒ two tracker rows sharing the finding_id.
        c1 = _check("rule_a", "FAIL", observed=_mv(1))
        c2 = _check("rule_b", "FAIL", observed=_mv(2), column="x", failing=9)
        finding = _finding("F-001", refs=["rule:rule_a", "rule:rule_b"])
        rows = _parse(render_issue_csv(_bundle([_col("x")], [c1, c2], [finding])))
        assert [r[1] for r in rows[1:]] == ["rule_a", "rule_b"]
        assert all(r[0] == "F-001" for r in rows[1:])


class TestMethodHonesty:
    def test_sampled_observed_carries_seed(self):
        check = _check("r", "FAIL", observed=_mv(42, "sampled", seed=42,
                                                  strategy="reservoir", sample_size=50))
        finding = _finding("F-001", refs=["rule:r"])
        row = _parse(render_issue_csv(_bundle([_col("x")], [check], [finding])))[1]
        observed = dict(zip(ISSUE_CSV_COLUMNS, row))["observed"]
        assert "sampled" in observed and "seed=42" in observed

    def test_not_computed_observed_prints_reason(self):
        check = _check("r", "FAIL", observed=_mv(None, "not_computed",
                                                  reason="column all null"))
        finding = _finding("F-001", refs=["rule:r"])
        row = _parse(render_issue_csv(_bundle([_col("x")], [check], [finding])))[1]
        observed = dict(zip(ISSUE_CSV_COLUMNS, row))["observed"]
        assert observed.startswith("not_computed") and "column all null" in observed


class TestFormatting:
    def test_threshold_between_operator(self):
        check = _check("r", "FAIL", observed=_mv(5),
                       threshold=CheckThreshold.model_validate(
                           {"operator": "between", "value": 0, "upper": 100}))
        finding = _finding("F-001", refs=["rule:r"])
        row = _parse(render_issue_csv(_bundle([_col("x")], [check], [finding])))[1]
        thr = dict(zip(ISSUE_CSV_COLUMNS, row))["threshold"]
        assert thr == "between 0..100"

    def test_dataset_scoped_rule_column_falls_back_blank(self):
        check = _check("r", "FAIL", column=None, observed=_mv(3), failing=None)
        finding = _finding("F-001", column=None, refs=["rule:r"], affected=7)
        row = _parse(render_issue_csv(_bundle([_col("x")], [check], [finding])))[1]
        rec = dict(zip(ISSUE_CSV_COLUMNS, row))
        assert rec["column"] == ""
        assert rec["affected_records"] == "7"  # falls back to finding scope

    def test_comma_bearing_threshold_is_quoted_single_cell(self):
        check = _check("r", "FAIL", observed=_mv(0),
                       threshold=CheckThreshold.model_validate(
                           {"operator": "in", "value": ["A", "B,C"]}))
        finding = _finding("F-001", refs=["rule:r"])
        text = render_issue_csv(_bundle([_col("x")], [check], [finding]))
        rows = _parse(text)
        assert len(rows[1]) == len(ISSUE_CSV_COLUMNS)  # comma stayed inside a cell
        assert dict(zip(ISSUE_CSV_COLUMNS, rows[1]))["threshold"] == 'in ["A", "B,C"]'

    def test_no_raw_source_value_leaks_for_pii_column(self):
        # a failing check on an email column: only id/name/aggregate land in CSV.
        secret = "john.doe@acme-corp.com"
        check = _check("email_null", "FAIL", column="email",
                       observed=_mv(7))  # observed is a count, never the value
        finding = _finding("F-001", column="email", refs=["rule:email_null"])
        text = render_issue_csv(_bundle([_col("email", "email")], [check], [finding]))
        assert secret not in text
        assert "email" in text  # column name preserved for the tracker join


# ---------------------------------------------------------------------------
# writers: direct path (CLI --csv) + ArtifactStore emit (M3-T2 lifecycle)
# ---------------------------------------------------------------------------

class TestWriters:
    def test_write_issue_csv_creates_parents(self, golden_bundle, tmp_path):
        out = tmp_path / "nested" / "dir" / "issues.csv"
        written = write_issue_csv(golden_bundle, out)
        assert written == out and out.is_file()
        assert out.read_text(encoding="utf-8") == render_issue_csv(golden_bundle)

    def test_emit_through_artifact_store_canonical_ref(self, golden_bundle, tmp_path):
        store = ArtifactStore(data_root=tmp_path)
        ref = emit_issue_csv_artifact(golden_bundle, store)
        assert ref == f"artifacts/{golden_bundle.run.run_id}/issues.csv"
        assert store.read_text(ref) == render_issue_csv(golden_bundle)

    def test_cli_csv_flag(self, tmp_path):
        out_json = tmp_path / "profile.json"
        out_csv = tmp_path / "issues.csv"
        run_profile(
            gh.GOLDEN_DIR / "sales_deals.csv",
            gh.GOLDEN_DIR / "sales_deals.config.json",
            out_json,
            dataset_id="sales_deals", run_id="run-cli-csv",
            allowed_roots=[gh.GOLDEN_DIR],
            csv_out=out_csv,
        )
        assert out_json.is_file()  # JSON path untouched
        assert out_csv.is_file()
        rows = _parse(out_csv.read_text(encoding="utf-8"))
        assert rows[0] == list(ISSUE_CSV_COLUMNS)
        assert {r[1] for r in rows[1:]} == {"deal_id_unique", "value_not_null"}
