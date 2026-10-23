"""Bridge-layer error codes (CONTRACTS §5 / §8, spike subset for M0-T2).

The full registry lives in CONTRACTS §8 and is formalised as a Python module
in M1-T6. For this spike we only need the codes the facade actually emits, so
they are kept as plain string constants here. See STATE.md GOTCHA for the
extra code we introduce (``BRIDGE_REQUEST_INVALID``) that M1-T6 must promote.
"""
from __future__ import annotations

# CONTRACTS §8 (existing).
COMMAND_UNKNOWN = "BRIDGE_COMMAND_UNKNOWN"
PAYLOAD_TOO_LARGE = "BRIDGE_PAYLOAD_TOO_LARGE"
SOURCE_NOT_ALLOWED = "SOURCE_NOT_ALLOWED"
WORKER_CRASHED = "WORKER_CRASHED"
JOB_TIMEOUT = "JOB_TIMEOUT"

# Extensible per CONTRACTS §8 — spike introduces this for envelope/payload
# schema validation failures. M1-T6 folds it into the registry module + doc.
REQUEST_INVALID = "BRIDGE_REQUEST_INVALID"

# Spike (M0-T3): polled status / cancel for an id the manager does not know.
# Same promotion path as REQUEST_INVALID — M1-T6 must fold it into §8.
JOB_NOT_FOUND = "JOB_NOT_FOUND"
