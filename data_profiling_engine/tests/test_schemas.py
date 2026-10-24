"""M1-T1 — canonical JSON Schemas: presence, cross-refs, contract rules.

Covers CONTRACTS §1 (conventions + Provenance), §2 (ProfilingRun),
§3.1 (DatasetProfile), §3.2 (ColumnProfile). Positive cases mirror the
literal examples in the doc; negative cases enforce the binding rules
ADR-015/016 rely on (sampled ⇒ seed, approximate ⇒ algorithm,
not_computed ⇒ reason & no value, terminal-status enum, etc.). M1-T3 adds
CONTRACTS §9 dataset-config rejection tests (unknown rule keys, missing
`dimension`, closed rule catalog).
"""
from __future__ import annotations

import copy

import pytest
from jsonschema import Draft202012Validator

from data_profiling_engine import schemas as S


# ---- schema-pack presence + $id hygiene ---------------------------------

def test_required_schema_files_exist() -> None:
    for name in S.REQUIRED_SCHEMAS:
        assert name in S.RAW_SCHEMAS, f"missing schema file: {name}"


def test_every_schema_has_expected_dpe_id() -> None:
    for name, body in S.RAW_SCHEMAS.items():
        sid = body.get("$id", "")
        assert sid.startswith("https://dpe.local/schemas/"), (name, sid)
        assert sid.endswith(name), (name, sid)


def test_all_schemas_compile() -> None:
    """Draft202012Validator.check_schema walks $refs; catches dangling pointers."""
    for name, body in S.RAW_SCHEMAS.items():
        try:
            Draft202012Validator.check_schema(body)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{name} is not a valid Draft 2020-12 schema: {exc}")


# ---- Provenance (§1.1) ---------------------------------------------------

PROVENANCE_EXACT = {"method": "exact", "method_params": {}}
PROVENANCE_APPROX = {
    "method": "approximate",
    "method_params": {"algorithm": "duckdb_approx_quantile", "confidence": 0.95},
    "computed_at": "2026-10-24T09:12:03Z",
    "engine_version": "0.1.0",
}
PROVENANCE_SAMPLED = {
    "method": "sampled",
    "method_params": {
        "sampling_strategy": "reservoir",
        "sample_size": 100000,
        "population_size": 52000000,
        "seed": 20261024,
        "algorithm": "duckdb-approx_quantile",
        "confidence": 0.95,
    },
    "computed_at": "2026-10-24T09:12:03Z",
    "engine_version": "0.1.0",
}
PROVENANCE_NOT_COMPUTED = {
    "method": "not_computed",
    "method_params": {},
    "reason": "column_type_unsupported",
}


@pytest.mark.parametrize("inst", [PROVENANCE_EXACT, PROVENANCE_APPROX, PROVENANCE_SAMPLED, PROVENANCE_NOT_COMPUTED])
def test_provenance_positive(inst):
    assert S.validate("provenance.schema.json", inst) == []


def test_provenance_sampled_requires_seed():
    bad = {"method": "sampled", "method_params": {"sample_size": 10}}
    errs = S.validate("provenance.schema.json", bad)
    assert any("seed" in e for e in errs), errs


def test_provenance_approximate_requires_algorithm():
    bad = {"method": "approximate", "method_params": {"confidence": 0.9}}
    errs = S.validate("provenance.schema.json", bad)
    assert any("algorithm" in e for e in errs), errs


def test_provenance_not_computed_requires_reason():
    bad = {"method": "not_computed", "method_params": {}}
    errs = S.validate("provenance.schema.json", bad)
    assert any("reason" in e for e in errs), errs


def test_provenance_method_enum_closed():
    errs = S.validate("provenance.schema.json", {"method": "guessed"})
    assert errs, errs


# ---- MetricValue $def ----------------------------------------------------

def test_metricvalue_not_computed_forbids_value():
    bad = {"provenance": dict(PROVENANCE_NOT_COMPUTED), "value": 0.15}
    sub = S.BY_ID["https://dpe.local/schemas/provenance.schema.json"]["$defs"]["metricValue"]
    v = Draft202012Validator(sub, registry=S.REGISTRY)
    errs = list(v.iter_errors(bad))
    assert errs, "MetricValue must reject a `value` when provenance.method=not_computed"


