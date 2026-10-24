"""CSV issue export (M4-T3; ENGINE §10, SECURITY §4).

One flat, tracker-importable row per **failing/warning item** — the ENGINE §10
bullet pins the column list verbatim:

    finding_id, rule_id, column, status, severity, observed,
    threshold, affected_records, evidence_ref

Built **only** from the structured :class:`ProfileBundle` — no statistic is
re-run here (§10 first bullet), the same input discipline the JSON writer
(:mod:`reporting`) and the HTML report (:mod:`reporting.html_report`) follow.

Row source (STATE M4-T3 NEXT): a row exists for each *finding whose originating
quality check FAILED or WARNED*. The engine derives a rule finding from exactly
those checks (:func:`findings.detectors.detect_from_quality_checks` skips PASS
and ERROR — FAIL ≠ ERROR, AGENTS §3), and every such finding carries the
originating rule as a ``rule:<rule_id>`` evidence ref (a profile-extreme
finding has none, so it is out of scope for a tracker issue). We therefore walk
findings, resolve each ``rule:`` ref back to its :class:`QualityCheckResult`,
and emit the join — one row per (finding, failing/warning check) pair (a merged
finding that absorbed several rules on the same column yields one row per rule).

Contract obligations carried:

* **ADR-015 method honesty**: the ``observed`` cell always names the measuring
  ``method`` (and, for ``sampled``/``approximate``, the seed/algorithm), so a
  bounded estimate never reads as a population truth; ``not_computed`` prints
  its reason, never a bare blank that could be misread as 0.
* **FAIL ≠ ERROR**: an ERROR check (un-executable) is *not* exported as a data
  problem here — it surfaces as a run error, not a tracker issue.
* **SECURITY §4 masking**: an issue row carries only rule identifiers, column
  *names*, aggregate metrics and config-declared thresholds — never a raw
  source cell value. There is nothing PII to redact by construction, which is
  why the CSV satisfies "PII-like columns masked in default HTML/CSV export"
  (SECURITY §8) without echoing any sample value.

Determinism follows the JSON/HTML writers (TESTING §3.3): same bundle ⇒ byte-
identical text. Rows keep the bundle's finding order (the pipeline already
sorts findings by priority/fingerprint, so ``F-001`` leads), fields use a fixed
``\\n`` terminator, and the ``csv`` module quotes any cell containing a
delimiter so a comma-bearing threshold stays a single field.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_profiling_engine.orchestration.rollup import ProfileBundle
from data_profiling_engine.schemas.models import (
    CheckStatus,
    Finding,
    MetricMethod,
    QualityCheckResult,
)

#: Header, verbatim from ENGINE §10 "CSV issue export" — the exact cross-boundary
#: shape a tracker imports. Do not reorder or rename; the contract test pins it.
ISSUE_CSV_COLUMNS: Tuple[str, ...] = (
    "finding_id",
    "rule_id",
    "column",
    "status",
    "severity",
    "observed",
    "threshold",
    "affected_records",
    "evidence_ref",
)

#: Only these check outcomes are actionable "issue" rows (ENGINE §10 "failing/
#: warning item"); PASS is a clean check and ERROR is un-executable, not a
#: proven data problem (AGENTS §3), so neither is exported.
_EXPORTABLE_STATUSES = (CheckStatus.FAIL, CheckStatus.WARN)


def _fmt_scalar(value: Any) -> str:
    """Stable text for a contract scalar in a CSV cell (numbers/bools/null via
    JSON so they are byte-deterministic; strings verbatim). ``csv`` quotes the
    result if it holds a delimiter."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _observed_cell(observed: Optional[object]) -> str:
    """Render a ``MetricValue`` with its ADR-015 ``method`` tag so the tracker
    never mistakes a sampled/approximate figure for an exact one."""
    if observed is None:
        return ""
    value = getattr(observed, "value", None)
    prov = getattr(observed, "provenance", None)
    method = getattr(prov, "method", None) if prov is not None else None
    method = getattr(method, "value", method)  # enum-or-str (use_enum_values)
    if method == MetricMethod.NOT_COMPUTED.value:
        reason = getattr(prov, "reason", "") or ""
        return f"not_computed ({reason})" if reason else "not_computed"
    bits: List[str] = []
    if method:
        bits.append(str(method))
    params = getattr(prov, "method_params", None) or {}
    if isinstance(params, dict):
        for key in ("seed", "algorithm", "strategy", "sample_size"):
            if key in params:
                bits.append(f"{key}={params[key]}")
    text = _fmt_scalar(value)
    return f"{text} ({'; '.join(bits)})" if bits else text


