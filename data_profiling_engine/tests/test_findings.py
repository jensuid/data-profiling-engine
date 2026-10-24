"""M2-T8 reference tests — findings: detectors, dedup/merge, prioritization,
**fingerprint** (ENGINE §6; CONTRACTS §4.2 / §4.4 / ADR-016).

TESTING §3.1 (engine owns the code, so test against independent references):
the "independent reference" for findings is the *deterministic identity law*
of ADR-016 (same problem, different numbers ⇒ same fingerprint; different
problem ⇒ different fingerprint), each §6.1 detector asserted against the
Finding shape it must emit, and every emitted Finding validated against the
frozen ``finding.schema.json`` with format checking on.

Coverage:
- fingerprint stability / distinction (§4.4): value change keeps identity;
  column / rule / category changes break it.
- rule detector: FAIL + WARN ⇒ findings; PASS ⇒ none; **ERROR ⇒ none** (an
  un-executable check is not a proven data problem, FAIL ≠ ERROR).
- profile-extreme detectors: constant column, near-total missingness, dominant
  category, duplicated-key auxiliary — each fires past its threshold, each
  silent below it.
- confidence mirrors method (exact>approximate>sampled); facts keep provenance.
- dedup collapses same ``(column, category)`` and unions evidence.
- prioritization: F-001 is the highest-impact finding; critical-PK outranks.
- MVP invariants: workflow null, finding_id ``F-NNN``, hypotheses unverified,
  no fabricated numbers in facts.
- one integration pass through ``run_quality_checks`` on the sales fixture.
"""
from __future__ import annotations

from typing import List, Optional

import duckdb

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.findings import build_findings
from data_profiling_engine.findings.detectors import (
    DOMINANT_CATEGORY_SHARE,
    NEAR_TOTAL_MISSINGNESS,
)
from data_profiling_engine.findings.fingerprinting import (
    build_scope_key,
    compute_fingerprint,
)
from data_profiling_engine.quality import run_quality_checks
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    DatasetConfig,
    Finding,
    QualityCheckResult,
    SourceSnapshot,
)

RUN_ID = "run_test_0001"
DS = "sales"


# ---------------------------------------------------------------------------
# fixture builders — construct contract-valid evidence directly
# ---------------------------------------------------------------------------


def _mv(value, method="exact", denominator=100, **prov):
    mv = {"value": value, "provenance": {"method": method}}
    if prov.get("params"):
        mv["provenance"]["method_params"] = prov["params"]
    if prov.get("reason"):
        mv["provenance"]["reason"] = prov["reason"]
    if denominator is not None:
        mv["denominator"] = denominator
    return mv


def _check(
    rule_id: str, dimension: str, status: str, column: Optional[str],
    value=None, method="exact", severity="high", denominator=100,
    failing=None, operator=None, threshold_value=None, error=None,
    params=None,
) -> QualityCheckResult:
    d = {
        "schema_version": "1.0", "run_id": RUN_ID, "rule_id": rule_id,
        "rule_version": "1", "dimension": dimension, "status": status,
        "severity": severity, "column": column, "executed_at": "2026-10-24T09:00:00Z",
    }
    if status in ("PASS", "FAIL", "WARN"):
        d["observed"] = _mv(value, method, denominator, params=params)
        d["failing_record_count"] = failing if failing is not None else 0
        if operator is not None:
            d["threshold"] = {"operator": operator, "value": threshold_value}
        else:
            d["threshold"] = None
    else:  # ERROR
        d["observed"] = None
        d["error"] = error or {"code": "RULE_EXEC_FAILED", "message": "boom"}
    return QualityCheckResult.model_validate(d)


def _col(
    column: str, ordinal: int, *, missing_rate=None, distinct=None,
    denom=None, categories=None, frequencies=None, logical="string",
    physical="VARCHAR", semantic="dimension",
) -> ColumnProfile:
    p = {
        "schema_version": "1.0", "column": column, "ordinal": ordinal,
        "physical_type": physical, "logical_type": logical, "semantic_type": semantic,
    }
    if missing_rate is not None:
        p["missingness"] = {
            "null_count": int(missing_rate * 100), "blank_count": 0, "sentinel_count": 0,
            "missing_rate": _mv(missing_rate, "exact", 100),
        }
    if distinct is not None:
        p["uniqueness"] = {
            "distinct_count": _mv(distinct, "exact", denom if denom is not None else 100),
            "is_unique_candidate": distinct == (denom or 100),
        }
    if categories is not None:
        p["categorical"] = {"categories": categories, "frequencies": frequencies}
    return ColumnProfile.model_validate(p)