def test_metricvalue_exact_positive():
    sub = S.BY_ID["https://dpe.local/schemas/provenance.schema.json"]["$defs"]["metricValue"]
    v = Draft202012Validator(sub, registry=S.REGISTRY)
    assert list(v.iter_errors({"value": 0.15, "provenance": {"method": "exact"}, "denominator": 20000})) == []


# ---- ProfilingRun (§2) ---------------------------------------------------

RUN_FULL = {
    "schema_version": "1.0",
    "run_id": "01J8Z4K2M7AAA1B2C3D4E5F6G7",
    "job_id": "01J8Z4K2M7JOB1B2C3D4E5F6G7H8",
    "dataset": {
        "dataset_id": "ds_sales_deals",
        "source_kind": "file",
        "connector": "csv",
        "source_ref": "sales_deals.csv",
        "source_snapshot": {
            "path": "/data/sales_deals.csv",
            "size_bytes": 10485760,
            "modified_at": "2026-10-01T00:00:00Z",
            "content_hash": "sha256:abcdef0123456789",
        },
    },
    "config": {"config_version": "3", "config_hash": "sha256:1234abcd", "profile_mode": "standard"},
    "engine": {"engine_version": "0.1.0", "duckdb_version": "1.1.0", "python_version": "3.12.7"},
    "timing": {
        "started_at": "2026-10-24T09:00:00Z",
        "finished_at": "2026-10-24T09:03:02Z",
        "duration_ms": 182000,
    },
    "sampling": {"enabled": True, "strategy": "reservoir", "sample_size": 100000, "seed": 20261024},
    "status": "SUCCEEDED",
    "result_refs": {
        "profile_json": "artifacts/DP-0001/profile.json",
        "html_report": "artifacts/DP-0001/report.html",
        "issue_csv": "artifacts/DP-0001/issues.csv",
    },
    "error_log": [
        {"code": "RULE_EXEC_FAILED", "task": "deal_value_outlier_scan", "message": "type mismatch"}
    ],
    "warnings": [],
}


def test_profiling_run_positive_full():
    assert S.validate("profiling_run.schema.json", RUN_FULL) == []


def test_profiling_run_requires_schema_version():
    bad = dict(RUN_FULL)
    bad.pop("schema_version")
    errs = S.validate("profiling_run.schema.json", bad)
    assert any("schema_version" in e for e in errs), errs


def test_profiling_run_status_terminal_only():
    bad = dict(RUN_FULL, status="RUNNING")
    errs = S.validate("profiling_run.schema.json", bad)
    assert any("status" in e for e in errs), errs


def test_profiling_run_sampling_enabled_requires_seed():
    bad = dict(RUN_FULL, sampling={"enabled": True, "strategy": "reservoir", "sample_size": 100})
    errs = S.validate("profiling_run.schema.json", bad)
    assert any("seed" in e for e in errs), errs


def test_profiling_run_rejects_unknown_top_level_key():
    bad = dict(RUN_FULL, surprise="x")
    errs = S.validate("profiling_run.schema.json", bad)
    assert errs, "additionalProperties=false must reject unknown top-level keys"


def test_profiling_run_content_hash_pattern():
    bad = copy.deepcopy(RUN_FULL)
    bad["dataset"]["source_snapshot"]["content_hash"] = "not-a-hash"
    errs = S.validate("profiling_run.schema.json", bad)
    assert any("content_hash" in e for e in errs), errs


# ---- DatasetProfile (§3.1) + ColumnProfile (§3.2) ------------------------

