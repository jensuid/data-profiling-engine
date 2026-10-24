"""M2-T7 reference tests — rule registry + compiler → one SQL per rule;
statuses PASS / FAIL / WARN / ERROR (ENGINE §5.1–5.3; CONTRACTS §4.1).

TESTING §3.1: each §5.1 rule type is asserted against an **independent,
hand-computed** pass/fail on a fixed fixture (plus a couple of Polars cross
checks), never against the engine's own SQL. The engine owns the compiler
(ADR-012), so an independent reference is mandatory. Coverage:

- Every MVP rule type vs its hand verdict (not_null / unique / missing_rate_max
  / greater_than_or_equal / less_than_or_equal / range / valid_domain /
  type_valid / conditional_not_null / outlier_scan).
- FAIL ≠ ERROR: a broken / absent / Phase-2 rule is ERROR (observed null + error
  object), never FAIL; §6.4 isolation — one broken rule does not abort siblings.
- WARN policy (§5.3): outlier_scan, business_note, warning-severity never
  hard-fail.
- Bound-input discipline (ADR-017): a value carrying a quote matches literally
  and cannot break the statement.
- Evidence = ``row:N`` refs (SECURITY §4), capped at ≤200 (ENGINE §5.2).
- Every emitted QualityCheckResult validates against the frozen
  quality_check_result.schema.json (§4.1) with format checking on.

HAND fixture "sales" (7 rows) — id / amount / status / region / closed_at:
  1  100  'Active'      'NA'    NULL
  2  200  'Closed Won'  'EU'    '2026-01-05'
  3  NULL 'Closed Won'  NULL    NULL      (amount null; Closed Won missing closed_at)
  4  -50  'Refund'      'APAC'  NULL      (negative amount)
  5  300  'Active'      NULL    NULL      (region null)
  6  300  'Active'      'EU'    '2026-02-01'
  7  250  'Closed Won'  'NA'    '2026-03-03'

Derived facts: id no nulls / unique; amount nulls 1, negatives 1 (row4),
  >250 → {300,300} = 2; status {Active×3, Closed Won×3, Refund×1} → 4 duplicate
  extras; region nulls 2; Closed Won rows = {2,3,7}, of which closed_at NULL =
  {3} → conditional violation 1 of total 3.
"""
from __future__ import annotations

import math
from typing import List

import duckdb
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery.types import LogicalType
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.quality import (
    catalog,
    compile_rule,
    run_quality_checks,
)
from data_profiling_engine.quality.compiler import _SAMPLE_REF_CAP
from data_profiling_engine.schemas.models import (
    CheckStatus,
    DatasetConfig,
    DatasetConfigRule,
    MetricMethod,
    QualityDimension,
    RuleType,
    Severity,
    SourceSnapshot,
)

SALES_VIEW = "sales"
_SALES_ROWS = (
    "(CAST(1 AS BIGINT), 100.0, 'Active', 'NA', NULL),"
    "(CAST(2 AS BIGINT), 200.0, 'Closed Won', 'EU', '2026-01-05'),"
    "(CAST(3 AS BIGINT), NULL, 'Closed Won', NULL, NULL),"
    "(CAST(4 AS BIGINT), -50.0, 'Refund', 'APAC', NULL),"
    "(CAST(5 AS BIGINT), 300.0, 'Active', NULL, NULL),"
    "(CAST(6 AS BIGINT), 300.0, 'Active', 'EU', '2026-02-01'),"
    "(CAST(7 AS BIGINT), 250.0, 'Closed Won', 'NA', '2026-03-03')"
)
_SALES_COLS = (
    ("id", "BIGINT"), ("amount", "DOUBLE"), ("status", "VARCHAR"),
    ("region", "VARCHAR"), ("closed_at", "VARCHAR"),
)