def _config(primary_key: Optional[List[str]] = None, rules: Optional[List[dict]] = None) -> DatasetConfig:
    ds = {"name": DS}
    if primary_key:
        ds["primary_key"] = primary_key
    return DatasetConfig.model_validate({
        "config_version": 3, "dataset": ds, "profiling": {"mode": "standard"},
        "quality_checks": rules or [
            {"id": "r1", "rule": "not_null", "column": "amount",
             "dimension": "completeness", "severity": "high"}
        ],
    })


def _findings_for(checks=(), profiles=(), dup=None, row_count=100, pk=None, config=None):
    cfg = config or _config(pk)
    return build_findings(
        DS, RUN_ID, cfg, list(profiles), list(checks),
        duplicate_group_counts=dup, row_count=row_count,
    ).findings


# ---------------------------------------------------------------------------
# §4.4 / ADR-016 fingerprint law
# ---------------------------------------------------------------------------


class TestFingerprint:
    def test_same_issue_different_value_same_fingerprint(self):
        f_lo = _findings_for([_check("deal_value_missing_rate", "completeness", "FAIL", "deal_value", value=0.15, failing=30)])
        f_hi = _findings_for([_check("deal_value_missing_rate", "completeness", "FAIL", "deal_value", value=0.42, failing=90)])
        assert f_lo[0].fingerprint == f_hi[0].fingerprint

    def test_different_column_different_fingerprint(self):
        a = _findings_for([_check("r", "completeness", "FAIL", "amount", value=0.1, failing=1)])
        b = _findings_for([_check("r", "completeness", "FAIL", "region", value=0.1, failing=1)])
        assert a[0].fingerprint != b[0].fingerprint

    def test_different_rule_id_different_fingerprint(self):
        a = _findings_for([_check("rule_alpha", "validity", "FAIL", "amount", value=1, failing=1)])
        b = _findings_for([_check("rule_beta", "validity", "FAIL", "amount", value=1, failing=1)])
        assert a[0].fingerprint != b[0].fingerprint

    def test_fingerprint_pure_function_is_deterministic(self):
        sk = build_scope_key(DS, "amount")
        h1 = compute_fingerprint("completeness", sk, "quality_rule", "amount_missing_rate")
        h2 = compute_fingerprint("completeness", sk, "quality_rule", "amount_missing_rate")
        assert h1 == h2 and h1.startswith("sha256:") and len(h1) == 71

    def test_dataset_scope_key_ignores_none_column(self):
        assert build_scope_key(DS, None) == build_scope_key(DS, None, table=None)

    def test_table_disambiguates_scope_key(self):
        assert build_scope_key(DS, "amount", "t1") != build_scope_key(DS, "amount", "t2")


# ---------------------------------------------------------------------------
# §6.1 rule detector — FAIL ≠ ERROR
# ---------------------------------------------------------------------------


class TestRuleDetector:
    def test_fail_and_warn_become_findings(self):
        fs = _findings_for([
            _check("r_fail", "completeness", "FAIL", "amount", value=1, failing=1),
            _check("r_warn", "accuracy_flagged", "WARN", "amount", value=2, failing=2, severity="warning"),
        ])
        assert {f.severity for f in fs} >= {"high", "warning"}
        assert len(fs) == 2

    def test_pass_produces_nothing(self):
        assert _findings_for([_check("r_pass", "completeness", "PASS", "amount", value=0, failing=0)]) == ()

    def test_error_produces_nothing(self):
        # an un-executable rule is ERROR, never a proven data problem → no finding
        assert _findings_for([_check("r_err", "validity", "ERROR", "amount")]) == ()

    def test_fact_carries_provenance_method(self):
        f = _findings_for([_check("r", "validity", "FAIL", "amount", value=3, failing=3)])[0]
        assert f.facts[0].provenance.method == "exact"

    def test_confidence_mirrors_method(self):
        exact = _findings_for([_check("r_e", "validity", "FAIL", "amount", value=1, failing=1)])[0]
        approx = _findings_for([_check("r_a", "validity", "FAIL", "amount", value=1, failing=1, method="approximate", params={"algorithm": "HLL"})])[0]
        sampled = _findings_for([_check("r_s", "validity", "FAIL", "amount", value=1, failing=1, method="sampled", params={"seed": 7})])[0]
        assert (exact.confidence, approx.confidence, sampled.confidence) == ("high", "medium", "low")

    def test_evidence_refs_include_rule_and_metric(self):
        f = _findings_for([_check("deal_value_missing_rate", "completeness", "FAIL", "deal_value", value=0.15, failing=30)])[0]
        assert set(f.evidence_refs) == {"metric:deal_value_missing_rate", "rule:deal_value_missing_rate"}