COLUMN_EXAMPLE = {
    "schema_version": "1.0",
    "column": "deal_value",
    "ordinal": 5,
    "physical_type": "VARCHAR",
    "logical_type": "numeric_currency",
    "semantic_type": "measure",
    "unit": {"currency": "USD", "status": "assumed"},
    "missingness": {
        "null_count": 3000,
        "blank_count": 0,
        "sentinel_count": 0,
        "missing_rate": {"value": 0.15, "denominator": 20000,
                         "provenance": {"method": "exact"}},
    },
    "uniqueness": {
        "distinct_count": {"value": 16820,
                           "provenance": {"method": "approximate",
                                          "method_params": {"algorithm": "duckdb_count_distinct_hll"}}},
        "is_unique_candidate": False,
    },
    "stats": {
        "valid_count": 17000,
        "min": {"value": 0, "provenance": {"method": "exact"}},
        "max": {"value": 980000, "provenance": {"method": "exact"}},
        "mean": {"value": 18450, "provenance": {"method": "exact"}},
        "median": {"value": 6800, "provenance": {"method": "approximate",
                    "method_params": {"algorithm": "duckdb_quantile_cont"}}},
        "p95": {"value": 82000, "provenance": {"method": "approximate",
                "method_params": {"algorithm": "duckdb_approx_quantile", "confidence": 0.95}}},
        "stddev": {"value": 41200, "provenance": {"method": "exact"}},
    },
    "distribution": {
        "histogram": {"bins": [0, 1000, 5000], "counts": [10, 200, 3000],
                      "provenance": {"method": "exact"}},
        "skewness": {"value": 4.2, "provenance": {"method": "exact"}},
        "outlier_candidates": {"count": 47, "method": "iqr_1_5",
                                "provenance": {"method": "exact"},
                                "sample_row_refs": ["row:0001"]},
    },
    "categorical": None,
    "temporal": None,
    "visualization_refs": ["chart:deal_value_hist"],
    "related_metric_refs": ["metric:deal_value_missing_rate"],
}

PROFILE_EXAMPLE = {
    "schema_version": "1.0",
    "run_id": "01J8Z4K2M7AAA1B2C3D4E5F6G7",
    "row_count": {"value": 20000, "provenance": {"method": "exact"}},
    "column_count": 8,
    "grain": "one_row_per_deal",
    "primary_key_candidates": ["deal_id"],
    "temporal_coverage": {
        "column": "created_at",
        "min": "2024-01-01T00:00:00Z",
        "max": "2026-09-30T00:00:00Z",
        "provenance": {"method": "exact"},
    },
    "source_metadata": {"connector": "csv", "delimiter": ",", "encoding": "utf-8"},
    "columns": [COLUMN_EXAMPLE],
}


def test_column_profile_positive():
    assert S.validate("column_profile.schema.json", COLUMN_EXAMPLE) == []


def test_column_profile_missing_ordinal_rejected():
    bad = dict(COLUMN_EXAMPLE)
    bad.pop("ordinal")
    errs = S.validate("column_profile.schema.json", bad)
    assert any("ordinal" in e for e in errs), errs


def test_column_profile_unit_status_enum():
    bad = dict(COLUMN_EXAMPLE, unit={"currency": "USD", "status": "asserted"})
    errs = S.validate("column_profile.schema.json", bad)
    assert any("status" in e for e in errs), errs


def test_column_profile_semantic_type_enum():
    bad = dict(COLUMN_EXAMPLE, semantic_type="amount")
    errs = S.validate("column_profile.schema.json", bad)
    assert errs, "semantic_type must be a closed enum"


def test_dataset_profile_positive():
    assert S.validate("dataset_profile.schema.json", PROFILE_EXAMPLE) == []


def test_dataset_profile_requires_columns_array():
    bad = dict(PROFILE_EXAMPLE)
    bad.pop("columns")
    errs = S.validate("dataset_profile.schema.json", bad)
    assert any("columns" in e for e in errs), errs


def test_dataset_profile_embeds_column_schema():
    bad = dict(PROFILE_EXAMPLE)
    bad["columns"] = [{"column": "x"}]  # missing required keys
    errs = S.validate("dataset_profile.schema.json", bad)
    assert errs, "columns items must validate against ColumnProfile"


# ---- Provenance propagates through nested MetricValue --------------------

def test_column_profile_sampled_missing_rate_requires_seed():
    bad = copy.deepcopy(PROFILE_EXAMPLE)
    bad["columns"][0]["missingness"]["missing_rate"]["provenance"] = {
        "method": "sampled",
        "method_params": {"sample_size": 100},  # no seed
    }
    errs = S.validate("dataset_profile.schema.json", bad)
    assert any("seed" in e for e in errs), errs


