"""M1-T6 — bridge/job envelope schemas + formal error-code registry.

Covers CONTRACTS §5.3 message envelopes (request / response / error / polled
status), the §5.4 discriminated body coupling (exactly one of result/job_id/
error per status), the §6.2 job-state enum, and the §8 error-code registry
module promoted in M1-T6 (``data_profiling_engine.errors``).

Complements the field/required/enum parity already asserted in
``test_models.py`` (CANONICAL_ROOTS) and the transport behaviour asserted in
``test_bridge_facade.py`` / ``test_worker_job.py``.
"""
from __future__ import annotations

import copy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from data_profiling_engine import errors as registry
from data_profiling_engine import schemas as S
from data_profiling_engine.schemas import models as M

# ---- §5.3 literal examples (positive) -----------------------------------

REQUEST_EXAMPLE = {
    "command": "profile.start",
    "request_id": "req-1",
    "payload": {"dataset_id": "ds_1", "profile_mode": "standard"},
}
ACCEPTED_EXAMPLE = {"request_id": "req-1", "status": "accepted", "job_id": "job_1"}
STATUS_EXAMPLE = {
    "job_id": "job_1",
    "status": "RUNNING",
    "progress": {
        "completed_steps": 3,
        "total_steps": 8,
        "current_step": "profiling:distributions",
    },
}
ERROR_EXAMPLE = {
    "request_id": "req-1",
    "status": "error",
    "error": {
        "code": "DATASET_READ_FAILED",
        "message": "Dataset could not be read.",
        "retryable": False,
    },
}


@pytest.mark.parametrize(
    "schema_name,example",
    [
        ("bridge_request.schema.json", REQUEST_EXAMPLE),
        ("bridge_response.schema.json", ACCEPTED_EXAMPLE),
        ("bridge_response.schema.json", ERROR_EXAMPLE),
        ("bridge_error.schema.json", ERROR_EXAMPLE["error"]),
        ("job_status.schema.json", STATUS_EXAMPLE),
    ],
)
def test_envelope_examples_validate(schema_name, example):
    assert S.validate(schema_name, example) == [], (schema_name, example)


def test_ok_response_validates_and_round_trips():
    ok = {"request_id": "req-2", "status": "ok", "result": {"message": "ping"}}
    assert S.validate("bridge_response.schema.json", ok) == []
    dumped = M.BridgeResponse.model_validate(ok).model_dump(mode="json")
    assert dumped == ok
    assert S.validate("bridge_response.schema.json", dumped) == []


# ---- §5.4 discriminated body coupling (schema-side negatives) -----------

BAD_RESPONSES = [
    # ok without result
    {"request_id": "r", "status": "ok"},
    # accepted without job_id
    {"request_id": "r", "status": "accepted"},
    # error without error object
    {"request_id": "r", "status": "error"},
    # ok must not also carry error
    {"request_id": "r", "status": "ok", "result": {"x": 1}, "error": {"code": "JOB_TIMEOUT", "message": "m"}},
    # accepted must not carry result
    {"request_id": "r", "status": "accepted", "job_id": "j", "result": {"x": 1}},
    # unknown status
    {"request_id": "r", "status": "partial", "result": {"x": 1}},
]


@pytest.mark.parametrize("bad", BAD_RESPONSES)
def test_response_schema_rejects_bad_body_combination(bad):
    assert S.validate("bridge_response.schema.json", bad) != []


@pytest.mark.parametrize("bad", BAD_RESPONSES)
def test_response_model_rejects_bad_body_combination(bad):
    with pytest.raises(ValidationError):
        M.BridgeResponse.model_validate(bad)


# ---- envelope hygiene (request side) ------------------------------------

def test_request_rejects_extra_top_level_field():
    with pytest.raises(ValidationError):
        M.BridgeRequest.model_validate(dict(REQUEST_EXAMPLE, op="delete_all"))
    assert S.validate("bridge_request.schema.json", dict(REQUEST_EXAMPLE, op="x")) != []


def test_request_rejects_empty_command_and_missing_request_id():
    for bad in (
        {"command": "", "request_id": "r", "payload": {}},
        {"command": "echo"},
        {"request_id": "r"},
    ):
        assert S.validate("bridge_request.schema.json", bad) != []


def test_request_allows_empty_payload():
    assert S.validate("bridge_request.schema.json", {"command": "profile.cancel", "request_id": "r"}) == []
    m = M.BridgeRequest.model_validate({"command": "profile.cancel", "request_id": "r"})
    assert m.payload == {}


# ---- error object: §8 code shape ----------------------------------------

def test_error_object_rejects_non_uppercase_code():
    with pytest.raises(ValidationError):
        M.BridgeErrorObject(code="dataset_read_failed", message="boom")
    assert S.validate("bridge_error.schema.json", {"code": "lower_case", "message": "boom"}) != []


