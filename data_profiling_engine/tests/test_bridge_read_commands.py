"""M5-T1 — the remaining §5.2 read commands on the bridge facade.

Covers the milestone's core claim: every MVP command in the CONTRACTS §5.2 table
is wired, schema-gated and served from the M3 stores (or, for ``dataset.*``,
through the M2 connector seam against the source file) — with the §5.4 payload
rules enforced in **both** directions (request cap in the envelope, row/limit
caps in the payload models, response cap re-checked before the envelope is
built) and the SECURITY §3 scope rules (path allow-list, artifact refs only
through recorded rows).

The store is seeded by a REAL in-process engine run (``profile_dataset`` →
``MetadataStore.save_run``), so the reads exercise the same persisted rows a
``profile.start`` worker would have written, without paying for a spawn.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.bridge import COMMANDS, BridgeFacade, configure_runtime
from data_profiling_engine.bridge import errors
from data_profiling_engine.bridge import facade as facade_mod
from data_profiling_engine.orchestration.rollup import profile_dataset
from data_profiling_engine.reporting import result_document
from data_profiling_engine.reporting.csv_export import emit_issue_csv_artifact
from data_profiling_engine.reporting.html_report import emit_html_report_artifact
from data_profiling_engine.schemas.models import DatasetConfig
from data_profiling_engine.storage.artifact_store import ArtifactStore
from data_profiling_engine.storage.metadata_store import MetadataStore

from . import golden_harness as gh

RUN_ID = "run-m5t1"
DATASET_ID = "sales_deals"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bridge(tmp_path_factory):
    """One real run persisted into a throwaway user-data root, bridge bound to it."""
    root = tmp_path_factory.mktemp("m5t1-bridge")
    config = gh.load_config("sales_deals")
    artifacts = ArtifactStore(data_root=root)
    refs = {kind: artifacts.ref_path(RUN_ID, kind)
            for kind in ("profile_json", "html_report", "issue_csv")}
    # refs are handed to the engine up-front, exactly as bridge/worker/profile_job
    # does: they land on the run document, so ``report.get_summary`` can read them
    # back (load_profiling_run only returns keys the stored document carried).
    bundle = profile_dataset(
        DATASET_ID, gh.GOLDEN_DIR / "sales_deals.csv", config, RUN_ID,
        allowed_roots=[gh.GOLDEN_DIR], result_refs=refs,
    )
    artifacts.write_document(RUN_ID, "profile_json", result_document(bundle))
    emit_html_report_artifact(bundle, artifacts)
    emit_issue_csv_artifact(bundle, artifacts)
    meta = MetadataStore(data_root=root)
    meta.save_run(bundle, config=config, artifact_refs=refs)
    meta.close()

    configure_runtime(root)
    yield root
    configure_runtime(None)


@pytest.fixture()
def facade() -> BridgeFacade:
    return BridgeFacade()  # packaged facade: spike commands off


def send(facade: BridgeFacade, command: str, payload: Dict[str, Any], rid: str = "req-1"):
    return facade.bridge_send({"command": command, "request_id": rid, "payload": payload})


def ok(facade: BridgeFacade, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = send(facade, command, payload)
    assert resp["status"] == "ok", resp
    return resp["result"]


# ---------------------------------------------------------------------------
# 1. the allow-list is the contract table
# ---------------------------------------------------------------------------

def test_commands_are_exactly_the_mvp_set() -> None:
    # CONTRACTS §5.2 table, row for row — nothing more, nothing less.
    assert set(COMMANDS) == {
        "dataset.inspect",
        "dataset.preview",
        "profile.start",
        "profile.get_status",
        "profile.cancel",
        "quality.get_results",
        "findings.get",
        "report.get_summary",
        "artifact.open",
    }


def test_every_command_has_a_schema_gated_payload() -> None:
    for name, spec in COMMANDS.items():
        assert spec.name == name
        assert spec.payload_model is not None, f"{name} payload must be validated"
        assert spec.payload_model.model_config.get("extra") == "forbid", name


# ---------------------------------------------------------------------------
# 2. dataset.inspect — schema/types/size metadata, never rows
# ---------------------------------------------------------------------------


class TestDatasetInspect:
    def test_returns_types_and_size_without_rows(self, facade, bridge) -> None:
        result = ok(facade, "dataset.inspect", {
            "source_path": str(gh.GOLDEN_DIR / "sales_deals.csv"),
            "allowed_roots": [str(gh.GOLDEN_DIR)],
        })
        assert result["connector"] == "csv"
        assert result["column_count"] == len(result["columns"])
        assert [c["name"] for c in result["columns"]] == [
            "deal_id", "created_at", "stage", "deal_value", "is_new"
        ]
        assert result["content_hash"].startswith("sha256:")
        assert result["size_bytes"] > 0
        assert "rows" not in result  # inspect is metadata only (ADR-007)

    def test_joins_stored_types_when_the_dataset_has_run(self, facade, bridge) -> None:
        result = ok(facade, "dataset.inspect", {"dataset_id": DATASET_ID,
                                                "source_path": str(gh.GOLDEN_DIR / "sales_deals.csv")})
        by_name = {c["name"]: c for c in result["columns"]}
        assert by_name["deal_id"]["logical_type"]  # from the M3 column inventory
        assert by_name["stage"]["semantic_type"]
        assert result["last_run"]["run_id"] == RUN_ID
        assert result["row_count"] == 40

    def test_path_outside_the_allow_list_is_refused(self, facade, bridge) -> None:
        resp = send(facade, "dataset.inspect", {
            "source_path": str(gh.GOLDEN_DIR / "sales_deals.csv"),
            "allowed_roots": [str(bridge)],
        })
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.SOURCE_NOT_ALLOWED

    def test_missing_source_is_a_typed_read_failure(self, facade, bridge) -> None:
        resp = send(facade, "dataset.inspect", {"source_path": str(bridge / "nope.csv")})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.DATASET_READ_FAILED

    def test_non_mvp_suffix_is_refused(self, facade, bridge, tmp_path) -> None:
        other = tmp_path / "sheet.tsv"
        other.write_text("a\tb\n1\t2\n", encoding="utf-8")
        resp = send(facade, "dataset.inspect", {"source_path": str(other)})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.SOURCE_NOT_ALLOWED

    def test_extra_payload_field_is_rejected(self, facade, bridge) -> None:
        resp = send(facade, "dataset.inspect", {
            "source_path": str(gh.GOLDEN_DIR / "sales_deals.csv"), "sql": "DROP TABLE runs",
        })
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID
        assert "DROP TABLE" not in json.dumps(resp)  # SECURITY §3: no echo


# ---------------------------------------------------------------------------
# 3. dataset.preview — bounded rows (§5.4)
# ---------------------------------------------------------------------------


class TestDatasetPreview:
    def test_page_is_bounded_and_paged(self, facade, bridge) -> None:
        payload = {"source_path": str(gh.GOLDEN_DIR / "sales_deals.csv"),
                   "allowed_roots": [str(gh.GOLDEN_DIR)]}
        first = ok(facade, "dataset.preview", {**payload, "limit": 10})
        assert first["returned"] == 10
        assert first["total_rows"] == 40
        assert all(len(row) == 5 for row in first["rows"])
        second = ok(facade, "dataset.preview", {**payload, "limit": 10, "offset": 10})
        assert second["rows"][0] != first["rows"][0]
        assert second["offset"] == 10

    def test_row_id_plumbing_is_not_exposed(self, facade, bridge) -> None:
        result = ok(facade, "dataset.preview", {
            "source_path": str(gh.GOLDEN_DIR / "sales_deals.csv"), "limit": 3,
        })
        names = [c["name"] for c in result["columns"]]
        assert "_row_id" not in names
        assert names == ["deal_id", "created_at", "stage", "deal_value", "is_new"]

    def test_row_cap_is_enforced_by_the_payload_schema(self, facade, bridge) -> None:
        # §5.4: preview ≤ 200 rows. 201 is not a valid request at all.
        resp = send(facade, "dataset.preview", {
            "source_path": str(gh.GOLDEN_DIR / "sales_deals.csv"), "limit": 201,
        })
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID

    def test_pii_values_are_masked_by_default(self, facade, bridge, tmp_path) -> None:
        csv = tmp_path / "contacts.csv"
        csv.write_text(
            "name,email,phone,note\n"
            "Ann,ann.example@acme.io,+62 811-2222-3333,clean\n"
            "Bob,bob.second@acme.io,+62 812-9999-8888,clean\n"
            "Cal,cal.third@acme.io,+62 813-1111-2222,clean\n",
            encoding="utf-8",
        )
        result = ok(facade, "dataset.preview", {"source_path": str(csv)})
        flat = json.dumps(result)
        assert "ann.example@acme.io" not in flat
        assert "8112222" not in flat.replace(" ", "")
        masked = {c["name"]: c["masked"] for c in result["columns"]}
        assert masked["email"] and masked["phone"]
        assert not masked["note"]
        assert sorted(result["masking_applied"]) == ["email", "phone"]

    def test_masking_can_be_explicitly_disabled(self, facade, bridge, tmp_path) -> None:
        csv = tmp_path / "contacts2.csv"
        csv.write_text("email\nann.example@acme.io\nbob.second@acme.io\ncal.third@acme.io\n",
                       encoding="utf-8")
        result = ok(facade, "dataset.preview", {"source_path": str(csv), "mask_pii": False})
        assert result["rows"][0][0] == "ann.example@acme.io"
        assert result["masking_applied"] == []


# ---------------------------------------------------------------------------
# 4. quality.get_results / findings.get — paged contract objects
# ---------------------------------------------------------------------------


class TestQualityResults:
    def test_page_carries_contract_objects_and_total(self, facade, bridge) -> None:
        result = ok(facade, "quality.get_results", {"run_id": RUN_ID})
        assert result["run_id"] == RUN_ID
        assert result["page"]["total"] == len(result["items"])
        for item in result["items"]:
            assert S.validate("quality_check_result.schema.json", item) == []

    def test_paging_walks_the_whole_set(self, facade, bridge) -> None:
        all_rows = ok(facade, "quality.get_results", {"run_id": RUN_ID})["items"]
        first = ok(facade, "quality.get_results", {"run_id": RUN_ID, "limit": 1, "offset": 0})
        second = ok(facade, "quality.get_results", {"run_id": RUN_ID, "limit": 1, "offset": 1})
        assert first["page"]["total"] == len(all_rows)
        ids = {r["rule_id"] for r in all_rows}
        assert {first["items"][0]["rule_id"], second["items"][0]["rule_id"]} <= ids

    def test_status_filter_narrows_and_counts(self, facade, bridge) -> None:
        result = ok(facade, "quality.get_results", {"run_id": RUN_ID, "status": "FAIL"})
        assert result["items"], "golden fixture injects FAIL rules"
        assert all(item["status"] == "FAIL" for item in result["items"])
        assert result["page"]["total"] == len(result["items"])

    def test_unknown_run_reference_is_a_typed_error(self, facade, bridge) -> None:
        resp = send(facade, "quality.get_results", {"run_id": "run_never_ran"})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID

    def test_typo_filter_is_refused_not_ignored(self, facade, bridge) -> None:
        resp = send(facade, "quality.get_results", {"run_id": RUN_ID, "status": "FALIED"})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID

    def test_page_requires_an_address(self, facade, bridge) -> None:
        resp = send(facade, "quality.get_results", {"limit": 10})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID

    def test_dataset_id_resolves_to_the_latest_run(self, facade, bridge) -> None:
        result = ok(facade, "quality.get_results", {"dataset_id": DATASET_ID})
        assert result["run_id"] == RUN_ID


class TestFindingsGet:
    def test_items_keep_fingerprint_and_fact_provenance(self, facade, bridge) -> None:
        result = ok(facade, "findings.get", {"run_id": RUN_ID})
        assert result["items"], "golden fixture yields findings"
        for item in result["items"]:
            assert S.validate("finding.schema.json", item) == []
            assert item["fingerprint"]  # ADR-016
            for fact in item["facts"]:
                assert fact["provenance"]["method"]  # ADR-015
        assert result["page"]["total"] == len(result["items"])

    def test_severity_filter(self, facade, bridge) -> None:
        result = ok(facade, "findings.get", {"run_id": RUN_ID, "severity": "critical"})
        assert all(item["severity"] == "critical" for item in result["items"])

    def test_findings_are_paged(self, facade, bridge) -> None:
        total = ok(facade, "findings.get", {"run_id": RUN_ID})["page"]["total"]
        page = ok(facade, "findings.get", {"run_id": RUN_ID, "limit": 1})
        assert page["page"]["total"] == total
        assert len(page["items"]) == min(1, total)


# ---------------------------------------------------------------------------
# 5. report.get_summary — counts, not bodies
# ---------------------------------------------------------------------------


class TestReportSummary:
    def test_summary_shape_and_counts(self, facade, bridge) -> None:
        result = ok(facade, "report.get_summary", {"run_id": RUN_ID})
        assert result["dataset_id"] == DATASET_ID
        assert result["status"] == "SUCCEEDED"
        assert result["row_count"] == 40
        assert result["column_count"] == 5
        assert set(result["check_status_counts"]) <= {"PASS", "FAIL", "WARN", "ERROR"}
        assert result["result_refs"]["profile_json"].startswith("artifacts/")
        # A summary is counts + refs only: no column metric bodies (§5.4).
        assert "metrics" not in json.dumps(result)

    def test_summary_is_small_enough_for_the_bridge(self, facade, bridge) -> None:
        result = ok(facade, "report.get_summary", {"dataset_id": DATASET_ID})
        assert len(json.dumps(result).encode("utf-8")) < facade_mod.PAYLOAD_CAP_BYTES

    def test_sampling_record_is_three_state(self, facade, bridge) -> None:
        # The golden run declares no sampling window → sampling_enabled is None
        # (absent), NOT a fabricated False (AGENTS §3: absent ≠ null).
        result = ok(facade, "report.get_summary", {"run_id": RUN_ID})
        assert result["sampling_enabled"] is None
        assert result["sample_seed"] is None

    def test_unknown_run_is_a_typed_error(self, facade, bridge) -> None:
        resp = send(facade, "report.get_summary", {"run_id": "run_never_ran"})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID


# ---------------------------------------------------------------------------
# 6. artifact.open — allow-listed refs, ack only
# ---------------------------------------------------------------------------


class TestArtifactOpen:
    def test_open_by_kind_returns_an_ack_not_the_content(self, facade, bridge) -> None:
        result = ok(facade, "artifact.open", {"run_id": RUN_ID, "kind": "html_report"})
        assert result["ref"].startswith("artifacts/")
        assert result["size_bytes"] > 0
        assert set(result) == {"run_id", "kind", "ref", "size_bytes"}
        assert "<html" not in json.dumps(result).lower()

    def test_open_by_a_recorded_ref(self, facade, bridge) -> None:
        recorded = MetadataStore(data_root=bridge).artifact_ref(RUN_ID, "issue_csv")
        result = ok(facade, "artifact.open", {"run_id": RUN_ID, "ref": recorded})
        assert result["ref"] == recorded

    def test_a_ref_the_store_does_not_record_is_refused(self, facade, bridge) -> None:
        resp = send(facade, "artifact.open", {
            "run_id": RUN_ID, "ref": f"artifacts/{RUN_ID}/../../secrets.txt",
        })
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.SOURCE_NOT_ALLOWED

    def test_an_absolute_outside_path_is_refused(self, facade, bridge) -> None:
        resp = send(facade, "artifact.open", {"run_id": RUN_ID, "ref": "/etc/passwd"})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.SOURCE_NOT_ALLOWED

    def test_kind_and_ref_are_mutually_exclusive(self, facade, bridge) -> None:
        resp = send(facade, "artifact.open", {
            "run_id": RUN_ID, "kind": "html_report", "ref": "artifacts/x/report.html",
        })
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID

    def test_unrecorded_kind_is_a_typed_error(self, facade, bridge) -> None:
        resp = send(facade, "artifact.open", {"run_id": RUN_ID, "kind": "nope"})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID

    def test_reveal_asks_the_os_and_nothing_else(self, facade, bridge, monkeypatch) -> None:
        seen: list = []
        monkeypatch.setattr(facade_mod, "_reveal_path", lambda path: seen.append(path))
        result = ok(facade, "artifact.open", {"run_id": RUN_ID, "kind": "issue_csv", "reveal": True})
        assert seen and Path(seen[0]).is_file()
        assert result["kind"] == "issue_csv"

    def test_reveal_is_off_by_default(self, facade, bridge, monkeypatch) -> None:
        def _boom(path):  # pragma: no cover - must not run
            raise AssertionError("reveal fired without ask")

        monkeypatch.setattr(facade_mod, "_reveal_path", _boom)
        assert ok(facade, "artifact.open", {"run_id": RUN_ID, "kind": "profile_json"})


# ---------------------------------------------------------------------------
# 7. the response direction of the §5.4 cap
# ---------------------------------------------------------------------------


def test_oversized_response_is_refused_as_payload_too_large(facade, bridge, monkeypatch) -> None:
    """A handler that assembled more than 1 MiB never reaches the WebView."""

    def _huge(payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"blob": "x" * (facade_mod.PAYLOAD_CAP_BYTES + 1024)}

    monkeypatch.setitem(
        facade._commands,  # noqa: SLF001 — test reaches the resolved table
        "report.get_summary",
        facade_mod.CommandSpec(name="report.get_summary",
                               payload_model=facade_mod.ReportSummaryPayload,
                               handler=_huge),
    )
    resp = send(facade, "report.get_summary", {"run_id": RUN_ID})
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.PAYLOAD_TOO_LARGE


def test_handler_crash_never_leaks_internals(facade, bridge, monkeypatch) -> None:
    def _boom(payload: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("sqlite row /Users/jensu/private leaked")

    monkeypatch.setitem(
        facade._commands,  # noqa: SLF001
        "findings.get",
        facade_mod.CommandSpec(name="findings.get",
                               payload_model=facade_mod.FindingsGetPayload,
                               handler=_boom),
    )
    resp = send(facade, "findings.get", {"run_id": RUN_ID})
    assert resp["status"] == "error"
    assert "/Users/jensu/private" not in json.dumps(resp)


# ---------------------------------------------------------------------------
# 8. error ``retryable`` is the §8 registry's answer, not the bridge's guess
# ---------------------------------------------------------------------------


def test_retryable_flag_follows_the_error_registry(facade, bridge) -> None:
    # A source that may simply be unavailable is worth retrying …
    resp = send(facade, "dataset.inspect", {"source_path": str(bridge / "nope.csv")})
    assert resp["error"]["code"] == errors.DATASET_READ_FAILED
    assert resp["error"]["retryable"] is True
    # … a malformed request never is.
    resp = send(facade, "dataset.inspect", {"source_path": 1})
    assert resp["error"]["code"] == errors.REQUEST_INVALID
    assert resp["error"]["retryable"] is False