def _threshold_cell(threshold: Optional[object]) -> str:
    """``operator [value [.. upper]]`` — the rule's bound as authored in config;
    a no-operand operator (``unique`` / ``not_null``) renders as just itself."""
    if threshold is None:
        return ""
    operator = _fmt_scalar(getattr(threshold, "operator", None))
    value = getattr(threshold, "value", None)
    upper = getattr(threshold, "upper", None)
    if value is not None and upper is not None:
        return f"{operator} {_fmt_scalar(value)}..{_fmt_scalar(upper)}"
    if value is not None:
        return f"{operator} {_fmt_scalar(value)}"
    return operator


def _rule_refs(finding: Finding) -> List[str]:
    """The rule id(s) a finding cites, taken from its ``rule:<id>`` evidence
    refs (the detector's own ref shape — see STATE GOTCHA: refs are
    ``rule:<rule_id>`` / ``metric:<column>.<measure>``, not ``measure:column``).
    Order-preserving and de-duplicated so merged findings stay deterministic."""
    seen: List[str] = []
    for ref in finding.evidence_refs or []:
        if isinstance(ref, str) and ref.startswith("rule:"):
            rule_id = ref[len("rule:"):]
            if rule_id and rule_id not in seen:
                seen.append(rule_id)
    return seen


def _build_rows(bundle: ProfileBundle) -> List[List[str]]:
    """Join each finding to its originating FAIL/WARN check, one row per pair.

    The check lookup is keyed on ``rule_id``; a ``rule:`` ref whose check is
    missing or is not ``FAIL``/``WARN`` is skipped so an ERROR/PASS never leaks
    into the tracker feed (FAIL ≠ ERROR)."""
    checks_by_rule: Dict[str, QualityCheckResult] = {}
    for check in bundle.checks:
        rule_id = getattr(check, "rule_id", None)
        if rule_id is not None:
            checks_by_rule.setdefault(rule_id, check)

    rows: List[List[str]] = []
    for finding in bundle.findings:
        scope = finding.scope
        for rule_id in _rule_refs(finding):
            check = checks_by_rule.get(rule_id)
            if check is None:
                continue
            status = getattr(check, "status", None)
            status = getattr(status, "value", status)
            if status not in _EXPORTABLE_STATUSES:
                continue  # PASS/ERROR never became a rule finding; guard anyway
            column = getattr(check, "column", None)
            if column is None:
                column = getattr(scope, "column", None)
            affected = getattr(check, "failing_record_count", None)
            if affected is None:
                affected = getattr(scope, "affected_records", None)
            severity = getattr(finding, "severity", None)
            severity = getattr(severity, "value", severity)
            rows.append([
                str(finding.finding_id),
                str(rule_id),
                _fmt_scalar(column),
                str(status),
                _fmt_scalar(severity),
                _observed_cell(getattr(check, "observed", None)),
                _threshold_cell(getattr(check, "threshold", None)),
                "" if affected is None else str(affected),
                "; ".join(str(ref) for ref in (finding.evidence_refs or [])),
            ])
    return rows


def render_issue_csv(bundle: ProfileBundle) -> str:
    """Render the §10 issue export for one run as CSV text (header + one row
    per failing/warning finding→check pair). Deterministic: the same bundle
    yields byte-identical output. A run with no failing/warning items still
    emits the header so a tracker import has a stable schema."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(list(ISSUE_CSV_COLUMNS))
    for row in _build_rows(bundle):
        writer.writerow(row)
    return buffer.getvalue()


def write_issue_csv(bundle: ProfileBundle, out_path: "Path | str") -> Path:
    """Render + write the issue CSV to ``out_path`` (the CLI ``--csv`` target).
    Mirrors :func:`reporting.write_profile_json` / ``html_report.write_html_report``:
    creates parents, UTF-8, returns the written path."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_issue_csv(bundle), encoding="utf-8")
    return out


def emit_issue_csv_artifact(bundle: ProfileBundle, store: "Any") -> str:
    """Write the issue CSV through an :class:`~data_profiling_engine.storage.artifact_store.ArtifactStore`
    under the canonical ``issue_csv`` kind (``artifacts/<run_id>/issues.csv`` —
    the name already registered in ``DEFAULT_FILENAMES`` / CONTRACTS §2
    ``result_refs``) and return the store-relative ref for ``run_artifacts``.

    Retention comes from the store's kind policy (``issue_csv`` ⇒ RESULT,
    survive-upgrade / explicit-delete-only, SECURITY §2) — inherited for free,
    exactly as the HTML report does."""
    content = render_issue_csv(bundle)
    return store.write_text(bundle.run.run_id, "issue_csv", content)


__all__ = [
    "ISSUE_CSV_COLUMNS",
    "render_issue_csv",
    "write_issue_csv",
    "emit_issue_csv_artifact",
]
