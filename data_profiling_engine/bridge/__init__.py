"""Bridge package — typed JS↔Python facade (CONTRACTS §5; SECURITY §3).

M0-T2/T3 spike: :class:`BridgeFacade` with ``echo`` plus the worker-job trio
backed by the spawn-method worker in :mod:`bridge.worker`. Real MVP commands
land in M5-T1.
"""
from .envelope import (
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
    EchoPayload,
    JobIdPayload,
    WorkerEchoJobPayload,
)
from .facade import COMMANDS, PAYLOAD_CAP_BYTES, BridgeFacade

__all__ = [
    "BridgeErrorObject",
    "BridgeFacade",
    "BridgeRequest",
    "BridgeResponse",
    "COMMANDS",
    "EchoPayload",
    "JobIdPayload",
    "PAYLOAD_CAP_BYTES",
    "WorkerEchoJobPayload",
]