# ---- M1-T2: QualityCheckResult (§4.1) -----------------------------------

QCR_FAIL = {
    "schema_version": "1.0",
    "run_id": "01J8Z4K2M7AAA1B2C3D4E5F6G7",
    "rule_id": "deal_value_missing_rate",
    "rule_version": "1",
    "dimension": "completeness",
    "status": "FAIL",
    "severity": "high",
    "column": "deal_value",
    "observed": {"value": 0.15, "provenance": {"method": "exact"}},
    "threshold": {"operator": "<=", "value": 0.05},
    "failing_record_count": 3000,
    "evidence": {"denominator": 20000, "sample_row_refs": ["row:0001"], "baseline_ref": None},
    "error": None,
    "executed_at": "2026-10-24T09:12:03Z",
}

QCR_ERROR = {
    "schema_version": "1.0",
    "run_id": "01J8Z4K2M7AAA1B2C3D4E5F6G7",
    "rule_id": "revenue_non_negative",
    "rule_version": "1",
    "dimension": "validity",
    "status": "ERROR",
    "severity": "warning",
    "column": "deal_value",
    "observed": None,
    "threshold": {"operator": ">=", "value": 0},
    "failing_record_count": None,
    "evidence": None,
    "error": {"code": "RULE_EXEC_FAILED", "message": "type mismatch on deal_value"},
    "executed_at": "2026-10-24T09:12:04Z",
}


def test_qcr_fail_positive():
    assert S.validate("quality_check_result.schema.json", QCR_FAIL) == []


def test_qcr_error_positive():
    assert S.validate("quality_check_result.schema.json", QCR_ERROR) == []


def test_qcr_error_status_requires_error_object():
    bad = dict(QCR_ERROR, error=None)
    errs = S.validate("quality_check_result.schema.json", bad)
    assert any("error" in e for e in errs), errs


def test_qcr_pass_forbids_error_object():
    bad = dict(QCR_FAIL, status="PASS", error={"code": "X", "message": "y"})
    errs = S.validate("quality_check_result.schema.json", bad)
    assert errs, "status=PASS must not carry a populated error"


def test_qcr_status_enum():
    errs = S.validate("quality_check_result.schema.json", dict(QCR_FAIL, status="SKIPPED"))
    assert any("status" in e for e in errs), errs


def test_qcr_dimension_enum():
    errs = S.validate("quality_check_result.schema.json", dict(QCR_FAIL, dimension="freshness"))
    assert any("dimension" in e for e in errs), errs


def test_qcr_dimension_accuracy_flagged_allowed():
    # §4.1: accuracy is deliberately exposed only as accuracy_flagged, never auto-scored.
    ok = dict(QCR_FAIL, dimension="accuracy_flagged")
    assert S.validate("quality_check_result.schema.json", ok) == []


def test_qcr_observed_inherits_metricvalue_not_computed_rule():
    # observed is a MetricValue: not_computed must drop the `value` sibling.
    bad = dict(QCR_FAIL, observed={
        "value": 0.15,
        "provenance": {"method": "not_computed", "reason": "column_type_unsupported"},
    })
    errs = S.validate("quality_check_result.schema.json", bad)
    assert errs, "observed with method=not_computed must omit `value`"


# ---- M1-T2: Finding + fingerprint (§4.2 / §4.4) --------------------------

FINGERPRINT_OK = "sha256:" + "a" * 64

FINDING_MVP = {
    "schema_version": "1.0",
    "finding_id": "F-001",
    "fingerprint": FINGERPRINT_OK,
    "run_id": "01J8Z4K2M7AAA1B2C3D4E5F6G7",
    "title": "Deal value incomplete",
    "summary": "15% of deal_value is empty, above the 5% configured threshold.",
    "category": "completeness",
    "severity": "high",
    "confidence": "high",
    "scope": {"column": "deal_value", "affected_records": 3000, "period": None},
    "facts": [
        {"metric_ref": "metric:deal_value_missing_rate", "value": 0.15,
         "provenance": {"method": "exact"}}
    ],
    "interpretation": "Revenue and segment comparisons may be biased if analyzed as-is.",
    "hypotheses": [
        {"text": "Missingness may cluster by status/source/period",
         "status": "unverified", "suggested_probe": "group missing rate by status"}
    ],
    "evidence_refs": ["metric:deal_value_missing_rate", "rule:deal_value_missing_rate"],
    "related_finding_ids": [],
    "recommendation_ids": ["R-001"],
    "workflow": None,
}


