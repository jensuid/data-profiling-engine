/**
 * M6-T3 — Run Progress screen (CONTRACTS §5.5, §6).
 *
 * Renders the live state of the job accepted by ``profile.start`` on the Run
 * Configuration screen (handed over through the shared in-memory store; after
 * an app restart the SQLite store + job manager §6.3 are the recovery path,
 * never this WebView). Polling ``profile.get_status`` is AUTHORITATIVE (§5.5);
 * the optional host→JS push (window.__dpeJobStatus) is consumed only as a
 * hint and merged through the same apply() path, so both transports feed one
 * state machine. A push may be coalesced or dropped — a missing push is never
 * a lost state. Cancel sets CANCEL_REQUESTED (§6.5), which is NOT a guarantee
 * the work stopped: the button locks and only a polled terminal state
 * confirms cancellation. Terminal states stop the poll.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BridgeError, bridgeAvailable, bridgeSend, onPushedJobStatus } from "../bridge/api";
import { useStartedJob } from "../state/selection";
import type { JobState, JobStatus, JsonValue } from "../types/contracts";
import { JobStateValues } from "../types/contracts";

/** Poll cadence for profile.get_status (§5.5). Cheap status reads; the push
 * hint only accelerates the render between polls, so 700 ms keeps the bar
 * responsive without turning the bridge into a busy loop. */
const POLL_MS = 700;

const TERMINAL_STATES: ReadonlySet<JobState> = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
]);

/** §6.1 monotonic rank — the only allowed state "backwards" move is
 * CANCEL_REQUESTED → RUNNING (worker could not stop; §6.5). Anything else
 * older (e.g. a late QUEUED poll response racing a newer pushed RUNNING) is
 * ignored so the bar never regresses. */
function stateRank(s: JobState | null): number {
  if (s === null) return -1;
  if (s === "QUEUED") return 0;
  if (s === "RUNNING") return 1;
  if (s === "CANCEL_REQUESTED") return 2;
  return 3; // terminal
}

function isJobState(v: unknown): v is JobState {
  return typeof v === "string" && (JobStateValues as readonly string[]).includes(v);
}

/** Defensive parse of a bridge/push object — a pushed hint arrives as
 * JsonValue and must never crash the screen on a shape surprise (§5.5: the
 * push is optional convenience; the poll would still be correct without it). */
function asJobStatus(raw: unknown): JobStatus | null {
  if (typeof raw !== "object" || raw === null) return null;
  const o = raw as Record<string, JsonValue>;
  if (typeof o.job_id !== "string" || !isJobState(o.status)) return null;
  const p = o.progress;
  if (
    typeof p !== "object" ||
    p === null ||
    typeof (p as Record<string, JsonValue>).completed_steps !== "number" ||
    typeof (p as Record<string, JsonValue>).total_steps !== "number"
  ) {
    return null;
  }
  const prog = p as Record<string, JsonValue>;
  const out: JobStatus = {
    job_id: o.job_id,
    status: o.status,
    progress: {
      completed_steps: prog.completed_steps as number,
      total_steps: prog.total_steps as number,
      ...(typeof prog.current_step === "string" ? { current_step: prog.current_step } : {}),
    },
  };
  if (typeof o.error_code === "string") out.error_code = o.error_code;
  if (typeof o.result === "object" && o.result !== null) out.result = o.result as { [key: string]: JsonValue };
  return out;
}

function pct(progress: JobStatus["progress"]): number {
  if (progress.total_steps <= 0) return 0;
  const r = progress.completed_steps / progress.total_steps;
  return Math.max(0, Math.min(100, Math.round(r * 100)));
}

function asCount(v: JsonValue | undefined): number | null {
  return typeof v === "number" ? v : null;
}

/** Bounded §5.4 success summary the worker attaches to the SUCCEEDED status
 * (profile_job._bounded_result): ids + counts, never the full document. */
