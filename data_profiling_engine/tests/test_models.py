"""M1-T4 — Pydantic models round-trip against the 9 frozen JSON Schemas.

Protocol (CONTRACTS §1: models are "generated from / validated against the
canonical JSON Schemas"; schemas/*.schema.json remain the source of truth):
for every literal example in test_schemas.py —
    example dict → TypeAdapter/Model.model_validate → model_dump(mode="json")
    → json.loads(model_dump_json) → schemas.validate(...) == []
plus negative tests proving the models enforce the same conditional rules
(§1.1 provenance couplings, §2 sampling seed lock, §4.1 FAIL≠ERROR coupling,
§4.3 evidence⇒refs, §9 rule catalog/conditionals, additionalProperties).
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from data_profiling_engine import schemas as S
from data_profiling_engine.schemas import models as M
from data_profiling_engine.tests.test_schemas import (
    COLUMN_EXAMPLE,
    CONFIG_EXAMPLE,
    FINDING_MVP,
    PROFILE_EXAMPLE,
    PROVENANCE_APPROX,
    PROVENANCE_EXACT,
    PROVENANCE_NOT_COMPUTED,
    PROVENANCE_SAMPLED,
    QCR_ERROR,
    QCR_FAIL,
    RECOMMENDATION_EVIDENCE,
    RUN_FULL,
)

# ---- round-trip: dict → model → JSON → canonical schema ------------------

ROUND_TRIP_CASES = [
    ("provenance.schema.json", M.Provenance, PROVENANCE_EXACT),
    ("provenance.schema.json", M.Provenance, PROVENANCE_APPROX),
    ("provenance.schema.json", M.Provenance, PROVENANCE_SAMPLED),
    ("provenance.schema.json", M.Provenance, PROVENANCE_NOT_COMPUTED),
    ("profiling_run.schema.json", M.ProfilingRun, RUN_FULL),
    ("column_profile.schema.json", M.ColumnProfile, COLUMN_EXAMPLE),
    ("dataset_profile.schema.json", M.DatasetProfile, PROFILE_EXAMPLE),
    ("quality_check_result.schema.json", M.QualityCheckResult, QCR_FAIL),
    ("quality_check_result.schema.json", M.QualityCheckResult, QCR_ERROR),
    ("finding.schema.json", M.Finding, FINDING_MVP),
    (
        "finding_workflow.schema.json",
        M.FindingWorkflow,
        {
            "owner": {"id": "u_1", "display": "ana"},
            "status": "investigating",
            "comments": [
                {"author": "ana", "body": "looking", "created_at": "2026-10-24T10:00:00Z"}
            ],
            "resolution_evidence": None,
            "updated_at": "2026-10-24T10:00:00Z",
        },
    ),
    ("recommendation.schema.json", M.Recommendation, RECOMMENDATION_EVIDENCE),
    ("dataset_config.schema.json", M.DatasetConfig, CONFIG_EXAMPLE),
]


@pytest.mark.parametrize(
    "schema_name,model_cls,example",
    ROUND_TRIP_CASES,
    ids=[f"{s}-{i}" for i, (s, _, _) in enumerate(ROUND_TRIP_CASES)],
)
def test_round_trip_validate_dump_revalidate(schema_name, model_cls, example):
    inst = model_cls.model_validate(example)
    dumped = inst.model_dump(mode="json")
    assert S.validate(schema_name, dumped) == [], (schema_name, dumped)
    # model_dump_json must agree with model_dump and re-parse identically
    via_json = json.loads(inst.model_dump_json())
    assert S.validate(schema_name, via_json) == [], (schema_name, via_json)
    # re-validated dump equals the same model (stable second round)
    assert model_cls.model_validate(dumped).model_dump(mode="json") == dumped


def test_round_trip_preserves_example_semantics():
    """Dump must not invent keys or drop required ones vs. the doc examples."""
    run = M.ProfilingRun.model_validate(RUN_FULL)
    assert run.model_dump(mode="json") == RUN_FULL
    prof = M.DatasetProfile.model_validate(PROFILE_EXAMPLE)
    assert prof.model_dump(mode="json") == PROFILE_EXAMPLE
    cfg = M.DatasetConfig.model_validate(CONFIG_EXAMPLE)
    assert cfg.model_dump(mode="json") == CONFIG_EXAMPLE


def test_qcr_error_exemplar_null_keys_survive():
    # §4.1 exemplars carry explicit nulls (observed/error/evidence) — these are
    # schema-nullable fields, so the dump keeps them and they still validate.
    dumped = M.QualityCheckResult.model_validate(QCR_ERROR).model_dump(mode="json")
    assert dumped["observed"] is None
    assert dumped["error"] == QCR_ERROR["error"]
    assert S.validate("quality_check_result.schema.json", dumped) == []


def test_metricvalue_round_trip_standalone_def():
    from jsonschema import Draft202012Validator

    sub = S.BY_ID["https://dpe.local/schemas/provenance.schema.json"]["$defs"]["metricValue"]
    v = Draft202012Validator(sub, registry=S.REGISTRY)
    good = {"value": 0.15, "provenance": {"method": "exact"}, "denominator": 20000}
    mv = M.MetricValue.model_validate(good)
    assert list(v.iter_errors(mv.model_dump(mode="json"))) == []
    nc = M.MetricValue.model_validate(
        {"provenance": {"method": "not_computed", "reason": "column_type_unsupported"}}
    )
    dumped = nc.model_dump(mode="json")
    assert "value" not in dumped  # absence, not null (§1 "never a silent null")
    assert list(v.iter_errors(dumped)) == []


# ---- model-side enforcement of the conditional schema rules --------------

def test_provenance_sampled_requires_seed_model_side():
    with pytest.raises(ValidationError, match="seed"):
        M.Provenance(method="sampled", method_params={"sample_size": 10})


def test_provenance_approximate_requires_algorithm_model_side():
    with pytest.raises(ValidationError, match="algorithm"):
        M.Provenance(method="approximate", method_params={"confidence": 0.9})


def test_provenance_not_computed_requires_reason_model_side():
    with pytest.raises(ValidationError, match="reason"):
        M.Provenance(method="not_computed")


def test_provenance_rejects_unknown_method():
    with pytest.raises(ValidationError):
        M.Provenance(method="guessed")


def test_metricvalue_not_computed_rejects_value():
    with pytest.raises(ValidationError, match="value"):
        M.MetricValue(
            provenance={"method": "not_computed", "reason": "x"}, value=0.15
        )


def test_run_sampling_enabled_requires_seed_strategy_size():
    with pytest.raises(ValidationError, match="seed"):
        M.RunSampling(enabled=True, strategy="reservoir", sample_size=100)
    with pytest.raises(ValidationError):
        M.ProfilingRun.model_validate(
            dict(RUN_FULL, sampling={"enabled": True, "strategy": "r", "sample_size": 1})
        )


def test_run_rejects_non_terminal_status():
    with pytest.raises(ValidationError):
        M.ProfilingRun.model_validate(dict(RUN_FULL, status="RUNNING"))


def test_run_rejects_unknown_key():
    with pytest.raises(ValidationError, match="surprise"):
        M.ProfilingRun.model_validate(dict(RUN_FULL, surprise="x"))


def test_run_rejects_bad_content_hash():
    bad = json.loads(json.dumps(RUN_FULL))
    bad["dataset"]["source_snapshot"]["content_hash"] = "not-a-hash"
    with pytest.raises(ValidationError, match="content_hash"):
        M.ProfilingRun.model_validate(bad)


def test_column_profile_semantic_and_unit_enums():
    with pytest.raises(ValidationError):
        M.ColumnProfile.model_validate(dict(COLUMN_EXAMPLE, semantic_type="amount"))
    with pytest.raises(ValidationError):
        M.ColumnProfile.model_validate(dict(COLUMN_EXAMPLE, unit={"currency": "USD", "status": "asserted"}))


def test_qcr_error_coupling_model_side():
    with pytest.raises(ValidationError, match="error"):
        M.QualityCheckResult.model_validate(dict(QCR_ERROR, error=None))
    with pytest.raises(ValidationError, match="error"):
        M.QualityCheckResult.model_validate(dict(QCR_FAIL, status="PASS", error={"code": "X", "message": "y"}))


def test_finding_fingerprint_pattern_model_side():
    for bad in ["sha256:...", "sha256:" + "A" * 64, "md5:" + "a" * 32]:
        with pytest.raises(ValidationError, match="fingerprint"):
            M.Finding.model_validate(dict(FINDING_MVP, fingerprint=bad))


def test_finding_facts_and_evidence_min_items_model_side():
    with pytest.raises(ValidationError, match="facts"):
        M.Finding.model_validate(dict(FINDING_MVP, facts=[]))
    with pytest.raises(ValidationError, match="evidence_refs"):
        M.Finding.model_validate(dict(FINDING_MVP, evidence_refs=[]))


def test_finding_workflow_object_model_side():
    good_wf = {"owner": "ana", "status": "acknowledged", "comments": [],
               "resolution_evidence": None, "updated_at": "2026-10-24T10:00:00Z"}
    f = M.Finding.model_validate(dict(FINDING_MVP, workflow=good_wf))
    dumped = f.model_dump(mode="json")
    assert S.validate("finding.schema.json", dumped) == []
    with pytest.raises(ValidationError):
        M.Finding.model_validate(dict(FINDING_MVP, workflow={"owner": "ana", "status": "closed"}))


def test_recommendation_evidence_requires_refs_model_side():
    with pytest.raises(ValidationError, match="evidence_refs"):
        M.Recommendation.model_validate(
            dict(RECOMMENDATION_EVIDENCE, reason_type="evidence", evidence_refs=[])
        )
    # business_context_required may carry none
    ok = M.Recommendation.model_validate(
        dict(RECOMMENDATION_EVIDENCE, reason_type="business_context_required", evidence_refs=[])
    )
    assert S.validate("recommendation.schema.json", ok.model_dump(mode="json")) == []


def test_config_rejects_unknown_rule_key_model_side():
    bad = json.loads(json.dumps(CONFIG_EXAMPLE))
    bad["quality_checks"][0]["expect"] = "no nulls"
    with pytest.raises(ValidationError, match="expect"):
        M.DatasetConfig.model_validate(bad)


def test_config_rejects_missing_dimension_and_unknown_rule():
    bad = json.loads(json.dumps(CONFIG_EXAMPLE))
    bad["quality_checks"][2].pop("dimension")
    with pytest.raises(ValidationError, match="dimension"):
        M.DatasetConfig.model_validate(bad)
    bad2 = json.loads(json.dumps(CONFIG_EXAMPLE))
    bad2["quality_checks"][0]["rule"] = "not_empty"
    with pytest.raises(ValidationError, match="rule"):
        M.DatasetConfig.model_validate(bad2)


def test_config_conditional_and_threshold_rules_model_side():
    bad = json.loads(json.dumps(CONFIG_EXAMPLE))
    bad["quality_checks"][4].pop("condition")
    with pytest.raises(ValidationError, match="condition"):
        M.DatasetConfig.model_validate(bad)
    bad2 = json.loads(json.dumps(CONFIG_EXAMPLE))
    bad2["quality_checks"][2].pop("value")
    with pytest.raises(ValidationError, match="value"):
        M.DatasetConfig.model_validate(bad2)


def test_config_sampling_enabled_lock_model_side():
    bad = json.loads(json.dumps(CONFIG_EXAMPLE))
    bad["profiling"]["sampling"] = {"enabled": True, "strategy": "reservoir", "size": 10}
    with pytest.raises(ValidationError, match="seed"):
        M.DatasetConfig.model_validate(bad)


# ---- parity: model-generated schema vs canonical schema (structural) -----

CANONICAL_ROOTS = {
    "provenance.schema.json": M.Provenance,
    "profiling_run.schema.json": M.ProfilingRun,
    "dataset_profile.schema.json": M.DatasetProfile,
    "column_profile.schema.json": M.ColumnProfile,
    "quality_check_result.schema.json": M.QualityCheckResult,
    "finding.schema.json": M.Finding,
    "finding_workflow.schema.json": M.FindingWorkflow,
    "recommendation.schema.json": M.Recommendation,
    "dataset_config.schema.json": M.DatasetConfig,
}


def test_top_level_field_names_match_canonical():
    """Every canonical root property set == model field set (no drift)."""
    for name, cls in CANONICAL_ROOTS.items():
        canon = set(S.RAW_SCHEMAS[name]["properties"])
        mine = set(cls.model_fields)
        assert mine == canon, (name, "only-in-model", mine - canon, "only-in-schema", canon - mine)


def test_required_sets_match_canonical():
    for name, cls in CANONICAL_ROOTS.items():
        canon = set(S.RAW_SCHEMAS[name].get("required", []))
        mine = {f for f, spec in cls.model_fields.items() if spec.is_required()}
        assert mine == canon, (name, mine ^ canon)


@pytest.mark.parametrize(
    "schema_name,property_path,enum_values",
    [
        ("provenance.schema.json", "method", ["exact", "approximate", "sampled", "not_computed"]),
        ("profiling_run.schema.json", "status", ["SUCCEEDED", "FAILED", "CANCELLED"]),
        ("column_profile.schema.json", "semantic_type",
         ["identifier", "dimension", "measure", "time", "status", "text", "other"]),
        ("quality_check_result.schema.json", "status", ["PASS", "FAIL", "WARN", "ERROR"]),
        ("quality_check_result.schema.json", "dimension",
         ["completeness", "uniqueness", "validity", "consistency", "timeliness", "accuracy_flagged"]),
        ("quality_check_result.schema.json", "severity",
         ["critical", "high", "medium", "low", "warning", "info"]),
        ("recommendation.schema.json", "reason_type",
         ["evidence", "heuristic", "business_context_required"]),
        ("recommendation.schema.json", "status",
         ["proposed", "accepted", "rejected", "deferred", "implemented"]),
        ("finding_workflow.schema.json", "status",
         ["open", "acknowledged", "investigating", "resolved", "accepted_risk"]),
    ],
)
def test_enum_values_parity(schema_name, property_path, enum_values):
    canon = S.RAW_SCHEMAS[schema_name]["properties"][property_path]["enum"]
    assert canon == enum_values, schema_name
    # model must reject any value outside the canonical enum
    cls = CANONICAL_ROOTS[schema_name]
    with pytest.raises(ValidationError):
        cls(**{property_path: "__not_a_canonical_value__"})


def test_strict_models_forbid_extras_open_models_allow():
    # strict (additionalProperties:false) roots reject unknown keys
    with pytest.raises(ValidationError):
        M.QualityCheckResult.model_validate(dict(QCR_FAIL, extra_key=1))
    with pytest.raises(ValidationError):
        M.Finding.model_validate(dict(FINDING_MVP, extra_key=1))
    with pytest.raises(ValidationError):
        M.DatasetConfig.model_validate(dict(CONFIG_EXAMPLE, extra_section={"x": 1}))
    # open (additionalProperties:true) blocks pass unknown keys through untouched
    snap = M.SourceSnapshot.model_validate(
        dict(RUN_FULL["dataset"]["source_snapshot"], custom_hint="abc")
    )
    assert snap.model_dump(mode="json")["custom_hint"] == "abc"
    meta = M.DatasetProfile.model_validate(dict(PROFILE_EXAMPLE, source_metadata={"x": None}))
    assert meta.model_dump(mode="json")["source_metadata"] == {"x": None}


def test_null_vs_absence_policy():
    """Non-nullable optional fields dump as absent; nullable ones as null."""
    p = M.Provenance.model_validate({"method": "exact"})
    dumped = p.model_dump(mode="json")
    assert "reason" not in dumped and "computed_at" not in dumped
    assert S.validate("provenance.schema.json", dumped) == []
    # explicit null on a non-nullable-by-schema field is still rejected at parse
    with pytest.raises(ValidationError):
        M.Provenance.model_validate({"method": "exact", "reason": None})
    # …but in an OPEN model an undeclared extra key may be null (schema types
    # additionalProperties as unrestricted).
    open_ok = M.SourceSnapshot.model_validate(
        dict(RUN_FULL["dataset"]["source_snapshot"], hint=None)
    )
    assert open_ok.model_dump(mode="json")["hint"] is None


def test_nested_blocks_enforce_their_own_strictness():
    # nested strict block rejects unknown keys / bad enums independently
    with pytest.raises(ValidationError):
        M.Missingness.model_validate(
            dict(COLUMN_EXAMPLE["missingness"], nullish=1)
        )
    with pytest.raises(ValidationError):
        M.ColumnUnit.model_validate({"currency": "USD", "status": "asserted"})
    # nested open block keeps unknown keys (schema additionalProperties: true)
    cat = M.Categorical.model_validate({"categories": ["a"], "custom": {"k": 1}})
    assert cat.model_dump(mode="json")["custom"] == {"k": 1}