def test_finding_positive_mvp():
    assert S.validate("finding.schema.json", FINDING_MVP) == []


def test_finding_fingerprint_sha256_hex_pattern():
    for bad_fp in ["sha256:...", "sha256:ABC", "md5:" + "a" * 32, "a" * 64]:
        errs = S.validate("finding.schema.json", dict(FINDING_MVP, fingerprint=bad_fp))
        assert any("fingerprint" in e for e in errs), (bad_fp, errs)


def test_finding_fingerprint_requires_lowercase_hex():
    errs = S.validate("finding.schema.json",
                       dict(FINDING_MVP, fingerprint="sha256:" + "A" * 64))
    assert errs, "uppercase hex must be rejected to keep fingerprint canonical"


def test_finding_facts_cannot_be_empty():
    errs = S.validate("finding.schema.json", dict(FINDING_MVP, facts=[]))
    assert any("facts" in e for e in errs), errs


def test_finding_fact_requires_provenance():
    bad = dict(FINDING_MVP, facts=[{"metric_ref": "metric:x", "value": 1}])
    errs = S.validate("finding.schema.json", bad)
    assert any("provenance" in e for e in errs), errs


def test_finding_evidence_refs_cannot_be_empty():
    errs = S.validate("finding.schema.json", dict(FINDING_MVP, evidence_refs=[]))
    assert any("evidence_refs" in e for e in errs), errs


def test_finding_hypothesis_status_enum():
    bad = dict(FINDING_MVP, hypotheses=[{"text": "x", "status": "proven"}])
    errs = S.validate("finding.schema.json", bad)
    assert any("status" in e for e in errs), errs


def test_finding_category_matches_dimension_enum():
    errs = S.validate("finding.schema.json", dict(FINDING_MVP, category="weird"))
    assert any("category" in e for e in errs), errs


def test_finding_workflow_null_ok_and_object_validates():
    # MVP: workflow = null
    assert S.validate("finding.schema.json", dict(FINDING_MVP, workflow=None)) == []
    # Final App: populated workflow must conform to §4.5.
    good_wf = {"owner": "ana", "status": "acknowledged", "comments": [],
               "resolution_evidence": None, "updated_at": "2026-10-24T10:00:00Z"}
    assert S.validate("finding.schema.json", dict(FINDING_MVP, workflow=good_wf)) == []
    bad_wf = {"owner": "ana", "status": "closed"}
    errs = S.validate("finding.schema.json", dict(FINDING_MVP, workflow=bad_wf))
    assert any("status" in e for e in errs), errs


# ---- M1-T2: Recommendation (§4.3) ---------------------------------------

RECOMMENDATION_EVIDENCE = {
    "schema_version": "1.0",
    "recommendation_id": "R-001",
    "finding_id": "F-001",
    "action": "Investigate deal_value completeness from the CRM source and sync mechanism.",
    "rationale": "Missing rate exceeds configured threshold and can bias revenue analysis.",
    "reason_type": "evidence",
    "target_improvement": "Reduce unexplained missingness below threshold or document cause.",
    "do_not": ["Do not fill empty values with zero as a first response."],
    "evidence_refs": ["metric:deal_value_missing_rate"],
    "status": "proposed",
}


def test_recommendation_positive():
    assert S.validate("recommendation.schema.json", RECOMMENDATION_EVIDENCE) == []


def test_recommendation_evidence_reason_requires_refs():
    bad = dict(RECOMMENDATION_EVIDENCE, reason_type="evidence", evidence_refs=[])
    errs = S.validate("recommendation.schema.json", bad)
    assert any("evidence_refs" in e for e in errs), errs