def _make_handle(con, view, rows, columns) -> RelationHandle:
    n = len(columns)
    casts = ", ".join(
        f'CAST(v{i} AS {typ}) AS "{name}"' for i, (name, typ) in enumerate(columns)
    )
    vcols = ", ".join(f"v{i}" for i in range(n))
    con.execute(
        f'CREATE OR REPLACE VIEW "{view}" AS SELECT {casts}, '
        f"row_number() OVER () AS _row_id FROM (VALUES {rows}) t({vcols})"
    )
    return RelationHandle(
        dataset_id="ds", connector="manual", view_name=view,
        snapshot=SourceSnapshot(content_hash="sha256:" + "0" * 64),
        columns=tuple(columns), row_id_column="_row_id",
    )


def _sales(con) -> RelationHandle:
    return _make_handle(con, SALES_VIEW, _SALES_ROWS, _SALES_COLS)


def _cfg(rules: List[dict]) -> DatasetConfig:
    return DatasetConfig.model_validate({
        "config_version": 3,
        "dataset": {"name": "sales"},
        "profiling": {"mode": "standard"},
        "quality_checks": rules,
    })


def _rule(rid, rtype, column, **kw) -> dict:
    out = {"id": rid, "rule": rtype, "column": column,
           "dimension": kw.pop("dimension", "validity"),
           "severity": kw.pop("severity", "high")}
    out.update(kw)
    return out


def _run(con, cfg, run_id="run_test_0001", discovery=None, executed_at="2026-10-24T09:00:00Z"):
    handle = con_handle[con]
    res = run_quality_checks(con, handle, cfg, run_id, discovery=discovery,
                            executed_at=executed_at)
    by_id = {c.rule_id: c for c in res.checks}
    return res, by_id


con_handle: "dict[duckdb.DuckDBPyConnection, RelationHandle]" = {}


