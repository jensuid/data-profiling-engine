"""M2-T9 reference tests — recommendation engine (rule-based templates +
narrative with evidence refs). ENGINE §7; CONTRACTS §4.3 / §7.

TESTING §3.1 (the engine owns the code, so test against independent references):
the "independent reference" for recommendations is the *no-fabrication law* —
every recommendation must (a) cite the finding's evidence refs **verbatim**
(never invent a ref or a number), (b) map a detector to the one template ENGINE
§7 expects for it, (c) reserve ``business_context_required`` for review signals
whose cause is not proven, and (d) validate against the frozen
``recommendation.schema.json``. Coverage also asserts the finding → recommendation
back-reference (``Finding.recommendation_ids``) M2-T8 left empty is now wired.
"""
from __future__ import annotations

from typing import List, Optional

from data_profiling_engine import schemas as S
from data_profiling_engine.findings import build_findings
from data_profiling_engine.recommendations import build_recommendations
from data_profiling_engine.recommendations.templates import (
    DEFAULT_LANGUAGE,
    TEMPLATES,
    TEMPLATES_BY_LANGUAGE,
    classify_finding_kind,
    templates_for_language,
)
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    DatasetConfig,
    Finding,
    QualityCheckResult,
    ReasonType,
    Recommendation,
)

RUN_ID = "run_rec_0001"
DS = "sales"


# ---------------------------------------------------------------------------
# fixture builders (same shape as the M2-T8 findings tests)
# ---------------------------------------------------------------------------


def _mv(value, method="exact", denominator=100):
    mv = {"value": value, "provenance": {"method": method}}
    if denominator is not None:
        mv["denominator"] = denominator
    return mv


def _check(
    rule_id: str, dimension: str, status: str, column: Optional[str],
    value=1, severity="high", failing=5,
) -> QualityCheckResult:
    return QualityCheckResult.model_validate({
        "schema_version": "1.0", "run_id": RUN_ID, "rule_id": rule_id,
        "rule_version": "1", "dimension": dimension, "status": status,
        "severity": severity, "column": column,
        "executed_at": "2026-10-24T09:00:00Z",
        "observed": _mv(value), "failing_record_count": failing, "threshold": None,
    })


def _col(
    column: str, ordinal: int, *, missing_rate=None, distinct=None, denom=None,
    categories=None, frequencies=None,
) -> ColumnProfile:
    p = {
        "schema_version": "1.0", "column": column, "ordinal": ordinal,
        "physical_type": "VARCHAR", "logical_type": "string", "semantic_type": "dimension",
    }
    if missing_rate is not None:
        p["missingness"] = {
            "null_count": int(missing_rate * 100), "blank_count": 0, "sentinel_count": 0,
            "missing_rate": _mv(missing_rate),
        }
    if distinct is not None:
        p["uniqueness"] = {
            "distinct_count": _mv(distinct, denominator=denom if denom is not None else 100),
            "is_unique_candidate": distinct == (denom or 100),
        }
    if categories is not None:
        p["categorical"] = {"categories": categories, "frequencies": frequencies}
    return ColumnProfile.model_validate(p)


def _config() -> DatasetConfig:
    return DatasetConfig.model_validate({
        "config_version": 3, "dataset": {"name": DS},
        "profiling": {"mode": "standard"},
        "quality_checks": [
            {"id": "r1", "rule": "not_null", "column": "amount",
             "dimension": "completeness", "severity": "high"}
        ],
    })


def _findings(checks=(), profiles=(), dup=None, row_count=100) -> tuple:
    return build_findings(
        DS, RUN_ID, _config(), list(profiles), list(checks),
        duplicate_group_counts=dup, row_count=row_count,
    ).findings


def _only_finding(**kw) -> Finding:
    fs = _findings(**kw)
    assert len(fs) == 1, f"expected 1 finding, got {len(fs)}"
    return fs[0]


def _rec_for(**kw):
    f = _only_finding(**kw)
    return f, build_recommendations([f]).recommendations[0]