function ResultSummary({ result }: { result: { [key: string]: JsonValue } }) {
  const runId = typeof result.run_id === "string" ? result.run_id : null;
  const rowStr = asCount(result.row_count);
  const refs =
    typeof result.result_refs === "object" && result.result_refs !== null
      ? (result.result_refs as Record<string, JsonValue>)
      : {};
  const refNames = Object.keys(refs).sort();
  return (
    <div className="panel">
      <h2>Run Summary</h2>
      <dl className="meta-grid">
        {runId && (
          <>
            <dt>Run ID</dt>
            <dd className="mono">{runId}</dd>
          </>
        )}
        {rowStr !== null && (
          <>
            <dt>Rows</dt>
            <dd>{rowStr.toLocaleString()}</dd>
          </>
        )}
        <dt>Columns</dt>
        <dd>{asCount(result.column_count) ?? "—"}</dd>
        <dt>Findings</dt>
        <dd>{asCount(result.finding_count) ?? "—"}</dd>
        <dt>Quality checks</dt>
        <dd>{asCount(result.check_count) ?? "—"}</dd>
        <dt>Recommendations</dt>
        <dd>{asCount(result.recommendation_count) ?? "—"}</dd>
        <dt>Task errors</dt>
        <dd>{asCount(result.error_count) ?? "—"}</dd>
      </dl>
      {refNames.length > 0 && (
        <p className="hint mono">artifacts: {refNames.join(", ")}</p>
      )}
      <p className="hint">
        The report view arrives with M6-T4 — open the HTML report via{" "}
        <code>artifact.open</code> meanwhile, or re-run from the Run screen.
      </p>
    </div>
  );
}