@pytest.fixture
def con():
    c = duckdb.connect()
    con_handle[c] = _sales(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Registry (ENGINE §5.1 closed catalog)
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_catalog_covers_every_mvp_rule_type(self):
        cat = catalog()
        assert set(cat) == set(RuleType)

    def test_phase2_types_are_inactive(self):
        assert catalog()[RuleType.REFERENTIAL_INTEGRITY].mvp_active is False
        assert catalog()[RuleType.DATE_ORDER].mvp_active is False

    def test_outlier_scan_is_warn_only(self):
        assert catalog()[RuleType.OUTLIER_SCAN].warn_only is True

    def test_default_dimensions_match_5_1(self):
        cat = catalog()
        assert cat[RuleType.NOT_NULL].default_dimension == QualityDimension.COMPLETENESS
        assert cat[RuleType.UNIQUE].default_dimension == QualityDimension.UNIQUENESS
        assert cat[RuleType.OUTLIER_SCAN].default_dimension == QualityDimension.ACCURACY_FLAGGED


# ---------------------------------------------------------------------------
# Each §5.1 MVP rule type vs an independent hand verdict
# ---------------------------------------------------------------------------


class TestRuleTypes:
    def test_not_null_pass_and_fail(self, con):
        _, by = _run(con, _cfg([
            _rule("id_nn", "not_null", "id", dimension="completeness"),
            _rule("amt_nn", "not_null", "amount", dimension="completeness"),
        ]))
        assert by["id_nn"].status == CheckStatus.PASS
        assert by["id_nn"].failing_record_count == 0
        # hand: amount has 1 null (row3)
        assert by["amt_nn"].status == CheckStatus.FAIL
        assert by["amt_nn"].failing_record_count == 1
        assert by["amt_nn"].observed.value == 1

    def test_unique_pass_and_fail(self, con):
        _, by = _run(con, _cfg([
            _rule("id_u", "unique", "id", dimension="uniqueness"),
            _rule("status_u", "unique", "status", dimension="uniqueness"),
        ]))
        assert by["id_u"].status == CheckStatus.PASS
        # hand: status dup extras = (3-1)+(3-1)+(1-1) = 4
        assert by["status_u"].status == CheckStatus.FAIL
        assert by["status_u"].failing_record_count == 4
        assert by["status_u"].observed.denominator == 7  # non-null population

    def test_missing_rate_max(self, con):
        # amount: 1 missing / 7 = 0.142857
        _, by = _run(con, _cfg([
            _rule("amt_rate_fail", "missing_rate_max", "amount",
                  dimension="completeness", value=0.05),
            _rule("amt_rate_pass", "missing_rate_max", "amount",
                  dimension="completeness", value=0.2),
        ]))
        rate = by["amt_rate_fail"].observed.value
        assert math.isclose(rate, 1 / 7, rel_tol=1e-9)
        assert by["amt_rate_fail"].status == CheckStatus.FAIL
        assert by["amt_rate_pass"].status == CheckStatus.PASS
        assert by["amt_rate_fail"].threshold.value == 0.05
        assert by["amt_rate_fail"].observed.denominator == 7

    def test_greater_than_or_equal(self, con):
        _, by = _run(con, _cfg([
            _rule("amt_gte", "greater_than_or_equal", "amount", value=0),
        ]))
        # hand: amount < 0 → {row4 -50} = 1 violation
        assert by["amt_gte"].status == CheckStatus.FAIL
        assert by["amt_gte"].failing_record_count == 1
        assert by["amt_gte"].threshold.value == 0

    def test_less_than_or_equal(self, con):
        _, by = _run(con, _cfg([
            _rule("amt_lte", "less_than_or_equal", "amount", value=250),
        ]))
        # hand: amount > 250 → {300, 300} = 2
        assert by["amt_lte"].status == CheckStatus.FAIL
        assert by["amt_lte"].failing_record_count == 2

    def test_range(self, con):
        _, by = _run(con, _cfg([
            _rule("amt_range", "range", "amount", value=[0, 250]),
        ]))
        # hand: outside [0,250] → -50, 300, 300 = 3
        assert by["amt_range"].status == CheckStatus.FAIL
        assert by["amt_range"].failing_record_count == 3
        assert by["amt_range"].threshold.value == 0
        assert by["amt_range"].threshold.upper == 250

    def test_valid_domain(self, con):
        _, by = _run(con, _cfg([
            _rule("status_ok", "valid_domain", "status",
                  value=["Active", "Closed Won", "Refund"]),
            _rule("status_bad", "valid_domain", "status", value=["Active"]),
        ]))
        assert by["status_ok"].status == CheckStatus.PASS
        # hand: non-null not in {Active} → Closed Won×3 + Refund×1 = 4
        assert by["status_bad"].status == CheckStatus.FAIL
        assert by["status_bad"].failing_record_count == 4

    def test_conditional_not_null(self, con):
        _, by = _run(con, _cfg([
            _rule("cw_closed_at", "conditional_not_null", "closed_at",
                  dimension="completeness",
                  condition={"column": "status", "operator": "equals", "value": "Closed Won"}),
            _rule("no_match", "conditional_not_null", "closed_at",
                  dimension="completeness",
                  condition={"column": "status", "operator": "equals", "value": "Ghost"}),
        ]))
        # hand: Closed Won rows {2,3,7}; closed_at NULL among them = {3} → 1 of 3
        assert by["cw_closed_at"].status == CheckStatus.FAIL
        assert by["cw_closed_at"].failing_record_count == 1
        assert by["cw_closed_at"].evidence.denominator == 3
        # no matching condition rows → 0 violations → PASS
        assert by["no_match"].status == CheckStatus.PASS

    def test_type_valid_e2e_pass_and_direct_fail(self, con):
        # (a) e2e: a physically-typed column is trivially valid → PASS
        _, by = _run(con, _cfg([
            _rule("id_tv", "type_valid", "id", dimension="validity"),
        ]))
        assert by["id_tv"].status == CheckStatus.PASS

        # (b) direct: a text column declared INTEGER, one unparseable value
        con.execute(
            'CREATE OR REPLACE VIEW "qt" AS SELECT v AS qty_text, '
            "row_number() OVER () AS _row_id FROM (VALUES "
            "('10'),('20'),('abc'),('40')) t(v)"
        )
        from data_profiling_engine.schemas.models import SourceSnapshot as _SS
        h = RelationHandle(
            dataset_id="q", connector="manual", view_name="qt",
            snapshot=_SS(content_hash="sha256:" + "0" * 64),
            columns=(("qty_text", "VARCHAR"),), row_id_column="_row_id",
        )
        rule = DatasetConfigRule.model_validate(
            _rule("qt_tv", "type_valid", "qty_text", dimension="validity")
        )
        compiled = compile_rule(rule, h, logical_type=LogicalType.INTEGER)
        row = con.execute(compiled.sql, compiled.params).fetchone()
        assert row[0] == 1  # hand: only 'abc' fails TRY_CAST to BIGINT
        assert row[1] == 4


# ---------------------------------------------------------------------------
# Outlier scan → WARN (never hard-fail)
# ---------------------------------------------------------------------------


class TestOutlierScan:
    def _series(self, con, view, values):
        rows = "),(".join(f"{v}" for v in values)
        con.execute(
            f'CREATE OR REPLACE VIEW "{view}" AS SELECT c AS amt, '
            f"row_number() OVER () AS _row_id FROM (VALUES ({rows})) t(c)"
        )
        from data_profiling_engine.schemas.models import SourceSnapshot as _SS
        return RelationHandle(
            dataset_id=view, connector="manual", view_name=view,
            snapshot=_SS(content_hash="sha256:" + "0" * 64),
            columns=(("amt", "DOUBLE"),), row_id_column="_row_id",
        )

    def test_outlier_flags_review_not_fail(self, con):
        # hand Tukey: [1..7,100] → q1=2.75 q3=6.25 IQR=3.5 fences [-2.5,11.5]
        # 100 is the only candidate.
        h = self._series(con, "out1", [1, 2, 3, 4, 5, 6, 7, 100])
        cfg = _cfg([_rule("o1", "outlier_scan", "amt",
                          dimension="accuracy_flagged", severity="info")])
        res = run_quality_checks(con, h, cfg, "run_o1",
                                 executed_at="2026-10-24T09:00:00Z")
        chk = res.checks[0]
        assert chk.status == CheckStatus.WARN
        assert chk.failing_record_count == 1
        # fences surfaced in threshold
        assert chk.threshold.value is not None and chk.threshold.upper is not None
        assert math.isclose(chk.threshold.value, -2.5, rel_tol=1e-9)
        assert math.isclose(chk.threshold.upper, 11.5, rel_tol=1e-9)

    def test_no_outliers_pass(self, con):
        h = self._series(con, "out2", [10, 11, 12, 13, 14, 15, 16, 17])
        cfg = _cfg([_rule("o2", "outlier_scan", "amt",
                          dimension="accuracy_flagged", severity="info")])
        res = run_quality_checks(con, h, cfg, "run_o2",
                                executed_at="2026-10-24T09:00:00Z")
        assert res.checks[0].status == CheckStatus.PASS


# ---------------------------------------------------------------------------
# WARN policy from business_note / warning severity (§5.3)
# ---------------------------------------------------------------------------


class TestWarnPolicy:
    def test_business_note_violation_is_warn_not_fail(self, con):
        cfg = _cfg([_rule(
            "rev_neg", "greater_than_or_equal", "amount", value=0,
            severity="warning", dimension="validity",
            business_note="negative may be valid for refunds; review",
        )])
        _, by = _run(con, cfg)
        # hand: row4 -50 violates >=0 → positive, but WARN (never hard-fail)
        assert by["rev_neg"].status == CheckStatus.WARN
        assert by["rev_neg"].failing_record_count == 1

    def test_business_note_makes_fail_into_warn(self, con):
        cfg = _cfg([_rule(
            "rev_neg_note", "greater_than_or_equal", "amount", value=0,
            severity="high", dimension="validity",
            business_note="refund rows legitimately negative",
        )])
        _, by = _run(con, cfg)
        assert by["rev_neg_note"].status == CheckStatus.WARN


# ---------------------------------------------------------------------------
# FAIL ≠ ERROR + §6.4 isolation
# ---------------------------------------------------------------------------


class TestErrorSemantics:
    def test_missing_column_is_error_not_fail(self, con):
        cfg = _cfg([_rule("ghost_nn", "not_null", "does_not_exist",
                          dimension="completeness")])
        res, by = _run(con, cfg)
        chk = by["ghost_nn"]
        assert chk.status == CheckStatus.ERROR
        assert chk.error is not None
        assert chk.error.code == ErrorCode.RULE_EXEC_FAILED.value
        assert chk.observed is None
        assert chk.failing_record_count is None
        # §6.4 error_log row present
        assert any(e.code == ErrorCode.RULE_EXEC_FAILED.value for e in res.errors)

    def test_phase2_rule_is_error(self, con):
        cfg = _cfg([_rule("ri", "referential_integrity", "id",
                          dimension="consistency")])
        _, by = _run(con, cfg)
        assert by["ri"].status == CheckStatus.ERROR
        assert "Phase-2" in (by["ri"].error.message or "")

    def test_broken_execution_is_error(self, con):
        # outlier_scan over a non-numeric VARCHAR column: quantile_cont(CAST
        # AS DOUBLE) on 'Active'/'Closed Won' raises a DuckDB conversion error
        # → ERROR (execution failure), not FAIL.
        cfg = _cfg([_rule("bad_out", "outlier_scan", "status",
                          dimension="accuracy_flagged")])
        _, by = _run(con, cfg)
        assert by["bad_out"].status == CheckStatus.ERROR
        assert by["bad_out"].observed is None
        assert by["bad_out"].error.code == ErrorCode.RULE_EXEC_FAILED.value

    def test_one_broken_rule_does_not_abort_siblings(self, con):
        cfg = _cfg([
            _rule("ghost", "not_null", "does_not_exist", dimension="completeness"),
            _rule("id_nn", "not_null", "id", dimension="completeness"),
        ])
        res, by = _run(con, cfg)
        assert by["ghost"].status == CheckStatus.ERROR
        assert by["id_nn"].status == CheckStatus.PASS  # sibling preserved
        assert len(res.checks) == 2
        assert len(res.errors) == 1

    def test_malformed_range_param_is_error(self, con):
        cfg = DatasetConfig.model_validate({
            "config_version": 3,
            "dataset": {"name": "sales"},
            "profiling": {"mode": "standard"},
            "quality_checks": [{"id": "r", "rule": "range", "column": "amount",
                                "dimension": "validity", "severity": "high",
                                "value": 5}],  # not a [low, high] pair
        })
        _, by = _run(con, cfg)
        assert by["r"].status == CheckStatus.ERROR


# ---------------------------------------------------------------------------
# Bound-input discipline (ADR-017) + evidence refs
# ---------------------------------------------------------------------------


class TestBindingAndEvidence:
    def test_value_with_quote_matches_literally(self, con):
        con.execute(
            'CREATE OR REPLACE VIEW "quo" AS SELECT s AS tag, '
            "row_number() OVER () AS _row_id FROM (VALUES "
            "('it''s ok'), ('keep'), ('drop')) t(s)"
        )
        h = RelationHandle(
            dataset_id="q", connector="manual", view_name="quo",
            snapshot=SourceSnapshot(content_hash="sha256:" + "0" * 64),
            columns=(("tag", "VARCHAR"),), row_id_column="_row_id",
        )
        cfg = _cfg([_rule("dom", "valid_domain", "tag",
                          value=["it's ok", "keep"])])
        res = run_quality_checks(con, h, cfg, "run_quo",
                                executed_at="2026-10-24T09:00:00Z")
        chk = res.checks[0]
        # 'drop' not in domain → 1 violation; the apostrophe value binds safely
        assert chk.status == CheckStatus.FAIL
        assert chk.failing_record_count == 1

    def test_evidence_refs_are_row_pointers(self, con):
        cfg = _cfg([_rule("amt_nn", "not_null", "amount", dimension="completeness")])
        _, by = _run(con, cfg)
        refs = by["amt_nn"].evidence.sample_row_refs
        assert refs == ["row:3"]  # the only null amount is data-line 3
        assert all(r.startswith("row:") for r in refs)

    def test_sample_refs_capped(self, con):
        rows = "),(".join(str(i) for i in range(1, 301))
        con.execute(
            f'CREATE OR REPLACE VIEW "big" AS SELECT v AS n, '
            f"row_number() OVER () AS _row_id FROM (VALUES ({rows})) t(v)"
        )
        from data_profiling_engine.schemas.models import SourceSnapshot as _SS
        h = RelationHandle(
            dataset_id="b", connector="manual", view_name="big",
            snapshot=_SS(content_hash="sha256:" + "0" * 64),
            columns=(("n", "BIGINT"),), row_id_column="_row_id",
        )
        cfg = _cfg([_rule("nn", "not_null", "n", dimension="completeness")])
        # make every row violate: check a column that is NULL? use range bound 0
        # so 300 rows violate → refs capped
        cfg = _cfg([_rule("gte", "greater_than_or_equal", "n", value=1000)])
        res = run_quality_checks(con, h, cfg, "run_big",
                                executed_at="2026-10-24T09:00:00Z")
        chk = res.checks[0]
        assert chk.failing_record_count == 300
        assert len(chk.evidence.sample_row_refs) == _SAMPLE_REF_CAP


# ---------------------------------------------------------------------------
# Contract conformance (§4.1 schema) + provenance honesty
# ---------------------------------------------------------------------------


class TestContracts:
    def test_every_result_is_schema_valid(self, con):
        cfg = _cfg([
            _rule("id_nn", "not_null", "id", dimension="completeness"),
            _rule("status_u", "unique", "status", dimension="uniqueness"),
            _rule("amt_rate", "missing_rate_max", "amount",
                  dimension="completeness", value=0.05),
            _rule("o", "outlier_scan", "amount", dimension="accuracy_flagged"),
            _rule("ghost", "not_null", "absent", dimension="completeness"),
        ])
        res, _ = _run(con, cfg)
        for chk in res.checks:
            errs = S.validate("quality_check_result.schema.json",
                              chk.model_dump(mode="json"))
            assert errs == [], f"{chk.rule_id}: {errs}"

    def test_non_error_observations_are_exact(self, con):
        cfg = _cfg([_rule("amt_nn", "not_null", "amount", dimension="completeness")])
        _, by = _run(con, cfg)
        assert by["amt_nn"].observed.provenance.method == MetricMethod.EXACT.value

    def test_error_and_pass_carry_consistent_shape(self, con):
        cfg = _cfg([
            _rule("id_nn", "not_null", "id", dimension="completeness"),
            _rule("ghost", "not_null", "absent", dimension="completeness"),
        ])
        _, by = _run(con, cfg)
        # PASS has an observed value and no error
        assert by["id_nn"].observed is not None and by["id_nn"].error is None
        # ERROR has no observed value and an error object
        assert by["ghost"].observed is None and by["ghost"].error is not None

    def test_dimension_severity_run_id_carried_verbatim(self, con):
        cfg = _cfg([_rule("id_nn", "not_null", "id",
                          dimension="completeness", severity="critical")])
        _, by = _run(con, cfg, run_id="run_zz_9")
        chk = by["id_nn"]
        assert chk.dimension == QualityDimension.COMPLETENESS
        assert chk.severity == Severity.CRITICAL
        assert chk.run_id == "run_zz_9"