# ---------------------------------------------------------------------------
# §7 detector → template kind mapping
# ---------------------------------------------------------------------------


class TestKindClassification:
    def test_rule_finding_classifies_by_category(self):
        f = _only_finding(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])
        assert classify_finding_kind(f) == "rule:completeness"

    def test_profile_missingness_extreme_kind(self):
        f = _only_finding(profiles=[_col("region", 1, missing_rate=0.95)])
        assert classify_finding_kind(f) == "profile:completeness:missingness"

    def test_constant_column_kind(self):
        f = _only_finding(profiles=[_col("flag", 1, distinct=1, denom=100)])
        assert classify_finding_kind(f) == "profile:uniqueness:constant_column"

    def test_duplicate_key_kind(self):
        f = _only_finding(profiles=[_col("order_id", 1)], dup={"order_id": 4})
        assert classify_finding_kind(f) == "profile:uniqueness:duplicate_key"

    def test_dominant_category_kind(self):
        f = _only_finding(profiles=[_col(
            "status", 1, categories=["Won", "Lost"], frequencies=[98, 2],
        )])
        assert classify_finding_kind(f) == "profile:accuracy_flagged:dominant_category"

    def test_every_kind_resolves_to_a_template(self):
        for f in _findings(
            checks=[_check("r", "validity", "FAIL", "amount")],
            profiles=[_col("region", 1, missing_rate=0.99)],
        ):
            assert classify_finding_kind(f) in TEMPLATES


# ---------------------------------------------------------------------------
# §4.3 reason_type policy — evidence vs business_context_required
# ---------------------------------------------------------------------------


class TestReasonTypePolicy:
    def test_evidence_kind_carries_non_empty_refs(self):
        f, rec = _rec_for(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])
        assert rec.reason_type == ReasonType.EVIDENCE.value
        assert len(rec.evidence_refs) >= 1

    def test_dominant_category_is_business_context_required(self):
        _, rec = _rec_for(profiles=[_col(
            "status", 1, categories=["Won", "Lost"], frequencies=[99, 1],
        )])
        assert rec.reason_type == ReasonType.BUSINESS_CONTEXT_REQUIRED.value

    def test_accuracy_flagged_rule_is_business_context_required(self):
        _, rec = _rec_for(checks=[_check(
            "amount_outliers", "accuracy_flagged", "WARN", "amount", severity="warning",
        )])
        assert rec.reason_type == ReasonType.BUSINESS_CONTEXT_REQUIRED.value

    def test_business_context_still_phrased_as_investigation(self):
        # a review signal never asserts a proven cause — action opens with a
        # confirm / investigate verb, and no do_not auto-remediates.
        _, rec = _rec_for(profiles=[_col(
            "status", 1, categories=["Won", "Lost"], frequencies=[99, 1],
        )])
        assert rec.action.split()[0] in {"Confirm", "Investigate", "Verify", "Check", "Trace"}
        assert all(not d.lower().startswith(("replace", "delete", "fill")) for d in rec.do_not)


# ---------------------------------------------------------------------------
# no-fabrication law — evidence refs carried verbatim, no invented numbers
# ---------------------------------------------------------------------------


class TestEvidenceGrounding:
    def test_evidence_refs_are_verbatim_subset_of_finding(self):
        f, rec = _rec_for(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])
        assert list(rec.evidence_refs) == list(f.evidence_refs)

    def test_rationale_quotes_cited_evidence(self):
        _, rec = _rec_for(profiles=[_col("region", 1, missing_rate=0.97)])
        for ref in rec.evidence_refs:
            assert ref in rec.rationale

    def test_no_invented_ref_exists_off_the_finding(self):
        f, rec = _rec_for(profiles=[_col("flag", 1, distinct=1, denom=50)])
        allowed = set(f.evidence_refs)
        assert set(rec.evidence_refs).issubset(allowed)

    def test_column_substituted_but_value_not_reproduced(self):
        # the numeric observation lives in the cited metric ref, not re-typed
        # into the action (which could drift / mis-quote) — the action only
        # carries the column name.
        _, rec = _rec_for(checks=[_check(
            "amount_missing_rate", "completeness", "FAIL", "amount", value=0.42,
        )])
        assert "amount" in rec.action
        assert "0.42" not in rec.action