export default function RunProgress() {
  const job = useStartedJob();
  const hasBridge = bridgeAvailable();

  const [status, setStatus] = useState<JobStatus | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);

  // Rank guard lives in a ref so the poll tick and the push handler share ONE
  // monotonic gate without re-subscribing either effect (§5.5: both transports
  // feed the same state machine).
  const rankRef = useRef(-1);
  const apply = useCallback((next: JobStatus) => {
    const rank = stateRank(next.status);
    if (rank < rankRef.current) return;
    rankRef.current = rank;
    setStatus(next);
    setPollError(null);
  }, []);

  // Reset the machine when a new job is handed over (Start again on Run).
  useEffect(() => {
    rankRef.current = -1;
    setStatus(null);
    setPollError(null);
    setCancelBusy(false);
    setCancelError(null);
  }, [job?.jobId]);

  // §5.5 authoritative poll — runs while the state is non-terminal.
  useEffect(() => {
    if (!job || !hasBridge) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      try {
        const res = await bridgeSend<{ job_id: string }, JobStatus>(
          "profile.get_status",
          { job_id: job!.jobId },
        );
        const parsed = asJobStatus(res);
        if (!cancelled && parsed) apply(parsed);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof BridgeError && e.code === "JOB_NOT_FOUND") {
          // §8: not retryable — the manager no longer knows this job (e.g.
          // host restart with an in-memory-only handover). Stop, explain.
          setPollError(`JOB_NOT_FOUND — ${e.message}. The job manager no longer knows ${job!.jobId}; start a new run on the Run screen.`);
          return;
        }
        // Transient/transport failure: keep polling, show a soft banner.
        setPollError(e instanceof BridgeError ? `${e.code}: ${e.message}` : String(e));
      }
      if (!cancelled) {
        if (rankRef.current < 3) timer = setTimeout(tick, POLL_MS);
      }
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // `apply` is stable; the machine restarts per job id only.
  }, [job?.jobId, hasBridge, apply]);

  // §5.5 optional push hints — same object shape, merged through apply().
  useEffect(() => {
    if (!job) return;
    return onPushedJobStatus((raw) => {
      const parsed = asJobStatus(raw);
      if (parsed && parsed.job_id === job.jobId) apply(parsed);
    });
  }, [job?.jobId, apply]);

  async function handleCancel() {
    if (!job || !status) return;
    setCancelBusy(true);
    setCancelError(null);
    try {
      await bridgeSend("profile.cancel", { job_id: job.jobId });
      // Optimistic CANCEL_REQUESTED; the poll confirms or corrects (§6.5).
      apply({ ...status, status: "CANCEL_REQUESTED" });
    } catch (e) {
      setCancelError(e instanceof BridgeError ? `${e.code}: ${e.message}` : String(e));
    } finally {
      setCancelBusy(false);
    }
  }

  if (!job) {
    return (
      <section className="run-progress">
        <h1>Run Progress</h1>
        <div className="panel">
          <p>No profile job has been started in this session.</p>
          <p className="hint">
            Configure and start a run on the <Link to="/run">Run</Link> screen —
            its accepted job appears here. After an app restart the job manager
            recovers critical state from the metadata store (§6.3); this screen
            only tracks the session's latest job.
          </p>
        </div>
      </section>
    );
  }

  const st = status;
  const state: JobState | null = st?.status ?? null;
  const terminal = state !== null && TERMINAL_STATES.has(state);
  const percent = st ? pct(st.progress) : 0;
  const result = st?.result ?? null;

  return (
    <section className="run-progress">
      <h1>Run Progress</h1>
      {!hasBridge && (
        <div className="banner-warn">
          Desktop bridge not detected — status cannot be polled. Run inside the
          app shell.
        </div>
      )}

      <div className="panel">
        <h2>Job</h2>
        <dl className="meta-grid">
          <dt>Job ID</dt>
          <dd className="mono">{job.jobId}</dd>
          <dt>Dataset</dt>
          <dd className="mono">{job.datasetId}</dd>
          <dt>Source</dt>
          <dd className="mono">{job.sourcePath}</dd>
          <dt>Started</dt>
          <dd>{job.startedAt}</dd>
        </dl>
      </div>

      <div className="panel">
        <h2>Status</h2>
        <div className="progress-head">
          <span className={`state-badge state-${state ?? "idle"}`}>
            {state ?? (hasBridge ? "connecting…" : "idle")}
          </span>
          {st && (
            <span className="progress-steps">
              {st.progress.completed_steps}/{st.progress.total_steps} steps · {percent}%
            </span>
          )}
        </div>
        <div className="progress-track" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div className={`progress-fill ${terminal ? "progress-" + (state === "SUCCEEDED" ? "ok" : "bad") : ""}`} style={{ width: `${percent}%` }} />
        </div>
        {st?.progress.current_step && (
          <p className="progress-step mono">{st.progress.current_step}</p>
        )}
        {state === null && !pollError && (
          <p className="hint">Waiting for the first polled status (§5.5 — the poll is authoritative; pushes only accelerate).</p>
        )}
        {pollError && <div className="error-box">{pollError}</div>}
        {state === "QUEUED" && <p className="hint">Registered, worker not yet started (§6.2).</p>}
        {state === "RUNNING" && <p className="hint">Worker executing the plan (§6.2).</p>}
        {state === "CANCEL_REQUESTED" && (
          <p className="hint">
            Cancellation requested — <b>not a guarantee the work stopped</b>{" "}
            (§6.5). Waiting for the polled terminal state to confirm.
          </p>
        )}
      </div>

      {state === "SUCCEEDED" && result && <ResultSummary result={result} />}

      {state === "FAILED" && (
        <div className="panel">
          <h2>Failed</h2>
          <div className="error-box">
            {st?.error_code
              ? `${st.error_code} — see the §8 registry; partial results may exist (§6.4).`
              : "The job failed; partial results may exist (§6.4)."}
          </div>
        </div>
      )}

      {state === "CANCELLED" && (
        <div className="panel">
          <h2>Cancelled</h2>
          <p className="hint">
            Worker confirmed the stop; partial artifacts are marked (§6.2).
          </p>
        </div>
      )}

      <div className="panel">
        <h2>Actions</h2>
        <div className="field-row">
          <button
            className="btn"
            onClick={handleCancel}
            disabled={
              !hasBridge ||
              terminal ||
              state === null ||
              state === "CANCEL_REQUESTED" ||
              cancelBusy
            }
          >
            {cancelBusy ? "Cancelling…" : "Cancel Run"}
          </button>
          <Link className="btn" to="/run">
            {terminal ? "New Run" : "Back to Run"}
          </Link>
        </div>
        {cancelError && <div className="error-box">{cancelError}</div>}
        <p className="hint">
          Cancel sends <code>profile.cancel</code> → CANCEL_REQUESTED; only a polled
          CANCELLED confirms the worker actually stopped (§6.5). A per-task
          error never hides the rest of the report (§6.4).
        </p>
      </div>
    </section>
  );
}