def test_error_object_requires_message():
    with pytest.raises(ValidationError):
        M.BridgeErrorObject(code="JOB_TIMEOUT")
    assert S.validate("bridge_error.schema.json", {"code": "JOB_TIMEOUT"}) != []


# ---- §6.2 job status -----------------------------------------------------

def test_job_status_terminal_and_non_terminal_states_validate():
    for state in ["QUEUED", "RUNNING", "CANCEL_REQUESTED", "SUCCEEDED", "FAILED", "CANCELLED"]:
        doc = {"job_id": "j", "status": state, "progress": {"completed_steps": 0, "total_steps": 1, "current_step": ""}}
        assert S.validate("job_status.schema.json", doc) == [], state


def test_job_status_rejects_unknown_state_and_negative_progress():
    bad_state = copy.deepcopy(STATUS_EXAMPLE)
    bad_state["status"] = "PAUSED"
    assert S.validate("job_status.schema.json", bad_state) != []
    bad_prog = copy.deepcopy(STATUS_EXAMPLE)
    bad_prog["progress"]["completed_steps"] = -1
    assert S.validate("job_status.schema.json", bad_prog) != []


def test_job_status_failed_carries_registry_code():
    doc = {
        "job_id": "j",
        "status": "FAILED",
        "progress": {"completed_steps": 1, "total_steps": 4, "current_step": ""},
        "error_code": "WORKER_CRASHED",
    }
    assert S.validate("job_status.schema.json", doc) == []
    m = M.JobStatus.model_validate(doc)
    assert m.model_dump(mode="json")["error_code"] == "WORKER_CRASHED"


def test_spike_runtime_status_validates_against_canonical_schema():
    """The M0-T3 spike worker model must produce §5.3-conformant status objects."""
    from data_profiling_engine.bridge.worker.models import JobProgress, JobStatus as SpikeStatus, JobState

    spike = SpikeStatus(
        job_id="job_spike",
        status=JobState.RUNNING,
        progress=JobProgress(completed_steps=2, total_steps=5, current_step="profiling:uniqueness"),
    )
    dumped = spike.model_dump(mode="json", exclude_none=True)
    assert S.validate("job_status.schema.json", dumped) == [], dumped


# ---- §8 error-code registry module --------------------------------------

# Expected Retryable column exactly as docs/CONTRACTS.md §8 defines ("maybe" and
# "no" both map to the default; only the marked-retryable rows are True).
_DOC_RETRYABLE = {
    "CONFIG_INVALID": False,
    "DATASET_READ_FAILED": True,
    "SOURCE_NOT_ALLOWED": False,
    "SCHEMA_INFERENCE_FAILED": False,
    "METRIC_NOT_COMPUTED": False,
    "RULE_EXEC_FAILED": True,
    "WORKER_CRASHED": True,
    "JOB_TIMEOUT": False,
    "BRIDGE_PAYLOAD_TOO_LARGE": False,
    "BRIDGE_COMMAND_UNKNOWN": False,
    "ARTIFACT_WRITE_FAILED": True,
    "BRIDGE_REQUEST_INVALID": False,  # promoted M1-T6
    "JOB_NOT_FOUND": False,  # promoted M1-T6
}


def test_registry_covers_all_doc_codes():
    assert set(_DOC_RETRYABLE) == {c.value for c in registry.ErrorCode}
    assert set(registry.ERROR_REGISTRY) == set(registry.ErrorCode)


@pytest.mark.parametrize("code,retryable", sorted(_DOC_RETRYABLE.items()))
def test_registry_retryable_matches_doc(code, retryable):
    assert registry.is_retryable(code) is retryable, code


def test_promoted_spike_codes_are_registered():
    for code in ("BRIDGE_REQUEST_INVALID", "JOB_NOT_FOUND"):
        assert registry.is_registered(code), code
        info = registry.lookup(code)
        assert info is not None and info.layer in {"bridge", "job"}


def test_bridge_alias_module_delegates_to_registry():
    """bridge.errors short names stay plain strings equal to §8 canonical values."""
    from data_profiling_engine.bridge import errors as bridge_errors

    assert bridge_errors.REQUEST_INVALID == registry.ErrorCode.BRIDGE_REQUEST_INVALID.value
    assert bridge_errors.JOB_NOT_FOUND == registry.ErrorCode.JOB_NOT_FOUND.value
    assert bridge_errors.COMMAND_UNKNOWN == "BRIDGE_COMMAND_UNKNOWN"
    assert isinstance(bridge_errors.REQUEST_INVALID, str)


def test_registry_unknown_code_is_not_registered():
    assert registry.lookup("NOT_A_CODE") is None
    assert registry.is_registered("NOT_A_CODE") is False
    assert registry.is_retryable("NOT_A_CODE") is False


def test_error_code_enum_values_are_unique():
    values = [c.value for c in registry.ErrorCode]
    assert len(values) == len(set(values))
