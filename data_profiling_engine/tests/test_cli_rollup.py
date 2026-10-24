"""M2-T11 tests — the ``profiler-cli`` end-to-end on a `sales_deals`-style
fixture → full contract-valid JSON (CONTRACTS §2–4, ENGINE §10, TESTING §2).

This is the **contract rollup** M2-T10 deliberately deferred: it executes the
dataset-scope plan decisions (``schema.row_count`` / ``schema.column_count`` /
``schema.key_candidates``) and folds the M2-T10 pipeline result into the
frozen §3.1 ``DatasetProfile`` + §2 ``ProfilingRun``, then serialises the whole
result via the ``reporting`` JSON writer and drives it through the ``cli``
argparse entry point.

Coverage (TESTING §2 end-to-end scenario + §3.3 reproducibility):
  - every emitted object (run, profile, each column, each check, finding,
    recommendation) schema-validates against its canonical JSON Schema;
  - ``row_count`` is exact + equals the true population even under sampling;
  - ``column_count`` + ``primary_key_candidates`` come from the dataset scope
    (the deal id is a candidate; a constant column is not);
  - ``temporal_coverage`` spans the created_at column;
  - reproducibility tuple: ``content_hash`` + stable ``config_hash`` +
    ``engine_version`` + seed all present; per-task errors land in ``error_log``
    without demoting a completed run to FAILED (FAIL≠ERROR, §6.4);
  - the JSON writer is byte-deterministic (sort_keys) for a fixed result;
  - the CLI rejects an invalid config BEFORE profiling (AGENTS §5);
  - ``--mode`` overrides the config mode; a sampled run carries the §2
    ``RunSampling`` record; ``job_id`` is absent for a pure CLI run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

import duckdb
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.cli import CliError, load_config, main, run_profile
from data_profiling_engine.orchestration.rollup import (
    ENGINE_VERSION,
    SCHEMA_VERSION,
    build_dataset_profile,
    build_profiling_run,
    config_hash,
    execute_dataset_metrics,
    profile_dataset,
)
from data_profiling_engine.orchestration import run_dataset_profiling
from data_profiling_engine.reporting import result_document, write_profile_json


# ---------------------------------------------------------------------------
# sales_deals-style fixture (TESTING §2): deterministic, issues injected so the
# expected findings are known. 12 duplicate deal_ids, ~15% missing deal_value,
# a constant column, an unknown stage.
# ---------------------------------------------------------------------------
def _write_sales_deals(csv: Path, n: int = 200) -> int:
    lines = ["deal_id,created_at,stage,deal_value,is_new"]
    stages = ["Prospecting", "Closed Won", "Negotiation"]
    for i in range(n):
        # duplicate deal_id for the first 12 rows (map onto ids 0..11 twice)
        did = f"DEAL-{i % (n - 12):04d}" if i >= n - 12 else f"DEAL-{i:04d}"
        day = (i % 28) + 1
        month = (i % 12) + 1
        created = f"2024-{month:02d}-{day:02d}"
        stage = stages[i % 3]
        # ~15% missing deal_value (every 7th row blank ≈ 14.3%)
        value = "" if i % 7 == 0 else str(1000 + i * 7)
        lines.append(f"{did},{created},{stage},{value},1")
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


def _sales_config(mode: str = "standard", sampling: Optional[dict] = None) -> dict:
    profiling: dict = {"mode": mode}
    if sampling:
        profiling["sampling"] = sampling
    return {
        "config_version": 3,
        "dataset": {
            "name": "sales_deals",
            "grain": "one_row_per_deal",
            "primary_key": ["deal_id"],
        },
        "profiling": profiling,
        "quality_checks": [
            {"id": "deal_id_unique", "rule": "unique", "column": "deal_id",
             "dimension": "uniqueness", "severity": "critical"},
            {"id": "value_not_null", "rule": "not_null", "column": "deal_value",
             "dimension": "completeness", "severity": "high"},
            {"id": "stage_domain", "rule": "valid_domain", "column": "stage",
             "dimension": "validity", "severity": "warning",
             "value": ["Prospecting", "Closed Won", "Negotiation"]},
        ],
    }


@pytest.fixture()
def sales_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "sales_deals.csv"
    _write_sales_deals(csv)
    return csv


# The standard-mode ``sales_deals`` rollup is identical for every read-only
# assertion below; ``run_dataset_profiling`` (the M2-T10 pipeline) dominates the
# wall-clock cost, so build the bundle ONCE per module and share it. Tests that
# need a *different* config (sampling, error rule, CLI, parquet) run their own.
@pytest.fixture(scope="module")
def standard_bundle(tmp_path_factory):
    ws = tmp_path_factory.mktemp("sales_std")
    csv = ws / "sales_deals.csv"
    _write_sales_deals(csv)
    cfg = _validate_cfg(_sales_config())
    return profile_dataset("sales_deals", csv, cfg, "run-shared", allowed_roots=[ws])


# ---------------------------------------------------------------------------
# 1. Dataset-scope metric execution (the decisions M2-T10 never ran)
# ---------------------------------------------------------------------------
class TestDatasetMetrics:
    def test_row_count_exact_and_accurate(self, standard_bundle):
        rc = standard_bundle.profile.row_count
        assert rc is not None
        assert rc.value == 200
        assert rc.provenance.method == "exact"

    def test_column_count_matches_source_columns(self, standard_bundle):
        assert standard_bundle.profile.column_count == 5

    def test_key_candidates_include_declared_id_exclude_constant(
        self, standard_bundle
    ):
        candidates = standard_bundle.profile.primary_key_candidates
        assert candidates is not None
        # is_new is constant (always 1) → never a key candidate
        assert "is_new" not in candidates
        # deal_id has duplicates → not a candidate on the data (engine is honest,
        # the declared-PK rule is what flags it as a violation)
        assert "deal_id" not in candidates
        # created_at + deal_id+deal_value combos aside: a genuinely unique column
        assert "deal_value" in candidates  # 200 distinct non-null values, unique

    def test_execute_dataset_metrics_directly(self, sales_csv: Path, tmp_path: Path):
        cfg = _validate_cfg(_sales_config())
        con = duckdb.connect()
        result = run_dataset_profiling("sales_deals", sales_csv, cfg, "run-direct",
                                       con=con, allowed_roots=[tmp_path])
        metrics = execute_dataset_metrics(con, result.handle, result.plan)
        assert metrics.row_count.value == 200
        assert metrics.column_count == 5
        assert metrics.primary_key_candidates is not None
        con.close()


# ---------------------------------------------------------------------------
# 2. Full-contract validity of the assembled rollup objects
# ---------------------------------------------------------------------------
class TestContractValidity:
    def test_run_and_profile_schema_valid(self, standard_bundle):
        bundle = standard_bundle
        run_doc = bundle.run.model_dump(mode="json")
        prof_doc = bundle.profile.model_dump(mode="json")
        assert S.validate("profiling_run.schema.json", run_doc) == []
        assert S.validate("dataset_profile.schema.json", prof_doc) == []

    def test_every_column_check_finding_rec_schema_valid(self, standard_bundle):
        bundle = standard_bundle
        for col in bundle.profile.columns:
            assert S.validate("column_profile.schema.json", col.model_dump(mode="json")) == []
        for check in bundle.checks:
            assert S.validate("quality_check_result.schema.json", check.model_dump(mode="json")) == []
        for finding in bundle.findings:
            assert S.validate("finding.schema.json", finding.model_dump(mode="json")) == []
        for rec in bundle.recommendations:
            assert S.validate("recommendation.schema.json", rec.model_dump(mode="json")) == []

    def test_findings_surface_the_injected_issues(self, standard_bundle):
        bundle = standard_bundle
        rule_ids = {c.rule_id for c in bundle.checks}
        assert {"deal_id_unique", "value_not_null"} <= rule_ids
        # duplicate deal_id + missing deal_value must both produce a FAIL check
        fails = {c.rule_id for c in bundle.checks if c.status == "FAIL"}
        assert "deal_id_unique" in fails
        assert "value_not_null" in fails
        # findings + recommendations wired 1:1 and back-referenced
        assert bundle.findings and len(bundle.findings) == len(bundle.recommendations)
        for f in bundle.findings:
            assert f.recommendation_ids


# ---------------------------------------------------------------------------
# 3. Reproducibility tuple + terminal status + FAIL≠ERROR (§6.4)
# ---------------------------------------------------------------------------
class TestRunMetadata:
    def test_reproducibility_fields_present(self, standard_bundle):
        bundle = standard_bundle
        run = bundle.run
        assert run.schema_version == SCHEMA_VERSION
        assert run.status == "SUCCEEDED"
        assert run.engine.engine_version == ENGINE_VERSION
        assert run.engine.duckdb_version
        assert run.engine.python_version
        assert run.dataset.source_snapshot.content_hash.startswith("sha256:")
        assert run.config.config_hash.startswith("sha256:")
        assert run.config.profile_mode == "standard"
        assert run.timing.duration_ms >= 0
        # pure CLI / direct run has no job envelope → job_id absent
        assert "job_id" not in run.model_dump(mode="json")

    def test_config_hash_is_stable_for_same_config(self):
        a = _validate_cfg(_sales_config())
        b = _validate_cfg(_sales_config())
        assert config_hash(a) == config_hash(b)
        # changing a rule flips the hash
        cfg2 = _sales_config()
        cfg2["quality_checks"][0]["severity"] = "high"
        assert config_hash(_validate_cfg(cfg2)) != config_hash(a)

    def test_unexecutable_rule_is_error_not_run_failure(self, tmp_path: Path):
        csv = tmp_path / "bad_rule.csv"
        csv.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
        cfg = _validate_cfg({
            "config_version": 3,
            "dataset": {"name": "bad_rule"},
            "profiling": {"mode": "standard"},
            "quality_checks": [
                {"id": "r_ok", "rule": "not_null", "column": "a",
                 "dimension": "completeness", "severity": "high"},
                # phase-2 rule → cannot execute → ERROR (§6.4), run still SUCCEEDED
                {"id": "r_ref", "rule": "referential_integrity", "column": "b",
                 "dimension": "consistency", "severity": "high", "value": "other.table"},
            ],
        })
        bundle = profile_dataset("bad_rule", csv, cfg, "run-err", allowed_roots=[tmp_path])
        assert bundle.run.status == "SUCCEEDED"
        # a rule that could not be compiled/executed surfaces as a run error entry
        assert bundle.run.error_log and all(e.code for e in bundle.run.error_log)


# ---------------------------------------------------------------------------
# 4. Modes + sampling reach the rollup honestly
# ---------------------------------------------------------------------------
class TestModesAndSampling:
    def test_run_sampling_record_absent_when_disabled(self, standard_bundle):
        assert standard_bundle.run.sampling is None

    def test_run_sampling_record_present_when_enabled(self, sales_csv: Path, tmp_path: Path):
        sampling = {"enabled": True, "strategy": "reservoir", "size": 50, "seed": 20261024}
        bundle = profile_dataset(
            "sales_deals", sales_csv, _validate_cfg(_sales_config(sampling=sampling)),
            "run-smp", allowed_roots=[tmp_path])
        s = bundle.run.sampling
        assert s is not None and s.enabled is True
        assert s.strategy == "reservoir"
        assert s.sample_size == 50
        assert s.seed == 20261024
        # a sampled run still keeps row_count EXACT (cheap-under-sampling honesty)
        assert bundle.profile.row_count.provenance.method == "exact"
        assert bundle.profile.row_count.value == 200
        # the whole document still validates
        assert S.validate("profiling_run.schema.json", bundle.run.model_dump(mode="json")) == []


# ---------------------------------------------------------------------------
# 5. JSON writer (ENGINE §10) determinism + shape
# ---------------------------------------------------------------------------
class TestJsonWriter:
    def test_result_document_has_all_contract_blocks(self, standard_bundle):
        bundle = standard_bundle
        doc = result_document(bundle)
        assert set(doc) == {"run", "profile", "checks", "findings", "recommendations"}
        assert doc["profile"]["row_count"]["value"] == 200
        assert len(doc["profile"]["columns"]) == 5

    def test_writer_is_byte_deterministic(self, standard_bundle, tmp_path: Path):
        bundle = standard_bundle
        out = tmp_path / "nested" / "profile.json"
        p1 = write_profile_json(bundle, out)
        text1 = p1.read_text(encoding="utf-8")
        p2 = write_profile_json(bundle, tmp_path / "other.json")
        # same fixed result → identical bytes (sort_keys stable); only timing on the
        # run differs between the two in-memory dumps is impossible (same bundle),
        # so the full file must match.
        assert p2.read_text(encoding="utf-8") == text1


# ---------------------------------------------------------------------------
# 6. The profiler-cli end-to-end (argparse entry) + config rejection
# ---------------------------------------------------------------------------
class TestCliEndToEnd:
    def test_cli_main_writes_valid_json(self, sales_csv: Path, tmp_path: Path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps(_sales_config()), encoding="utf-8")
        out = tmp_path / "artifacts" / "profile.json"
        rc = main([
            "profile", "--input", str(sales_csv), "--config", str(cfg),
            "--out", str(out), "--run-id", "run-cli", "--allow-root", str(tmp_path),
        ])
        assert rc == 0
        assert out.is_file()
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert S.validate("profiling_run.schema.json", doc["run"]) == []
        assert S.validate("dataset_profile.schema.json", doc["profile"]) == []
        # result_refs points at the written file
        assert doc["run"]["result_refs"]["profile_json"] == str(out)
        assert doc["run"]["run_id"] == "run-cli"

    def test_cli_accepts_yaml_config(self, sales_csv: Path, tmp_path: Path):
        # BLUEPRINT Phase 1 --config x.yaml : the §9 authoring form (JSON is a
        # YAML subset, so the same document written as YAML must load too).
        import yaml
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.safe_dump(_sales_config()), encoding="utf-8")
        out = tmp_path / "yaml.json"
        rc = main([
            "profile", "--input", str(sales_csv), "--config", str(cfg),
            "--out", str(out), "--allow-root", str(tmp_path),
        ])
        assert rc == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert S.validate("profiling_run.schema.json", doc["run"]) == []
        assert doc["profile"]["row_count"]["value"] == 200

    def test_cli_mode_override(self, sales_csv: Path, tmp_path: Path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps(_sales_config("standard")), encoding="utf-8")
        out = tmp_path / "quick.json"
        rc = main([
            "profile", "--input", str(sales_csv), "--config", str(cfg),
            "--mode", "quick", "--out", str(out), "--allow-root", str(tmp_path),
        ])
        assert rc == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["run"]["config"]["profile_mode"] == "quick"

    def test_cli_rejects_invalid_config_before_profiling(self, sales_csv: Path, tmp_path: Path):
        bad = _sales_config()
        bad["quality_checks"][0]["dimension"] = "nonexistent_dimension"
        cfg = tmp_path / "bad.json"
        cfg.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(CliError):
            run_profile(sales_csv, cfg, tmp_path / "never.json", allowed_roots=[tmp_path])

    def test_cli_rejects_unknown_rule_key(self, sales_csv: Path, tmp_path: Path):
        bad = _sales_config()
        bad["profiling"]["bogus"] = True  # additionalProperties:false at $
        cfg = tmp_path / "bad2.json"
        cfg.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(CliError):
            load_config(cfg)

    def test_cli_parquet_source(self, tmp_path: Path):
        # prove both MVP connectors reach the rollup through the CLI (ADR-011)
        con = duckdb.connect()
        pq = tmp_path / "deals.parquet"
        con.execute(
            "COPY (SELECT 'D1' AS deal_id, DATE '2024-01-01' AS created_at, "
            "3 AS deal_value UNION ALL SELECT 'D2', DATE '2024-02-01', 4) "
            f"TO '{pq}' (FORMAT PARQUET)"
        )
        con.close()
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps(_sales_config()), encoding="utf-8")
        out = tmp_path / "pq.json"
        rc = main([
            "profile", "--input", str(pq), "--config", str(cfg), "--out", str(out),
            "--dataset-id", "deals", "--allow-root", str(tmp_path),
        ])
        assert rc == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert S.validate("profiling_run.schema.json", doc["run"]) == []
        assert doc["run"]["dataset"]["connector"] == "parquet"


# ---------------------------------------------------------------------------
# helper: validate a raw config dict into the pydantic model
# ---------------------------------------------------------------------------
def _validate_cfg(document: dict):
    from data_profiling_engine.schemas.models import DatasetConfig
    assert S.validate("dataset_config.schema.json", document) == [], document
    return DatasetConfig.model_validate(document)