# ---------------------------------------------------------------------------
# §6.1 profile-extreme detectors
# ---------------------------------------------------------------------------


class TestProfileDetectors:
    def test_near_total_missingness(self):
        p = _col("region", 0, missing_rate=0.95)
        fs = _findings_for(profiles=[p])
        assert any(f.category == "completeness" and "95" in f.summary for f in fs)

    def test_constant_column(self):
        p = _col("status", 0, distinct=1, denom=100)
        fs = _findings_for(profiles=[p])
        assert any(f.category == "uniqueness" and "single distinct" in f.summary for f in fs)

    def test_dominant_category(self):
        p = _col("tier", 0, categories=["A", "B"], frequencies=[98, 2])
        fs = _findings_for(profiles=[p])
        assert any(f.category == "accuracy_flagged" for f in fs)

    def test_duplicate_group_auxiliary(self):
        p = _col("email", 0)
        fs = _findings_for(profiles=[p], dup={"email": 5})
        assert any(f.category == "uniqueness" and "duplicated key" in f.summary for f in fs)

    def test_below_thresholds_are_silent(self):
        p = _col(
            "x", 0, missing_rate=0.5, distinct=7, denom=100,
            categories=["A", "B"], frequencies=[55, 45],
        )
        assert _findings_for(profiles=[p]) == ()

    def test_constant_needs_two_rows(self):
        # a single-row relation is not "constant" — no discrimination signal yet
        p = _col("y", 0, distinct=1, denom=1)
        assert not any(f.category == "uniqueness" for f in _findings_for(profiles=[p]))

    def test_all_profile_findings_are_warn_severity(self):
        fs = _findings_for(profiles=[
            _col("a", 0, missing_rate=0.99),
            _col("b", 1, distinct=1, denom=100),
        ])
        assert all(f.severity == "warning" for f in fs)

    def test_detector_thresholds_are_boundaries(self):
        just_below = _col("z", 0, missing_rate=NEAR_TOTAL_MISSINGNESS - 0.001)
        at = _col("w", 0, missing_rate=NEAR_TOTAL_MISSINGNESS)
        assert _findings_for(profiles=[just_below]) == ()
        assert len(_findings_for(profiles=[at])) == 1
        assert DOMINANT_CATEGORY_SHARE == 0.95


# ---------------------------------------------------------------------------
# §6.3 dedup / §6.2 prioritization
# ---------------------------------------------------------------------------


class TestDedupAndPriority:
    def test_dedup_collapses_same_column_category(self):
        # rule completeness FAIL + profile completeness near-total-missingness
        # on the SAME column → one merged completeness finding
        checks = [_check("miss_rate_rule", "completeness", "FAIL", "deal_value", value=0.95, failing=95, operator="<=", threshold_value=0.05)]
        profiles = [_col("deal_value", 0, missing_rate=0.95)]
        fs = _findings_for(checks, profiles, row_count=100)
        comp = [f for f in fs if f.category == "completeness" and f.scope.column == "deal_value"]
        assert len(comp) == 1
        refs = set(comp[0].evidence_refs)
        assert "rule:miss_rate_rule" in refs and any(r.startswith("metric:deal_value") for r in refs)

    def test_priority_orders_most_urgent_first(self):
        fs = _findings_for([
            _check("low_one", "validity", "FAIL", "notes", value=1, failing=1, severity="low"),
            _check("crit_one", "validity", "FAIL", "amount", value=1, failing=1, severity="critical"),
        ])
        assert fs[0].finding_id == "F-001" and fs[0].severity == "critical"

    def test_critical_pk_outranks_noncritical_equal_severity(self):
        fs = _findings_for(
            [
                _check("r_a", "validity", "FAIL", "deal_id", value=1, failing=1),
                _check("r_b", "validity", "FAIL", "notes", value=1, failing=1),
            ],
            pk=["deal_id"],
        )
        assert fs[0].scope.column == "deal_id"

    def test_deterministic_ordering_across_reruns(self):
        checks = [_check(f"r{i}", "validity", "FAIL", f"c{i}", value=1, failing=1) for i in range(4)]
        a = _findings_for(checks)
        b = _findings_for(checks)
        assert [f.fingerprint for f in a] == [f.fingerprint for f in b]