# ---------------------------------------------------------------------------
# §4.3 contract validity + identity + status
# ---------------------------------------------------------------------------


class TestContractValidity:
    def test_every_recommendation_schema_valid(self):
        fs = _findings(
            checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")],
            profiles=[
                _col("region", 1, missing_rate=0.98),
                _col("status", 2, categories=["A", "B"], frequencies=[97, 3]),
            ],
            dup={"order_id": 3},
        )
        for rec in build_recommendations(fs).recommendations:
            assert S.validate("recommendation.schema.json", rec.model_dump()) == []

    def test_recommendation_id_shape_and_backref(self):
        fs = _findings(checks=[
            _check("a_missing", "completeness", "FAIL", "a"),
            _check("b_bad", "validity", "FAIL", "b"),
        ])
        recs = build_recommendations(fs).recommendations
        assert [r.recommendation_id for r in recs] == ["R-001", "R-002"]
        assert [r.finding_id for r in recs] == [f.finding_id for f in fs]

    def test_status_always_proposed(self):
        _, rec = _rec_for(checks=[_check("r", "completeness", "FAIL", "amount")])
        assert rec.status == "proposed"

    def test_every_finding_gets_at_least_one_recommendation(self):
        fs = _findings(
            checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")],
            profiles=[_col("region", 1, missing_rate=0.99)],
        )
        res = build_recommendations(fs)
        cited = {r.finding_id for r in res.recommendations}
        assert cited == {f.finding_id for f in fs}


# ---------------------------------------------------------------------------
# wiring — finding.recommendation_ids (M2-T8 left it [])
# ---------------------------------------------------------------------------


class TestWiring:
    def test_finding_recommendation_ids_populated(self):
        fs = _findings(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])
        assert fs[0].recommendation_ids == []  # M2-T8 leaves it empty
        res = build_recommendations(fs)
        assert res.findings[0].recommendation_ids == ["R-001"]

    def test_wired_finding_still_schema_valid(self):
        fs = _findings(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])
        for f in build_recommendations(fs).findings:
            assert S.validate("finding.schema.json", f.model_dump()) == []

    def test_wiring_preserves_every_other_finding_field(self):
        fs = _findings(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])
        wired = build_recommendations(fs).findings[0]
        original = fs[0].model_dump()
        after = wired.model_dump()
        original["recommendation_ids"] = after["recommendation_ids"]
        assert original == after


# ---------------------------------------------------------------------------
# determinism + do_not guardrails content
# ---------------------------------------------------------------------------


class TestDeterminismAndGuardrails:
    def _mixed(self):
        return _findings(
            checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")],
            profiles=[
                _col("region", 1, missing_rate=0.99),
                _col("status", 2, categories=["Won", "Lost"], frequencies=[96, 4]),
            ],
        )

    def test_byte_stable_rerun(self):
        a = [r.model_dump_json() for r in build_recommendations(self._mixed()).recommendations]
        b = [r.model_dump_json() for r in build_recommendations(self._mixed()).recommendations]
        assert a == b

    def test_completeness_carries_zero_fill_guardrail(self):
        _, rec = _rec_for(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])
        assert any("zero" in d.lower() for d in rec.do_not)

    def test_template_catalog_covers_all_rule_categories(self):
        from data_profiling_engine.schemas.models import QualityDimension
        for dim in QualityDimension:
            assert f"rule:{dim.value}" in TEMPLATES


# ---------------------------------------------------------------------------
# M4-T4 — bilingual narrative (ADR-018 / BLUEPRINT Q1). English is the shipped
# default; Indonesian is opt-in via ``DatasetConfig.language``. Only the
# human-readable strings change — ids / evidence / reason_type / shape do not.
# ---------------------------------------------------------------------------