def test_recommendation_business_context_allows_no_refs():
    ok = dict(RECOMMENDATION_EVIDENCE, reason_type="business_context_required",
              evidence_refs=[])
    assert S.validate("recommendation.schema.json", ok) == []


def test_recommendation_reason_type_enum():
    errs = S.validate("recommendation.schema.json",
                       dict(RECOMMENDATION_EVIDENCE, reason_type="guess"))
    assert any("reason_type" in e for e in errs), errs


def test_recommendation_status_enum():
    errs = S.validate("recommendation.schema.json",
                       dict(RECOMMENDATION_EVIDENCE, status="shipped"))
    assert any("status" in e for e in errs), errs


# ---- M1-T2: FindingWorkflow (§4.5) --------------------------------------

def test_workflow_positive():
    good = {"owner": None, "status": "open", "comments": [],
            "resolution_evidence": None, "updated_at": None}
    assert S.validate("finding_workflow.schema.json", good) == []


def test_workflow_status_enum():
    errs = S.validate("finding_workflow.schema.json", {"status": "closed"})
    assert any("status" in e for e in errs), errs


def test_workflow_requires_status():
    errs = S.validate("finding_workflow.schema.json", {"owner": "ana"})
    assert any("status" in e for e in errs), errs


def test_workflow_comment_shape():
    good = {"status": "investigating", "comments": [
        {"author": "ana", "body": "looking", "created_at": "2026-10-24T10:00:00Z"}
    ]}
    assert S.validate("finding_workflow.schema.json", good) == []
    bad = {"status": "investigating", "comments": [{"author": "ana"}]}
    errs = S.validate("finding_workflow.schema.json", bad)
    assert errs, "comments must carry author/body/created_at"


# ---- M1-T2: §4 registry hygiene -----------------------------------------

def test_m1_t2_schema_ids_align_with_filenames():
    """Every new M1-T2 file must appear in REQUIRED_SCHEMAS and carry the right $id."""
    for name in (
        "quality_check_result.schema.json",
        "finding.schema.json",
        "finding_workflow.schema.json",
        "recommendation.schema.json",
    ):
        assert name in S.REQUIRED_SCHEMAS, name
        assert name in S.RAW_SCHEMAS, name
        assert S.RAW_SCHEMAS[name]["$id"].endswith(name)


# ---- M1-T3: Dataset config input contract (CONTRACTS §9) -----------------

# Literal transcription of the §9 YAML example (parsed to JSON before validation).
CONFIG_EXAMPLE = {
    "config_version": 3,
    "dataset": {
        "name": "sales_deals",
        "grain": "one_row_per_deal",
        "primary_key": ["deal_id"],
        "units": {"deal_value": {"currency": "USD", "status": "assumed"}},
    },
    "profiling": {
        "mode": "standard",
        "sampling": {"enabled": False, "strategy": "reservoir", "size": 100000, "seed": 20261024},
        "sections": ["schema", "descriptive_statistics", "missing_values", "uniqueness", "distributions"],
    },
    "quality_checks": [
        {"id": "deal_id_not_null", "rule": "not_null", "column": "deal_id",
         "dimension": "completeness", "severity": "critical"},
        {"id": "deal_id_unique", "rule": "unique", "column": "deal_id",
         "dimension": "uniqueness", "severity": "critical"},
        {"id": "deal_value_missing_rate", "rule": "missing_rate_max", "column": "deal_value",
         "value": 0.05, "dimension": "completeness", "severity": "high"},
        {"id": "revenue_non_negative", "rule": "greater_than_or_equal", "column": "deal_value",
         "value": 0, "dimension": "validity", "severity": "warning",
         "business_note": "negative may be valid for refunds; flagged for review, not auto-fail"},
        {"id": "closed_won_has_closed_at", "rule": "conditional_not_null", "column": "closed_at",
         "condition": {"column": "status", "operator": "equals", "value": "Closed Won"},
         "dimension": "completeness", "severity": "high"},
    ],
}