# ---------------------------------------------------------------------------
# §4.2 contract shape + MVP invariants
# ---------------------------------------------------------------------------


class TestContractShape:
    def _all_kinds(self):
        return _findings_for(
            [_check("miss_rate_rule", "completeness", "FAIL", "deal_value", value=0.15, failing=30, operator="<=", threshold_value=0.05)],
            [_col("region", 0, missing_rate=0.95), _col("status", 1, distinct=1, denom=100)],
            dup={"email": 3},
        )

    def test_every_finding_schema_valid(self):
        for f in self._all_kinds():
            errs = S.validate("finding.schema.json", f.model_dump(mode="json"))
            assert errs == [], (f.finding_id, errs)

    def test_workflow_null_and_id_format(self):
        for f in self._all_kinds():
            assert f.workflow is None
            assert f.finding_id.startswith("F-") and len(f.finding_id) == 5

    def test_hypotheses_only_unverified(self):
        for f in self._all_kinds():
            assert all(h.status == "unverified" for h in f.hypotheses)

    def test_facts_min_one_and_evidence_min_one(self):
        for f in self._all_kinds():
            assert len(f.facts) >= 1 and len(f.evidence_refs) >= 1


# ---------------------------------------------------------------------------
# integration through the real quality executor
# ---------------------------------------------------------------------------


SALES_ROWS = (
    "(CAST(1 AS BIGINT), 100.0, 'Active'),"
    "(CAST(2 AS BIGINT), 200.0, 'Closed Won'),"
    "(CAST(3 AS BIGINT), NULL, 'Closed Won'),"
    "(CAST(4 AS BIGINT), -50.0, 'Refund')"
)


def _sales_handle(con) -> RelationHandle:
    con.execute(
        'CREATE OR REPLACE VIEW "sales" AS SELECT CAST(v0 AS BIGINT) AS "id", '
        'CAST(v1 AS DOUBLE) AS "amount", CAST(v2 AS VARCHAR) AS "status", '
        "row_number() OVER () AS _row_id "
        f"FROM (VALUES {SALES_ROWS}) t(v0, v1, v2)"
    )
    return RelationHandle(
        dataset_id=DS, connector="manual", view_name="sales",
        snapshot=SourceSnapshot(content_hash="sha256:" + "0" * 64),
        columns=(("id", "BIGINT"), ("amount", "DOUBLE"), ("status", "VARCHAR")),
        row_id_column="_row_id",
    )


def test_integration_quality_to_findings():
    con = duckdb.connect()
    handle = _sales_handle(con)
    cfg = _config(rules=[
        {"id": "amount_not_null", "rule": "not_null", "column": "amount",
         "dimension": "completeness", "severity": "high"},
        {"id": "amount_gte_zero", "rule": "greater_than_or_equal", "column": "amount",
         "dimension": "validity", "severity": "medium", "value": 0},
    ])
    quality = run_quality_checks(con, handle, cfg, RUN_ID)
    # amount has 1 null → not_null FAIL; amount has -50 → gte FAIL
    assert {c.rule_id: c.status.value if hasattr(c.status, "value") else c.status for c in quality.checks} == {
        "amount_not_null": "FAIL", "amount_gte_zero": "FAIL",
    }
    fs = build_findings(
        DS, RUN_ID, cfg, profiles=[], checks=quality.checks, row_count=4
    ).findings
    assert len(fs) == 2
    for f in fs:
        assert S.validate("finding.schema.json", f.model_dump(mode="json")) == []
    # facts cite the rule metric with exact provenance (rules run exact)
    for f in fs:
        assert f.facts[0].provenance.method == "exact"