class TestNarrativeLanguage:
    _COMPLETENESS = dict(checks=[_check("amount_missing_rate", "completeness", "FAIL", "amount")])

    def _findings(self):
        return _findings(**self._COMPLETENESS)

    def test_default_language_is_english(self):
        assert DEFAULT_LANGUAGE == "en"
        rec = build_recommendations(self._findings()).recommendations[0]
        assert rec.action.startswith("Trace the completeness gap")

    def test_explicit_en_equals_default_byte_identical(self):
        a = [r.model_dump_json() for r in build_recommendations(self._findings()).recommendations]
        b = [r.model_dump_json() for r in build_recommendations(self._findings(), "en").recommendations]
        assert a == b

    def test_indonesian_narrative_is_opt_in(self):
        rec = build_recommendations(self._findings(), "id").recommendations[0]
        assert "Telusuri celah kelengkapan" in rec.action
        assert "amount" in rec.action  # real column name substituted verbatim

    def test_indonesian_recommendation_still_schema_valid(self):
        rec = build_recommendations(self._findings(), "id").recommendations[0]
        assert S.validate("recommendation.schema.json", rec.model_dump()) == []

    def test_only_strings_differ_across_languages(self):
        en = build_recommendations(self._findings(), "en").recommendations[0]
        idi = build_recommendations(self._findings(), "id").recommendations[0]
        # identity + evidence + policy fields are language-independent...
        assert en.recommendation_id == idi.recommendation_id
        assert en.finding_id == idi.finding_id
        assert en.reason_type == idi.reason_type
        assert en.evidence_refs == idi.evidence_refs
        assert en.status == idi.status
        # ...only the human-readable narrative differs
        assert en.action != idi.action
        assert en.rationale != idi.rationale

    def test_business_context_policy_is_language_independent(self):
        # accuracy_flagged is a review signal (business_context_required) in BOTH
        # catalogs — never a proven defect, in either language.
        for lang in ("en", "id"):
            tpl = templates_for_language(lang)[classify_finding_kind(
                _only_finding(profiles=[_col("status", 1, categories=["Won", "Lost"], frequencies=[99, 1])])
            )]
            assert tpl.reason_type == ReasonType.BUSINESS_CONTEXT_REQUIRED

    def test_catalogs_share_identical_kind_keys(self):
        assert set(TEMPLATES_BY_LANGUAGE["en"]) == set(TEMPLATES_BY_LANGUAGE["id"])

    def test_templates_for_language_none_is_default_en(self):
        assert templates_for_language(None) is TEMPLATES_BY_LANGUAGE["en"]

    def test_unsupported_language_is_hard_error_not_silent_fallback(self):
        import pytest
        with pytest.raises(ValueError, match="unsupported recommendation language"):
            templates_for_language("de")

    def test_enum_member_accepted_as_language(self):
        from data_profiling_engine.schemas.models import ReportLanguage
        rec = build_recommendations(self._findings(), ReportLanguage.ID).recommendations[0]
        assert "Telusuri" in rec.action

    def test_config_language_flows_and_roundtrips(self):
        cfg = DatasetConfig.model_validate({
            "config_version": 3, "dataset": {"name": DS},
            "profiling": {"mode": "standard"},
            "quality_checks": [
                {"id": "r1", "rule": "not_null", "column": "amount",
                 "dimension": "completeness", "severity": "high"}
            ],
            "language": "id",
        })
        assert cfg.language == "id"
        assert S.validate("dataset_config.schema.json", cfg.model_dump()) == []
        assert cfg.model_dump()["language"] == "id"

    def test_config_language_omitted_when_unset_is_backward_compatible(self):
        # an untouched `language` is dropped from the dump (== shipped "en"), so
        # config_hash / golden of pre-existing configs stay byte-identical.
        cfg = DatasetConfig.model_validate({
            "config_version": 3, "dataset": {"name": DS},
            "profiling": {"mode": "standard"},
            "quality_checks": [
                {"id": "r1", "rule": "not_null", "column": "amount",
                 "dimension": "completeness", "severity": "high"}
            ],
        })
        assert cfg.language is None
        assert "language" not in cfg.model_dump()