def test_config_positive_literal_example():
    """The verbatim §9 exemplar must validate — including per-rule conditionals."""
    assert S.validate("dataset_config.schema.json", CONFIG_EXAMPLE) == []


def test_config_rejects_unknown_top_level_key():
    bad = dict(CONFIG_EXAMPLE, extra_section={"x": 1})
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("<root>" in e or "extra_section" in e for e in errs), errs


def test_config_rejects_unknown_rule_key():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["quality_checks"][0]["expect"] = "no nulls"  # GX-ish but not in §9
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("quality_checks/0" in e for e in errs), errs


def test_config_rejects_missing_dimension():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["quality_checks"][2].pop("dimension")
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("dimension" in e for e in errs), errs


def test_config_rejects_unknown_rule_type():
    # ENGINE §5.1 catalog is closed — no rogue rule names pre-execution.
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["quality_checks"][0]["rule"] = "not_empty"
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("rule" in e for e in errs), errs


@pytest.mark.parametrize("rule_type", ["referential_integrity", "date_order", "outlier_scan",
                                        "valid_domain", "type_valid", "range",
                                        "less_than_or_equal", "unique"])
def test_config_accepts_full_rule_catalog(rule_type):
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    rule = bad["quality_checks"][1]  # swap the `unique` exemplar
    rule["rule"] = rule_type
    if rule_type in ("range", "valid_domain", "less_than_or_equal", "outlier_scan"):
        rule["value"] = [0, 100]
    assert S.validate("dataset_config.schema.json", bad) == []


def test_config_rejects_dimension_enum_mismatch():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["quality_checks"][0]["dimension"] = "freshness"
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("dimension" in e for e in errs), errs


def test_config_rejects_invalid_severity():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["quality_checks"][0]["severity"] = "blocker"
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("severity" in e for e in errs), errs


def test_config_rejects_mode_enum():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["profiling"]["mode"] = "turbo"
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("mode" in e for e in errs), errs


def test_config_sampling_enabled_requires_seed_strategy_size():
    for dropped in ("seed", "strategy", "size"):
        bad = copy.deepcopy(CONFIG_EXAMPLE)
        bad["profiling"]["sampling"]["enabled"] = True
        bad["profiling"]["sampling"].pop(dropped)
        errs = S.validate("dataset_config.schema.json", bad)
        assert any(dropped in e for e in errs), (dropped, errs)


def test_config_sampling_disabled_allows_missing_seed():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["profiling"]["sampling"] = {"enabled": False}
    assert S.validate("dataset_config.schema.json", bad) == []


def test_config_rejects_bad_unit_status():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["dataset"]["units"]["deal_value"]["status"] = "asserted"
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("status" in e for e in errs), errs


def test_config_rejects_empty_quality_checks():
    errs = S.validate("dataset_config.schema.json", dict(CONFIG_EXAMPLE, quality_checks=[]))
    assert any("quality_checks" in e for e in errs), errs


def test_config_conditional_not_null_requires_condition():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["quality_checks"][4].pop("condition")
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("condition" in e for e in errs), errs


def test_config_threshold_rules_require_value():
    bad = copy.deepcopy(CONFIG_EXAMPLE)
    bad["quality_checks"][2].pop("value")
    errs = S.validate("dataset_config.schema.json", bad)
    assert any("value" in e or "threshold" in e for e in errs), errs


def test_config_rule_def_standalone_validation():
    """#/$defs/rule is extractable per schema-pack convention (no `#` refs to extractable defs)."""
    sub = S.BY_ID["https://dpe.local/schemas/dataset_config.schema.json"]["$defs"]["rule"]
    v = Draft202012Validator(sub, registry=S.REGISTRY)
    assert list(v.iter_errors(CONFIG_EXAMPLE["quality_checks"][0])) == []
    assert list(v.iter_errors({"id": "x", "rule": "not_null", "column": "c", "severity": "low"})), \
        "dimension is required on every rule"


def test_m1_t3_schema_id_hygiene():
    name = "dataset_config.schema.json"
    assert name in S.REQUIRED_SCHEMAS
    assert name in S.RAW_SCHEMAS
    assert S.RAW_SCHEMAS[name]["$id"].endswith(name)
