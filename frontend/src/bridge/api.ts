/**
 * M6-T1 — typed bridge client (CONTRACTS §5.1/§5.3; SECURITY §3).
 *
 * The WebView reaches Python through exactly ONE method —
 * ``window.pywebview.api.bridge_send`` — with the frozen §5.3 envelope models.
 * This module is the only place UI code touches that surface: it builds request
 * envelopes, generates the §5.4 ``request_id``, unwraps the discriminated
 * response (``ok``→result, ``accepted``→job_id, ``error``→typed BridgeError),
 * and exposes the optional §5.5 push sink. UI code never branches on the
 * envelope; it calls :func:`bridgeSend` and catches :class:`BridgeError`.
 */
import type { JsonValue } from "../types/contracts";
import type { DialogOpenFileResult } from "../types/contracts";

// --- §5.3 envelope wire types (mirror of the frozen models in schemas/models.py;
// extra fields are forbidden by the Python validator, so a request here that
// carries a key the schema does not know fails as BRIDGE_REQUEST_INVALID). ---

export interface BridgeRequest<P extends object = { [key: string]: JsonValue }> {
  command: string;
  request_id: string;
  payload: P;
}

export interface BridgeErrorObject {
  code: string; // §8 registry code (open, extensible)
  message: string;
  retryable: boolean;
}

export interface BridgeResponse {
  request_id: string;
  status: "ok" | "accepted" | "error";
  result?: { [key: string]: JsonValue };
  job_id?: string;
  error?: BridgeErrorObject;
}

/**
 * Typed rejection of a bridge call. Server-side errors carry the §8 code +
 * retryability verbatim; the transport-failure codes below (`BRIDGE_UNAVAILABLE`,
 * `BRIDGE_UNEXPECTED_ACCEPTED`, `BRIDGE_UNEXPECTED_RESPONSE`) are CLIENT-LOCAL
 * sentinels for a call that never completed a §5.3 round-trip — they never
 * cross the bridge and are deliberately not registry rows (§8 governs errors
 * produced by the Python side).
 */
export class BridgeError extends Error {
  readonly code: string;
  readonly retryable: boolean;

  constructor(err: BridgeErrorObject) {
    super(err.message);
    this.name = "BridgeError";
    this.code = err.code;
    this.retryable = err.retryable;
  }
}

/** Outcome of an ``accepted``-style (async) command: the §5.3 job id. */
export interface AcceptedOutcome {
  jobId: string;
}

// pywebview injects ``window.pywebview`` into the host page; outside the
// desktop shell (plain-browser dev) it is absent and every call must fail
// with a clear message instead of a TypeError deep in a promise chain.
declare global {
  interface Window {
    pywebview?: { api?: Record<string, unknown> };
    /** §5.5 host→JS push slot — declared once here, registered by the UI. */
    __dpeJobStatus?: (status: JsonValue) => void;
  }
}

export function bridgeAvailable(): boolean {
  return typeof window.pywebview?.api?.bridge_send === "function";
}

/**
 * §5.2 ``dialog.open_file`` (ADR-022): ask the host to show the OS-native open
 * dialog (CSV/Parquet filter fixed host-side, ADR-011). Resolves with the
 * picked path, or ``null`` when the user cancels. The pick only FILLS the path
 * field — reading the file still goes through the allow-listed read commands.
 */
export async function bridgeSelectSourceFile(): Promise<string | null> {
  const result = await bridgeSend<Record<string, never>, DialogOpenFileResult>(
    "dialog.open_file",
    {},
  );
  return result.path ?? null;
}

function newRequestId(): string {
  // §5.4 only requires uniqueness per request; a monotonic counter + random
  // suffix is enough for correlation and stays well under the 128-char cap.
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Result of a synchronous §5.2 read command (status === "ok"). */
export async function bridgeSend<P extends object, R = JsonValue>(
  command: string,
  payload: P,
): Promise<R> {
  const api = window.pywebview?.api as
    | { bridge_send?: (req: BridgeRequest<P>) => Promise<BridgeResponse> }
    | undefined;
  if (typeof api?.bridge_send !== "function") {
    throw new BridgeError({
      code: "BRIDGE_UNAVAILABLE",
      message:
        "The desktop bridge is not available — run inside the Data Profiling Engine app shell.",
      retryable: false,
    });
  }
  const resp = await api.bridge_send({
    command,
    request_id: newRequestId(),
    payload,
  });
  if (resp.status === "error" && resp.error) {
    throw new BridgeError(resp.error);
  }
  if (resp.status === "accepted" && resp.job_id) {
    // Async commands answer accepted+job_id (§5.3); the caller polls.
    throw new BridgeError({
      code: "BRIDGE_UNEXPECTED_ACCEPTED",
      message: `command ${command} returned a job id (${resp.job_id}); use bridgeStartJob`,
      retryable: false,
    });
  }
  return (resp.result ?? {}) as unknown as R;
}

/** Start an async command (``profile.start``): resolves with the §5.3 job id. */
export async function bridgeStartJob<P extends object>(
  command: string,
  payload: P,
): Promise<AcceptedOutcome> {
  const api = window.pywebview?.api as
    | { bridge_send?: (req: BridgeRequest<P>) => Promise<BridgeResponse> }
    | undefined;
  if (typeof api?.bridge_send !== "function") {
    throw new BridgeError({
      code: "BRIDGE_UNAVAILABLE",
      message: "The desktop bridge is not available — run inside the app shell.",
      retryable: false,
    });
  }
  const resp = await api.bridge_send({
    command,
    request_id: newRequestId(),
    payload,
  });
  if (resp.status === "error" && resp.error) {
    throw new BridgeError(resp.error);
  }
  if (resp.status !== "accepted" || !resp.job_id) {
    throw new BridgeError({
      code: "BRIDGE_UNEXPECTED_RESPONSE",
      message: `command ${command} did not return status=accepted with a job_id`,
      retryable: false,
    });
  }
  return { jobId: resp.job_id };
}

/**
 * §5.5 optional push: the host calls ``window.__dpeJobStatus(status)`` with the
 * SAME §5.3 status object the poll returns. Register a handler to receive
 * hints; polling stays authoritative — a push may be coalesced or dropped, so
 * a missing push must never be treated as a lost state.
 */
export function onPushedJobStatus(
  handler: (status: JsonValue) => void,
): () => void {
  const previous = window.__dpeJobStatus ?? null;
  window.__dpeJobStatus = (status: JsonValue) => {
    try {
      handler(status);
    } finally {
      previous?.(status);
    }
  };
  return () => {
    window.__dpeJobStatus = previous ?? undefined;
  };
}
