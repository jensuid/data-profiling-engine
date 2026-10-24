"""Bridge package — typed JS↔Python facade (CONTRACTS §5; SECURITY §3).

M0-T2/T3 spike: :class:`BridgeFacade` with ``echo`` plus the worker-job trio
backed by the spawn-method worker in :mod:`bridge.worker`. M5-T2 lands the real
``profile.start`` / ``profile.get_status`` / ``profile.cancel`` trio over the
store-backed job manager; the spike commands stay available only behind
``BridgeFacade(enable_spike_commands=True)``. The app host binds its user-data
root with :func:`configure_runtime` before serving the bridge, and may enable the
optional §5.5 progress push with :func:`configure_status_push` +
:func:`window_push_sink` once it has a window (polling stays authoritative).
"""
from .envelope import (
    BridgeErrorObject,
    BridgeRequest,
    BridgeResponse,
    EchoPayload,
    JobIdPayload,
    ProfileStartPayload,
    WorkerEchoJobPayload,
)
from .facade import COMMANDS, PAYLOAD_CAP_BYTES, SPIKE_COMMANDS, BridgeFacade
from .runtime import configure_runtime, configure_status_push, window_push_sink

__all__ = [
    "BridgeErrorObject",
    "BridgeFacade",
    "BridgeRequest",
    "BridgeResponse",
    "COMMANDS",
    "SPIKE_COMMANDS",
    "EchoPayload",
    "JobIdPayload",
    "ProfileStartPayload",
    "PAYLOAD_CAP_BYTES",
    "WorkerEchoJobPayload",
    "configure_runtime",
    "configure_status_push",
    "window_push_sink",
]
