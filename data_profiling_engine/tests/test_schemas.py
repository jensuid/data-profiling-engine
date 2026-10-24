"""M1-T1 — canonical JSON Schemas: presence, cross-refs, contract rules.

Covers CONTRACTS §1 (conventions + Provenance), §2 (ProfilingRun),
§3.1 (DatasetProfile), §3.2 (ColumnProfile). Positive cases mirror the
literal examples in the doc; negative cases enforce the binding rules
ADR-015/016 rely on (sampled ⇒ seed, approximate ⇒ algorithm,
not_computed ⇒ reason & no value, terminal-status enum, etc.).
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

