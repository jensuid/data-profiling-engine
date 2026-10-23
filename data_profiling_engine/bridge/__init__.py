"""Bridge package — typed JS↔Python facade (CONTRACTS §5; SECURITY §3).

M0-T2 spike: exposes :class:`BridgeFacade` with a single allow-listed
``echo`` command. Real MVP commands land in M5-T1.
"""
from .envelope import (
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
    EchoPayload,
)
from .facade import COMMANDS, PAYLOAD_CAP_BYTES, BridgeFacade

__all__ = [
    "BridgeErrorObject",
    "BridgeFacade",
    "BridgeRequest",
    "BridgeResponse",
    "COMMANDS",
    "EchoPayload",
    "PAYLOAD_CAP_BYTES",
]
