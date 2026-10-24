"""Bridge facade tests — M0-T2 spike.

Covers CONTRACTS §5.1–§5.4 and SECURITY §3 boundary rules: allow-list
dispatch, envelope + payload schema validation (extra fields forbidden),
1 MiB payload cap, and the guarantee that the facade never raises into the
WebView. Real per-command behaviour tests land with each command in M5-T1.
"""
from __future__ import annotations

import json

import pytest

from data_profiling_engine.bridge import (
    PAYLOAD_CAP_BYTES,
    BridgeFacade,
    BridgeRequest,
    BridgeResponse,
    EchoPayload,
)
from data_profiling_engine.bridge import errors


@pytest.fixture()
def facade() -> BridgeFacade:
    # Spike tests exercise the M0 echo pathway; the packaged facade leaves
    # these commands off (M5-T2 spike-behind-a-flag).
    return BridgeFacade(enable_spike_commands=True)


# ---- happy path ----------------------------------------------------------


def test_echo_round_trips_message(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {"command": "echo", "request_id": "req-1", "payload": {"message": "ping"}}
    )
    assert resp == {
        "request_id": "req-1",
        "status": "ok",
        "result": {"message": "ping", "echoed_by": "bridge"},
    }


def test_echo_accepts_empty_message(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {"command": "echo", "request_id": "req-2", "payload": {"message": ""}}
    )
    assert resp["status"] == "ok"
    assert resp["result"]["message"] == ""


def test_accepts_json_string_request(facade: BridgeFacade) -> None:
    raw = json.dumps(
        {"command": "echo", "request_id": "req-3", "payload": {"message": "hi"}}
    )
    resp = facade.bridge_send(raw)
    assert resp["status"] == "ok"
    assert resp["result"]["message"] == "hi"


# ---- allow-list & dispatch ----------------------------------------------


def test_unknown_command_returns_bridge_command_unknown(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {"command": "drop_table", "request_id": "req-x", "payload": {}}
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.COMMAND_UNKNOWN
    assert resp["error"]["retryable"] is False
    # Message must not leak internals.
    assert "Traceback" not in resp["error"]["message"]


def test_handler_exception_never_leaks(facade: BridgeFacade, monkeypatch) -> None:
    def _boom(payload: dict) -> dict:
        raise RuntimeError("secret internal value /path/users/foo")

    from data_profiling_engine.bridge import facade as facade_mod

    # The facade snapshots its command table at construction, so patch the
    # live instance's resolved map (echo is a spike command enabled above).
    monkeypatch.setitem(
        facade._commands,  # noqa: SLF001 — test reaches the resolved table
        "echo",
        facade_mod.CommandSpec(name="echo", payload_model=EchoPayload, handler=_boom),
    )
    resp = facade.bridge_send(
        {"command": "echo", "request_id": "req-y", "payload": {"message": "x"}}
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.REQUEST_INVALID
    # SECURITY §3: no raw internal message / filesystem path leaks through.
    assert "secret internal value" not in resp["error"]["message"]
    assert "/path/users/foo" not in resp["error"]["message"]


# ---- envelope schema validation -----------------------------------------


@pytest.mark.parametrize(
    "bad_request",
    [
        {},  # missing every required field
        {"request_id": "r"},  # missing command
        {"command": "echo"},  # missing request_id
        {"command": "", "request_id": "r", "payload": {}},  # empty command
        {"command": "echo", "request_id": "r", "payload": []},  # payload wrong type
        {
            "command": "echo",
            "request_id": "r",
            "payload": {"message": "m"},
            "extra": "nope",
        },  # extra top-level field
    ],
)
def test_envelope_rejects_bad_shape(facade: BridgeFacade, bad_request: dict) -> None:
    resp = facade.bridge_send(bad_request)
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.REQUEST_INVALID
    assert resp["request_id"] == bad_request.get("request_id", "")


def test_envelope_rejects_non_object(facade: BridgeFacade) -> None:
    for bad in ([1, 2, 3], "not-json", 42, None, True):
        resp = facade.bridge_send(bad)
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID


# ---- payload schema validation ------------------------------------------


def test_payload_rejects_missing_field(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {"command": "echo", "request_id": "r-1", "payload": {}}
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.REQUEST_INVALID
    assert "message" in resp["error"]["message"]


def test_payload_rejects_unknown_field(facade: BridgeFacade) -> None:
    resp = facade.bridge_send(
        {
            "command": "echo",
            "request_id": "r-2",
            "payload": {"message": "hi", "sneaky": "drop"},
        }
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.REQUEST_INVALID
    assert "sneaky" in resp["error"]["message"]


def test_error_message_does_not_echo_input_value(facade: BridgeFacade) -> None:
    secret = "SSN-123-45-6789"
    resp = facade.bridge_send(
        {"command": "echo", "request_id": "r-3", "payload": {"message": 42, "leak": secret}}
    )
    assert resp["status"] == "error"
    assert secret not in json.dumps(resp)
    assert "42" not in resp["error"]["message"]


# ---- payload cap ---------------------------------------------------------


def test_oversized_payload_is_rejected(facade: BridgeFacade) -> None:
    big = "a" * (PAYLOAD_CAP_BYTES + 1024)
    resp = facade.bridge_send(
        {
            "command": "echo",
            "request_id": "r-cap",
            # `message` fails EchoPayload? no — we hit cap first because
            # size check precedes schema validation.
            "payload": {"message": big},
        }
    )
    assert resp["status"] == "error"
    assert resp["error"]["code"] == errors.PAYLOAD_TOO_LARGE


def test_within_cap_passes(facade: BridgeFacade) -> None:
    # A ~500 KiB message stays comfortably below 1 MiB.
    medium = "a" * (500 * 1024)
    resp = facade.bridge_send(
        {"command": "echo", "request_id": "r-ok", "payload": {"message": medium}}
    )
    assert resp["status"] == "ok"
    assert resp["result"]["message"] == medium


# ---- envelope model contracts (round-trip) ------------------------------


def test_response_envelope_round_trips() -> None:
    resp = BridgeResponse(
        request_id="r-rt",
        status="ok",
        result={"message": "hi", "echoed_by": "bridge"},
    )
    dumped = resp.model_dump(exclude_none=True)
    again = BridgeResponse.model_validate(dumped)
    assert again == resp


def test_request_forbids_extra_fields() -> None:
    with pytest.raises(Exception):
        BridgeRequest.model_validate(
            {"command": "echo", "request_id": "r", "payload": {}, "op": "delete_all"}
        )


# ---- JS-reachability (SECURITY §3) --------------------------------------


def test_only_bridge_send_is_public_callable() -> None:
    """pywebview exposes public methods of js_api to JS.

    Anything reachable here is a genuine attack surface; assert exactly one
    public callable so a future contributor cannot widen the surface by
    accident (e.g. by adding another bare ``def foo(self, ...)``).
    """
    public_callables = {
        name
        for name in dir(BridgeFacade)
        if not name.startswith("_") and callable(getattr(BridgeFacade, name, None))
    }
    assert public_callables == {"bridge_send"}
