"""M5-T2 — real ``profile.start`` end-to-end through the bridge.

Covers the milestone's core claim (STATE NEXT): the pywebview facade + the
store-backed worker propagate an M2–M4 profiling run end-to-end — config flows
facade → worker → ``orchestration.rollup.profile_dataset`` → ``build_recommendations``
— the reporting artifacts land in the artifact store, the run + job persist to
SQLite, and the new ``DatasetConfig.language`` field (ADR-018) reaches the
narrative layer. It also pins the boundary behaviours the §5.2 profiling trio
must keep: an invalid config is rejected before any worker spawns (AGENTS §5),
``profile.get_status`` on an unknown id is a typed ``JOB_NOT_FOUND``, and the
SUCCEEDED status carries only the §5.4-bounded summary (never the document).

Workers are real spawn-method processes running DuckDB against the checked-in
golden fixture, so these are genuine integration tests (TESTING §2 integration),
not stubs. The whole context is redirected to ``tmp_path`` via
:func:`configure_runtime` so no test touches the real user-data directory.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from data_profiling_engine.bridge import BridgeFacade, configure_runtime, errors
from data_profiling_engine.bridge.worker.models import JobState
from data_profiling_engine.storage.metadata_store import MetadataStore

FIXTURES = Path(__file__).parent / "fixtures" / "golden"
CSV = FIXTURES / "sales_deals.csv"
CONFIG = FIXTURES / "sales_deals.config.json"

POLL_SECONDS = 90.0
POLL_INTERVAL = 0.1


@pytest.fixture()
def data_root(tmp_path):
    # Bind the bridge context to a throwaway user-data root for one test, then
    # restore the OS default so no later test inherits this manager/store.
    configure_runtime(tmp_path)
    yield tmp_path
    configure_runtime(None)


@pytest.fixture()
def facade() -> BridgeFacade:
    # The packaged facade: spike commands off, real §5.2 profile trio on.
    return BridgeFacade()


def _base_config(**overrides) -> dict:
    document = json.loads(CONFIG.read_text(encoding="utf-8"))
    document.update(overrides)
    return document


def _start(facade: BridgeFacade, *, config: dict, data_root: Path, **payload_overrides) -> str:
    resp = facade.bridge_send(
        {
            "command": "profile.start",
            "request_id": "req-start",
            "payload": {
                "dataset_id": "sales_deals",
                "source_path": str(CSV),
                "config": config,
                "allowed_roots": [str(CSV.parent)],
                **payload_overrides,
            },
        }
    )
    assert resp["status"] == "accepted", resp
    assert resp["request_id"] == "req-start"
    assert set(resp) == {"request_id", "status", "job_id"}  # §5.3 accepted shape
    return resp["job_id"]


def _poll_terminal(facade: BridgeFacade, job_id: str) -> dict:
    deadline = time.monotonic() + POLL_SECONDS
    seen_states: set[str] = set()
    while time.monotonic() < deadline:
        resp = facade.bridge_send(
            {
                "command": "profile.get_status",
                "request_id": "poll",
                "payload": {"job_id": job_id},
            }
        )
        assert resp["status"] == "ok", resp
        result = resp["result"]
        seen_states.add(result["status"])
        if result["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            assert set(result["progress"]) == {
                "completed_steps",
                "total_steps",
                "current_step",
            }
            return result
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"job {job_id} never reached a terminal state (saw {seen_states})")


# ---- happy path ----------------------------------------------------------


def test_profile_start_runs_engine_and_persists(facade: BridgeFacade, data_root: Path) -> None:
    job_id = _start(facade, config=_base_config(), data_root=data_root)

    # The job row exists in QUEUED immediately (created before the worker spawn).
    store = MetadataStore(data_root=data_root)
    created = store.get_job(job_id)
    assert created is not None and created["state"] in {"QUEUED", "RUNNING"}

    result = _poll_terminal(facade, job_id)
    assert result["status"] == "SUCCEEDED"
    # A clean run carries no §8 error code (exclude_none drops the key).
    assert result.get("error_code") is None

    # §5.4-bounded status payload: ids + refs + counts, never the document.
    bounded = result["result"]
    assert set(bounded) == {
        "run_id",
        "dataset_id",
        "result_refs",
        "row_count",
        "column_count",
        "finding_count",
        "check_count",
        "recommendation_count",
        "error_count",
    }
    assert bounded["dataset_id"] == "sales_deals"
    assert bounded["column_count"] and bounded["row_count"]

    # Every reporting artifact actually landed in the store at the ref.
    run_id = bounded["run_id"]
    refs = bounded["result_refs"]
    assert set(refs) == {"profile_json", "html_report", "issue_csv"}
    for ref in refs.values():
        assert (data_root / ref).is_file(), ref
        assert ref.startswith("artifacts/")

    # The persisted job reached its terminal state, and the run + artifacts have
    # a metadata row (single-writer cross-process: host wrote the job, worker the run).
    done = store.get_job(job_id)
    assert done["state"] == "SUCCEEDED"
    runs = store.list_runs("sales_deals")
    assert any(r["run_id"] == run_id for r in runs)
    artifact_rows = {row["ref_path"] for row in store.list_artifacts(run_id)}
    assert set(refs.values()) <= artifact_rows


def test_profile_json_artifact_is_the_canonical_document(
    facade: BridgeFacade, data_root: Path
) -> None:
    job_id = _start(facade, config=_base_config(), data_root=data_root)
    result = _poll_terminal(facade, job_id)
    bounded = result["result"]
    doc = json.loads((data_root / bounded["result_refs"]["profile_json"]).read_text("utf-8"))
    # Same canonical top-level keys the CLI/golden writer emits (ENGINE §10).
    assert set(doc) >= {"run", "profile", "checks", "findings", "recommendations"}
    assert doc["run"]["run_id"] == bounded["run_id"]
    # The run record links back to the §6 job that produced it.
    assert doc["run"]["job_id"] == job_id
    # Golden fixture injects exactly two FAIL rules → findings + recommendations.
    assert len(doc["findings"]) >= 1
    assert len(doc["recommendations"]) >= 1


# ---- language propagates facade → worker → build_recommendations ---------


def _recommendation_texts(data_root: Path, ref: str) -> str:
    doc = json.loads((data_root / ref).read_text("utf-8"))
    return json.dumps(doc["recommendations"], sort_keys=True, ensure_ascii=False)


def test_dataset_config_language_reaches_narrative(
    facade: BridgeFacade, data_root: Path
) -> None:
    # Same fixture, two languages → identical findings, *different* narrative
    # strings, proving ``config.language`` flowed all the way to the templates.
    en_job = _start(facade, config=_base_config(language="en"), data_root=data_root)
    en_refs = _poll_terminal(facade, en_job)["result"]["result_refs"]

    id_job = _start(facade, config=_base_config(language="id"), data_root=data_root)
    id_refs = _poll_terminal(facade, id_job)["result"]["result_refs"]

    en_text = _recommendation_texts(data_root, en_refs["profile_json"])
    id_text = _recommendation_texts(data_root, id_refs["profile_json"])
    assert en_text and id_text
    assert en_text != id_text, "language=id changed nothing — config.language did not propagate"


# ---- boundary behaviours (§5.2 / §5.4 / AGENTS §5) -----------------------


def test_invalid_config_rejected_before_worker_spawns(
    facade: BridgeFacade, data_root: Path
) -> None:
    bad = _base_config()
    bad["profiling"] = {}  # mode is required → schema + model reject
    resp = facade.bridge_send(
        {
            "command": "profile.start",
            "request_id": "req-bad",
            "payload": {
                "dataset_id": "sales_deals",
                "source_path": str(CSV),
                "config": bad,
                "allowed_roots": [str(CSV.parent)],
            },
        }
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.CONFIG_INVALID
    assert resp["error"]["retryable"] is False


def test_profile_get_status_unknown_job_is_typed_error(
    facade: BridgeFacade, data_root: Path
) -> None:
    resp = facade.bridge_send(
        {
            "command": "profile.get_status",
            "request_id": "req-unk",
            "payload": {"job_id": "job_never_started"},
        }
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.JOB_NOT_FOUND


def test_profile_cancel_unknown_job_is_typed_error(
    facade: BridgeFacade, data_root: Path
) -> None:
    resp = facade.bridge_send(
        {
            "command": "profile.cancel",
            "request_id": "req-canc",
            "payload": {"job_id": "job_never_started"},
        }
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.JOB_NOT_FOUND


def test_start_rejects_missing_required_payload_fields(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {
            "command": "profile.start",
            "request_id": "r",
            "payload": {"dataset_id": "ds"},  # no source_path / config
        }
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.REQUEST_INVALID


def test_spike_commands_absent_on_packaged_facade(facade: BridgeFacade) -> None:
    # §5.2 MVP set excludes the spike; echo is only reachable when explicitly
    # enabled (the spike suites), never through the default facade.
    resp = facade.bridge_send({"command": "echo", "request_id": "r", "payload": {"message": "x"}})
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.COMMAND_UNKNOWN


def test_terminal_job_state_recovers_as_failed_after_restart(tmp_path) -> None:
    # §6.3 recovery: a job the store still shows open, with no live worker in a
    # freshly-created manager, is reconciled to FAILED/WORKER_CRASHED on startup.
    from data_profiling_engine.bridge.worker.manager import JobManager

    store = MetadataStore(data_root=tmp_path)
    store.register_dataset(
        dataset_id="sales_deals", name="sales_deals", connector="csv", source_ref="x.csv"
    )
    store.create_job("job_orphan", "sales_deals", command="profile.start", state="RUNNING")

    recovered = JobManager(metadata_store=store).recover_interrupted_jobs()
    assert recovered >= 1
    assert store.get_job("job_orphan")["state"] == JobState.FAILED.value
    assert store.get_job("job_orphan")["error_code"] == errors.WORKER_CRASHED
