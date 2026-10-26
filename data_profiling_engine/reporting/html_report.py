"""Self-contained HTML report writer (M4-T2; ENGINE §10, SECURITY §4).

One single-file, offline-shareable HTML document built **only** from the
structured :class:`ProfileBundle` — no statistic is re-run here (§10 first
bullet). Obligations carried:

* **Plotly.js inlined** from the vendored ``assets/plotly.min.js`` so charts
  render with zero network access (no CDN tag — ADR-005 forbids any HTTP
  surface, and a shared report must open on a plane).
* **Every number wears its ``method`` badge** (ADR-015): the ``exact`` /
  ``approximate`` / ``sampled`` / ``not_computed`` label from the metric's
  own ``Provenance`` is rendered beside it, with the seed / algorithm /
  reason in the tooltip — a sampled value is never presentable as exact.
* **PII-like columns are masked by default** (SECURITY §4): discovery's
  pattern refinement lands ``email`` / ``phone`` in the open
  ``logical_type`` vocabulary, and for those columns every sample value the
  report shows (categorical bars/tables) is rendered masked
  (``j***@acme…``-style). Full values live only in the JSON artifact.
* **Absent is not null** (AGENTS §3): a ``not_computed`` metric renders its
  reason, never a bare ``—`` that could be misread as 0.

Chart selection is data-type-aware (§10 last bullet): histogram for numeric
columns that computed one, an IQR box from the quantile stats, a frequency
bar for categorical columns, a volume line for temporal columns, plus one
dataset-level missingness bar — not every chart for every column.

Determinism follows the JSON writer's rule (TESTING §3.3): the same bundle
plus the same engine assets produces byte-identical HTML; the only
wall-clock content is what the bundle itself already carries.
"""
from __future__ import annotations

import functools
import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from data_profiling_engine.orchestration.rollup import ProfileBundle
from data_profiling_engine.reporting import result_document
from data_profiling_engine.schemas.models import MetricMethod

#: Vendored Plotly.js bundle (extract of the official ``plotly`` wheel,
#: package_data/plotly.min.js). Committed under ``reporting/assets`` so the
#: frozen app (ADR-009) carries it as package data and no build step needs
#: the network.
PLOTLY_ASSET = Path(__file__).resolve().parent / "assets" / "plotly.min.js"

#: ``ColumnProfile.logical_type`` values the discovery pattern refinement
#: (ENGINE §4.1) assigns to probable-PII string columns. SECURITY §4 names
#: email / phone / national-id-like; the MVP vocabulary has the first two.
PII_LOGICAL_TYPES = frozenset({"email", "phone"})

#: Badge CSS class per ADR-015 method, so the reader can eyeball at a glance
#: which numbers are population truths and which are bounded estimates.
_METHOD_CLASSES = {
    MetricMethod.EXACT.value: "badge-exact",
    MetricMethod.APPROXIMATE.value: "badge-approx",
    MetricMethod.SAMPLED.value: "badge-sampled",
    MetricMethod.NOT_COMPUTED.value: "badge-notcomputed",
}


@functools.lru_cache(maxsize=1)
def _plotly_source() -> str:
    """The vendored Plotly.js text, read once per process and inlined verbatim
    into every report (ENGINE §10 "Plotly.js inlined"; no CDN tag, no
    network — ADR-005)."""
    return PLOTLY_ASSET.read_text(encoding="utf-8")


def _esc(value: Any) -> str:
    """HTML-escape any contract scalar for safe interpolation."""
    return html.escape("" if value is None else str(value), quote=True)


def _js_json(obj: Any) -> str:
    """Embed a JSON payload inside a <script> tag: stable key order and the
    ``</`` sequence escaped so a string value can never close the tag."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).replace("</", "<\\/")


def _fmt_num(value: Any) -> str:
    """Compact display for a numeric metric value (rates keep 4 significant
    digits; ints stay exact) — display formatting only, never rounding that
    enters a contract."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:.6g}"
    return _esc(value)


def _badge(provenance: Optional[Dict[str, Any]]) -> str:
    """One ADR-015 method badge for a provenance dict (canonical-JSON form).
    The tooltip carries the deciding parameters — sampled the seed,
    approximate the algorithm, not_computed the reason."""
    if not provenance:
        return ""
    method = str(provenance.get("method", ""))
    cls = _METHOD_CLASSES.get(method, "badge-unknown")
    parts = [method]
    params = provenance.get("method_params") or {}
    if isinstance(params, dict):
        for key in ("seed", "algorithm", "strategy", "sample_size"):
            if key in params:
                parts.append(f"{key}={params[key]}")
    if provenance.get("reason"):
        parts.append(f"reason: {provenance['reason']}")
    title = _esc("; ".join(parts))
    return f'<span class="badge {cls}" title="{title}">{_esc(method)}</span>'


def _metric_value_doc(mv: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """(display, badge_html) for one canonical-JSON MetricValue block."""
    if not isinstance(mv, dict):
        return "—", ""
    prov = mv.get("provenance") or {}
    badge = _badge(prov)
    if prov.get("method") == MetricMethod.NOT_COMPUTED.value:
        reason = _esc(prov.get("reason") or "not computed")
        return f'<span class="not-computed">not_computed<small> ({reason})</small></span>', badge
    value = _fmt_num(mv.get("value"))
    if mv.get("denominator") is not None:
        value += f'<small> / {_fmt_num(mv["denominator"])}</small>'
    return value, badge


def _mv_cell(mv: Optional[Dict[str, Any]]) -> str:
    """A table cell rendering value + method badge together (ADR-015)."""
    display, badge = _metric_value_doc(mv)
    return f'<td class="num">{display} {badge}</td>'


# ---------------------------------------------------------------------------
# PII masking (SECURITY §4 — default in HTML/CSV, full values only in JSON)
# ---------------------------------------------------------------------------

def is_pii_column(profile: Dict[str, Any]) -> bool:
    """Whether one canonical-JSON ColumnProfile names a probable-PII column
    (discovery pattern refinement tagged its logical_type email/phone)."""
    return str(profile.get("logical_type", "")).lower() in PII_LOGICAL_TYPES


def mask_value(value: str, logical_type: str) -> str:
    """Render a single sample value masked: keep just enough shape for the
    reader to recognise the column, never the value (SECURITY §4
    ``j***@…`` / partial digits)."""
    text = str(value)
    if logical_type == "email" and "@" in text:
        local, _, domain = text.partition("@")
        first = local[:1] if local else ""
        dom_first = domain.split(".", 1)[0][:1] if domain else ""
        return f"{first}***@{dom_first}…"
    digits = re.sub(r"\D", "", text)
    if logical_type == "phone" or (digits and len(digits) >= 6):
        keep = text[:2] if not text.startswith("+") else text[:3]
        return f"{keep} •••• {digits[-2:]}" if len(digits) >= 4 else f"{keep} ••••"
    return f"{text[:1]}***" if text else "—"


# ---------------------------------------------------------------------------
# Chart construction (ENGINE §10: data-type-aware, from stored metrics only)
# ---------------------------------------------------------------------------

def _box_spec(profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """§10 \"outlier box\": an IQR box drawn entirely from the stored quantile
    MetricValues (min/p25/median/p75/max) — never recomputed here."""
    name = str(profile.get("column", ""))
    dist = profile.get("distribution") or {}
    stats = profile.get("stats") or {}
    q1, q3, med = stats.get("p25"), stats.get("p75"), stats.get("median")
    lo, hi = stats.get("min"), stats.get("max")
    if not all(isinstance(m, dict) and m.get("value") is not None for m in (q1, q3, med, lo, hi)):
        return None
    data = [{
        "type": "box", "name": name, "orientation": "h",
        "q1": [float(q1["value"])], "median": [float(med["value"])],
        "q3": [float(q3["value"])],
        "lowerfence": [float(lo["value"])], "upperfence": [float(hi["value"])],
        "marker": {"color": "#4a7deb"},
        "hoverinfo": "x",
    }]
    outl = (dist.get("outlier_candidates") or {}).get("count")
    title = f"{name} — spread (IQR box)" + (f" · {outl} outlier candidates" if outl else "")
    layout = {"title": title, "xaxis": {"title": "value"},
              "margin": {"l": 56, "r": 16, "t": 48, "b": 40},
              "height": 220}
    return {"kind": "box", "data": data, "layout": layout,
            "provenance": q1.get("provenance")}


def _charts_for_column(profile: Dict[str, Any], mask: bool = True) -> List[Dict[str, Any]]:
    """The data-type-aware Plotly specs for one column, built from the metrics
    the engine already computed (never recomputing here). A numeric column
    that has a histogram also gets its §10 \"outlier box\"; categorical and
    temporal columns get one chart each."""
    name = str(profile.get("column", ""))
    dist = profile.get("distribution") or {}
    stats = profile.get("stats") or {}
    cat = profile.get("categorical") or {}
    temporal = profile.get("temporal") or {}
    pii = mask and is_pii_column(profile)

    hist = dist.get("histogram")
    if isinstance(hist, dict) and hist.get("bins") and hist.get("counts"):
        numeric_bins = all(isinstance(b, (int, float)) for b in hist["bins"])
        if numeric_bins:
            # §3.2 stores one LEFT EDGE per bin (fixed_width, edges[i+1]-edges[i]
            # = step); the last bin gets a half-step so its bar stays on-scale
            # without inventing an upper edge the contract never carried.
            edges = [float(b) for b in hist["bins"]]
            step = edges[1] - edges[0] if len(edges) > 1 else 0.0
            centers = [e + step / 2 for e in edges]
            data = [{
                "x": centers[: len(hist["counts"])], "y": hist["counts"],
                "type": "bar", "marker": {"color": "#4a7deb"},
                "hovertemplate": "bin edge %{customdata:.6g}<br>count %{y}<extra></extra>",
                "customdata": edges[: len(hist["counts"])],
            }]
            layout = {
                "title": f"{name} — distribution (histogram)",
                "xaxis": {"title": "value"}, "yaxis": {"title": "count"},
                "bargap": 0.02, "margin": {"l": 56, "r": 16, "t": 48, "b": 48},
            }
            specs: List[Dict[str, Any]] = [{
                "kind": "histogram", "data": data, "layout": layout,
                "provenance": hist.get("provenance"),
            }]
            box = _box_spec(profile)
            if box is not None:
                specs.append(box)
            return specs

    if stats:
        box = _box_spec(profile)
        if box is not None:
            return [box]

    cats, freqs = cat.get("categories"), cat.get("frequencies")
    if isinstance(cats, list) and isinstance(freqs, list) and cats and len(cats) == len(freqs):
        logical = str(profile.get("logical_type", "")).lower()
        labels = [mask_value(str(c), logical) if pii else str(c) for c in cats]
        data = [{
            "x": labels, "y": freqs, "type": "bar", "marker": {"color": "#2fa385"},
            "hovertemplate": "%{x}<br>count %{y}<extra></extra>",
        }]
        layout = {
            "title": f"{name} — top categories" + (" (values masked)" if pii else ""),
            "xaxis": {"title": "category", "tickangle": -30},
            "yaxis": {"title": "count"},
            "margin": {"l": 56, "r": 16, "t": 48, "b": 72},
        }
        return [{"kind": "bar", "data": data, "layout": layout, "provenance": None}]

    volume = temporal.get("volume_per_granularity") or {}
    buckets = volume.get("buckets") if isinstance(volume, dict) else None
    if isinstance(buckets, list) and buckets:
        periods = [str(b.get("period", "")) for b in buckets]
        counts = [b.get("count", 0) for b in buckets]
        data = [{
            "x": periods, "y": counts, "type": "bar", "marker": {"color": "#8b5fc7"},
            "hovertemplate": "%{x}<br>rows %{y}<extra></extra>",
        }]
        layout = {
            "title": f"{name} — volume per {volume.get('granularity', 'bucket')}",
            "xaxis": {"title": "period"}, "yaxis": {"title": "row count"},
            "margin": {"l": 56, "r": 16, "t": 48, "b": 56},
        }
        return [{"kind": "volume", "data": data, "layout": layout, "provenance": None}]
    return []


def _missingness_chart(profiles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Dataset-level missingness bar: each column's stored missing_rate, one
    bar each — the §10 \"missingness bar\". No recomputation, values are the
    engine's own MetricValues."""
    names, rates, methods = [], [], []
    for p in profiles:
        miss = p.get("missingness") or {}
        mv = miss.get("missing_rate")
        if isinstance(mv, dict) and mv.get("value") is not None:
            names.append(str(p.get("column", "")))
            rates.append(float(mv["value"]))
            methods.append(str((mv.get("provenance") or {}).get("method", "exact")))
    if not names:
        return None
    data = [{
        "x": names, "y": rates, "type": "bar", "marker": {"color": "#d9822b"},
        "text": methods, "hovertemplate": "%{x}<br>missing %{y:.3%}<br>method %{text}<extra></extra>",
    }]
    layout = {
        "title": "Missing rate per column", "yaxis": {"title": "missing rate",
                                                       "tickformat": ".1%"},
        "margin": {"l": 64, "r": 16, "t": 48, "b": 88},
    }
    return {"kind": "missingness", "data": data, "layout": layout, "provenance": None}


# ---------------------------------------------------------------------------
# Section rendering
# ---------------------------------------------------------------------------

def _status_counts(checks: List[Dict[str, Any]], findings: List[Dict[str, Any]]) -> str:
    """§10 \"status counts\": quality outcomes + finding severities as chips."""
    counts: Dict[str, int] = {}
    for c in checks:
        counts[str(c.get("status", "?"))] = counts.get(str(c.get("status", "?")), 0) + 1
    sev: Dict[str, int] = {}
    for f in findings:
        sev[str(f.get("severity", "?"))] = sev.get(str(f.get("severity", "?")), 0) + 1
    chips = "".join(
        f'<span class="chip chip-{ _esc(s.lower()) }">{_esc(s)} <b>{n}</b></span>'
        for s, n in sorted(counts.items())
    )
    sev_chips = "".join(
        f'<span class="chip sev-{ _esc(s.lower()) }">{_esc(s)} <b>{n}</b></span>'
        for s, n in sorted(sev.items())
    )
    return (
        '<section id="status"><h2>Status counts</h2>'
        f'<p><span class="label">Quality checks:</span> {chips or "<em>none</em>"}</p>'
        f'<p><span class="label">Findings by severity:</span> {sev_chips or "<em>none</em>"}</p>'
        "</section>"
    )


def _run_metadata(run: Dict[str, Any]) -> str:
    """§10 \"run metadata\": identity + reproducibility tuple + engine stamp.
    Values come straight off the canonical run record; nothing is invented."""
    ds = run.get("dataset") or {}
    snap = ds.get("source_snapshot") or {}
    cfg = run.get("config") or {}
    eng = run.get("engine") or {}
    timing = run.get("timing") or {}
    sampling = run.get("sampling")
    rows: List[Tuple[str, str]] = [
        ("Run", _esc(run.get("run_id"))),
        ("Dataset", _esc(ds.get("dataset_id"))),
        ("Source", f"{_esc(ds.get('source_kind'))} · {_esc(ds.get('connector'))} · {_esc(ds.get('source_ref'))}"),
        ("Source snapshot", f"{_esc(snap.get('size_bytes'))} bytes · modified {_esc(snap.get('modified_at'))} · {_esc(snap.get('content_hash'))}"),
        ("Status", _esc(run.get("status"))),
        ("Profile mode", _esc(cfg.get("profile_mode"))),
        ("Config", f"v{_esc(cfg.get('config_version'))} · {_esc(cfg.get('config_hash'))}"),
        ("Engine", f"dpe {_esc(eng.get('engine_version'))} · duckdb {_esc(eng.get('duckdb_version'))} · python {_esc(eng.get('python_version'))}"),
        ("Timing", f"{_esc(timing.get('started_at'))} → {_esc(timing.get('finished_at'))} ({_esc(timing.get('duration_ms'))} ms)"),
    ]
    if isinstance(sampling, dict) and sampling.get("enabled"):
        rows.append(("Sampling",
                     f"{_esc(sampling.get('strategy'))} · n={_esc(sampling.get('sample_size'))} · seed={_esc(sampling.get('seed'))}"))
    else:
        rows.append(("Sampling", "not enabled (full relation)"))
    body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    errors = run.get("error_log") or []
    error_html = ""
    if errors:
        items = "".join(
            f"<li><b>{_esc(e.get('code'))}</b>"
            + (f" [{_esc(e.get('task'))}]" if e.get("task") else "")
            + f" — {_esc(e.get('message'))}</li>"
            for e in errors
        )
        error_html = (
            '<p class="warn-note"><span class="label">Per-task errors '
            f"(run still completed — FAIL≠ERROR, CONTRACTS §6.4):</span></p><ul>{items}</ul>"
        )
    return f'<section id="run-metadata"><h2>Run metadata</h2><table class="kv">{body}</table>{error_html}</section>'


def _column_table(profile: Dict[str, Any], charts: List[Dict[str, Any]], mask: bool = True) -> str:
    """§10 \"column profiles\": one card per column with its key MetricValues,
    each carrying its ADR-015 badge, plus its data-type-aware chart."""
    head_rows: List[str] = []
    for p in profile.get("columns") or []:
        name = str(p.get("column", ""))
        miss, miss_badge = _metric_value_doc((p.get("missingness") or {}).get("missing_rate"))
        dist, dist_badge = _metric_value_doc((p.get("uniqueness") or {}).get("distinct_count"))
        pii = mask and is_pii_column(p)
        pii_chip = '<span class="badge badge-pii" title="values masked (SECURITY §4)">PII · masked</span>' if pii else ""
        unit = p.get("unit") or {}
        unit_txt = f" ({_esc(unit.get('currency'))} · {_esc(unit.get('status'))})" if unit.get("currency") else ""
        head_rows.append(
            "<tr>"
            f'<td><a href="#col-{_esc(re.sub(r"[^A-Za-z0-9_.-]", "-", name))}">{_esc(name)}</a>{unit_txt}</td>'
            f"<td>{_esc(p.get('physical_type'))}</td>"
            f"<td>{_esc(p.get('logical_type'))}</td>"
            f"<td>{_esc(p.get('semantic_type'))} {pii_chip}</td>"
            f'<td class="num">{miss} {miss_badge}</td>'
            f'<td class="num">{dist} {dist_badge}</td>'
            "</tr>"
        )
    table = (
        '<table class="grid"><thead><tr><th>Column</th><th>Physical</th>'
        "<th>Logical</th><th>Semantic</th><th>Missing rate</th><th>Distinct</th>"
        f"</tr></thead><tbody>{''.join(head_rows)}</tbody></table>"
    )

    detail: List[str] = []
    for p in profile.get("columns") or []:
        name = str(p.get("column", ""))
        anchor = re.sub(r"[^A-Za-z0-9_.-]", "-", name)
        pii = mask and is_pii_column(p)  # SECURITY §4: per-column, never inherited
        blocks: List[str] = []
        stats = p.get("stats") or {}
        if stats:
            cells = "".join(
                f"<th>{label}</th>{_mv_cell(stats.get(key))}"
                for key, label in (
                    ("min", "min"), ("p25", "p25"), ("median", "median"),
                    ("p75", "p75"), ("p95", "p95"), ("p99", "p99"),
                    ("max", "max"), ("mean", "mean"), ("stddev", "stddev"),
                )
                if key in stats
            )
            if cells:
                vc = stats.get("valid_count")
                vc_txt = f"<p><span class='label'>valid rows:</span> {_fmt_num(vc)}</p>" if vc is not None else ""
                blocks.append(f"<h4>Numeric stats</h4>{vc_txt}<table class='stats'>{cells}</table>")
        dist = p.get("distribution") or {}
        dist_bits: List[str] = []
        if dist.get("skewness") or dist.get("outlier_candidates"):
            row = ""
            if dist.get("skewness"):
                row += f"<th>skewness</th>{_mv_cell(dist['skewness'])}"
            oc = dist.get("outlier_candidates") or {}
            if oc:
                prov = oc.get("provenance") or {}
                row += (
                    f"<th>outliers ({_esc(oc.get('method'))})</th>"
                    f'<td class="num">{_fmt_num(oc.get("count"))} {_badge(prov)}</td>'
                )
            if row:
                dist_bits.append(f"<table class='stats'>{row}</table>")
            refs = (oc.get("sample_row_refs") if isinstance(oc, dict) else None) or []
            if refs:
                dist_bits.append(f'<p class="refs">row refs: {_esc(", ".join(map(str, refs)))}</p>')
        if dist_bits:
            blocks.append("<h4>Distribution</h4>" + "".join(dist_bits))
        cat = p.get("categorical") or {}
        if cat.get("categories"):
            shown = [
                mask_value(str(c), str(p.get("logical_type", "")).lower()) if pii else str(c)
                for c in cat["categories"][:15]
            ]
            blocks.append(
                "<h4>Categories</h4><p class='refs'>"
                + _esc(", ".join(shown))
                + (f" · {len(cat['categories']) - len(shown)} more" if len(cat["categories"]) > len(shown) else "")
                + "</p>"
            )
        rare = cat.get("rare_categories") or []
        if rare:
            shown_rare = [
                mask_value(str(c), str(p.get("logical_type", "")).lower()) if pii else str(c)
                for c in rare[:15]
            ]
            blocks.append(f"<h4>Rare categories</h4><p class='refs'>{_esc(', '.join(shown_rare))}</p>")
        temporal = p.get("temporal") or {}
        rng = temporal.get("range") or {}
        if rng.get("min") or rng.get("max"):
            tz = f" · tz source: {_esc(temporal.get('timezone_source'))}" if temporal.get("timezone_source") else ""
            blocks.append(f"<h4>Temporal range</h4><p class='refs'>{_esc(rng.get('min'))} → {_esc(rng.get('max'))}{tz}</p>")
        chart_html = ""
        for spec in _charts_for_column(p, mask):
            spec = dict(spec)
            spec["id"] = f"chart-col-{anchor}-{spec['kind']}"
            charts.append(spec)
            chart_html += (
                f'<div class="chart" id="{_esc(spec["id"])}" '
                f'data-method="{_esc((spec.get("provenance") or {}).get("method", ""))}"></div>'
            )
        pii_badge = '<span class="badge badge-pii">PII · masked</span>' if pii else ""
        detail.append(
            f'<div class="card col-card" id="col-{_esc(anchor)}">'
            f"<h3>{_esc(name)} {pii_badge}</h3>"
            f'<p class="types">{_esc(p.get("physical_type"))} → {_esc(p.get("logical_type"))} · {_esc(p.get("semantic_type"))}</p>'
            + "".join(blocks) + chart_html + "</div>"
        )

    miss_chart = _missingness_chart(list(profile.get("columns") or []))
    miss_html = ""
    if miss_chart is not None:
        miss_chart = dict(miss_chart)
        miss_chart["id"] = "chart-missingness"
        charts.append(miss_chart)
        miss_html = f'<div class="chart wide" id="chart-missingness"></div>'
    return (
        '<section id="columns"><h2>Column profiles</h2>'
        + table
        + '<h3>Missingness</h3>' + miss_html
        + '<div class="cols">' + "".join(detail) + "</div></section>"
    )


def _quality_table(checks: List[Dict[str, Any]]) -> str:
    """§10 \"quality table\": one row per QualityCheckResult, observed value
    with its badge, threshold, failing records, evidence, ERROR ≠ FAIL."""
    rows: List[str] = []
    for c in checks:
        rule_id = str(c.get("rule_id", ""))
        anchor = re.sub(r"[^A-Za-z0-9_.-]", "-", rule_id)
        thr = c.get("threshold") or {}
        thr_txt = (
            f"{_esc(thr.get('operator'))} {_esc(thr.get('value'))}"
            + (f" … {_esc(thr.get('upper'))}" if thr.get("upper") is not None else "")
            if thr else "—"
        )
        obs_display, obs_badge = _metric_value_doc(c.get("observed"))
        err = c.get("error")
        err_txt = f'<span class="err">{_esc(err.get("code"))}: {_esc(err.get("message"))}</span>' if err else ""
        evid = c.get("evidence") or {}
        refs = evid.get("sample_row_refs") or []
        refs_txt = f'<p class="refs" id="rule-{_esc(anchor)}">row refs: {_esc(", ".join(refs[:10]))}{"…" if len(refs) > 10 else ""}</p>' if refs else f'<span id="rule-{_esc(anchor)}"></span>'
        col = c.get("column")
        col_link = (
            f'<a href="#col-{_esc(re.sub(r"[^A-Za-z0-9_.-]", "-", str(col)))}">{_esc(col)}</a>'
            if col else "<em>dataset</em>"
        )
        rows.append(
            "<tr>"
            f"<td>{_esc(rule_id)}<small> v{_esc(c.get('rule_version'))}</small></td>"
            f"<td>{_esc(c.get('dimension'))}</td><td>{col_link}</td>"
            f'<td><span class="chip chip-{_esc(str(c.get("status", "")).lower())}">{_esc(c.get("status"))}</span></td>'
            f"<td>{_esc(c.get('severity'))}</td>"
            f'<td class="num">{obs_display} {obs_badge}</td>'
            f"<td>{thr_txt}</td>"
            f'<td class="num">{_fmt_num(c.get("failing_record_count"))}</td>'
            f"<td>{err_txt}{refs_txt}</td>"
            "</tr>"
        )
    return (
        '<section id="quality"><h2>Quality checks</h2>'
        '<table class="grid"><thead><tr><th>Rule</th><th>Dimension</th><th>Column</th>'
        "<th>Status</th><th>Severity</th><th>Observed</th><th>Threshold</th>"
        "<th>Failing records</th><th>Evidence / error</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _evidence_href(ref: str, column_names: set) -> str:
    """Map a finding ``evidence_refs`` / fact ref to an in-document anchor when
    the target is actually rendered here (§10 \"findings + evidence links\");
    anything else stays plain text — no fabricated links. Recognised shapes
    are the detector's own: ``rule:<rule_id>`` and ``metric:<column>.…``."""
    if ref.startswith("rule:"):
        return f"#rule-{re.sub(r'[^A-Za-z0-9_.-]', '-', ref[5:])}"
    if ref.startswith("metric:"):
        head = ref[7:].split(".", 1)[0]
        if head in column_names:
            return f"#col-{re.sub(r'[^A-Za-z0-9_.-]', '-', head)}"
    return ""


def _findings_section(findings: List[Dict[str, Any]], column_names: set) -> str:
    """§10 \"findings + evidence links\": facts / interpretation / hypotheses
    stay structurally separate (CONTRACTS §4.2); the ADR-016 fingerprint is
    shown so the same issue is recognisable across runs."""
    if not findings:
        return '<section id="findings"><h2>Findings</h2><p><em>None.</em></p></section>'
    cards: List[str] = []
    for f in findings:
        fid = str(f.get("finding_id", ""))
        anchor = re.sub(r"[^A-Za-z0-9_.-]", "-", fid)
        scope = f.get("scope") or {}
        facts = "".join(
            "<li><code>" + _esc(ft.get("metric_ref")) + "</code> = "
            + _fmt_num(ft.get("value")) + " " + _badge(ft.get("provenance")) + "</li>"
            for ft in f.get("facts") or []
        )
        hyps = "".join(
            "<li>" + _esc(h.get("text")) + f' <small>({_esc(h.get("status"))})</small>'
            + (f" — probe: {_esc(h.get('suggested_probe'))}" if h.get("suggested_probe") else "")
            + "</li>"
            for h in f.get("hypotheses") or []
        )
        evid = "".join(
            (f'<a href="{_esc(_evidence_href(ref, column_names))}"><code>{_esc(ref)}</code></a> · '
             if _evidence_href(ref, column_names) else f"<code>{_esc(ref)}</code> · ")
            for ref in f.get("evidence_refs") or []
        )
        rec_ids = "".join(
            f'<a href="#rec-{_esc(re.sub(r"[^A-Za-z0-9_.-]", "-", rid))}">{_esc(rid)}</a> '
            for rid in f.get("recommendation_ids") or []
        )
        interp = (
            f'<p class="interp">{_esc(f.get("interpretation"))}</p>'
            if f.get("interpretation") else ""
        )
        cards.append(
            f'<div class="card" id="find-{_esc(anchor)}">'
            f'<h3>{_esc(fid)} — {_esc(f.get("title"))} '
            f'<span class="chip sev-{_esc(str(f.get("severity", "")).lower())}">{_esc(f.get("severity"))}</span></h3>'
            f'<p>{_esc(f.get("summary"))}</p>'
            f'<p class="meta">category {_esc(f.get("category"))} · confidence {_esc(f.get("confidence"))}'
            f' · scope: {_esc(scope.get("column") or "dataset")}'
            + (f' · {scope.get("affected_records")} records' if scope.get("affected_records") is not None else "")
            + f' · fingerprint <code title="{_esc(f.get("fingerprint"))}">{_esc(str(f.get("fingerprint", ""))[:71])}…</code></p>'
            f"<h4>Facts</h4><ul>{facts}</ul>"
            + interp
            + (f"<h4>Hypotheses</h4><ul>{hyps}</ul>" if hyps else "")
            + f'<h4>Evidence</h4><p class="refs">{evid}</p>'
            + (f'<h4>Recommendations</h4><p>{rec_ids}</p>' if rec_ids else "")
            + "</div>"
        )
    return f'<section id="findings"><h2>Findings</h2>{"".join(cards)}</section>'


def _recommendations_section(recs: List[Dict[str, Any]]) -> str:
    """CONTRACTS §4.3 recommendations cited by their findings; each must
    reference existing evidence, which is rendered as links where possible."""
    if not recs:
        return ""
    items: List[str] = []
    for r in recs:
        rid = str(r.get("recommendation_id", ""))
        anchor = re.sub(r"[^A-Za-z0-9_.-]", "-", rid)
        fid = str(r.get("finding_id", ""))
        evid = "".join(
            f"<code>{_esc(ref)}</code> · " for ref in r.get("evidence_refs") or []
        )
        donots = r.get("do_not") or []
        dnt = (
            '<p class="donot"><span class="label">Do not:</span> '
            + _esc("; ".join(map(str, donots))) + "</p>"
            if donots else ""
        )
        items.append(
            f'<div class="card" id="rec-{_esc(anchor)}">'
            f'<h3>{_esc(rid)} — <a href="#find-{_esc(re.sub(r"[^A-Za-z0-9_.-]", "-", fid))}">{_esc(fid)}</a></h3>'
            f"<p>{_esc(r.get('action'))}</p>"
            f'<p class="meta">{_esc(r.get("rationale"))} · reason: {_esc(r.get("reason_type"))}'
            + (f' · target: {_esc(r.get("target_improvement"))}' if r.get("target_improvement") else "")
            + f' · status: {_esc(r.get("status"))}</p>'
            + dnt
            + (f'<p class="refs">{evid}</p>' if evid else "")
            + "</div>"
        )
    return f'<section id="recommendations"><h2>Recommendations</h2>{"".join(items)}</section>'


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------

_CSS = """
:root { --ink:#22272e; --muted:#5b6572; --line:#d7dce2; --bg:#f6f8fa; }
* { box-sizing: border-box; }
body { font: 14px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; color: var(--ink);
       margin: 0 auto; max-width: 1100px; padding: 24px 20px 64px; background:#fff; }
h1 { font-size: 24px; margin: 0 0 2px; } h2 { font-size: 19px; margin: 34px 0 10px;
     border-bottom: 2px solid var(--line); padding-bottom: 4px; }
h3 { font-size: 15px; margin: 14px 0 6px; } h4 { font-size: 13px; margin: 12px 0 4px;
     color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.sub { color: var(--muted); margin: 0 0 18px; }
section { }
table { border-collapse: collapse; width: 100%; margin: 8px 0 14px; }
table.grid th, table.grid td { border: 1px solid var(--line); padding: 5px 8px;
     text-align: left; vertical-align: top; font-size: 13px; }
table.grid thead th { background: var(--bg); }
table.kv th { text-align: left; width: 190px; color: var(--muted); font-weight: 600;
     padding: 3px 10px 3px 0; vertical-align: top; }
table.kv td { padding: 3px 0; }
table.stats { width:auto; } table.stats th, table.stats td { border:1px solid var(--line);
     padding:4px 8px; background:#fff; } table.stats th { color: var(--muted); }
td.num, .num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.num small { color: var(--muted); }
.badge { display:inline-block; font-size:10.5px; font-weight:600; border-radius:8px;
     padding:0 7px; line-height:16px; vertical-align: 1px; }
.badge-exact { background:#e2f2e6; color:#1c6b33; }
.badge-approx { background:#fdf0d9; color:#8a5b00; }
.badge-sampled { background:#e3ecfb; color:#2b55b0; }
.badge-notcomputed { background:#fbe3e3; color:#a02020; }
.badge-unknown { background:var(--bg); color:var(--muted); }
.badge-pii { background:#f3e3fb; color:#7d2aa0; }
.chip { display:inline-block; border-radius:10px; padding:1px 9px; font-size:12px;
     background:var(--bg); border:1px solid var(--line); margin-right:4px; }
.chip-pass { background:#e2f2e6; border-color:#b7dfc2; }
.chip-fail { background:#fbe3e3; border-color:#f0b9b9; }
.chip-warn, .sev-high, .sev-critical { background:#fdf0d9; border-color:#ecd3a2; }
.chip-error { background:#fbe3e3; border-color:#f0b9b9; }
.sev-medium { background:#e3ecfb; border-color:#bcd0f0; }
.sev-low { background:var(--bg); }
.card { border:1px solid var(--line); border-radius:8px; padding:10px 16px 12px;
     margin: 10px 0; background:#fff; }
.cols { display:grid; grid-template-columns: repeat(auto-fill, minmax(480px, 1fr));
     gap: 12px; }
.col-card { break-inside: avoid; }
.types { color: var(--muted); margin: 0; font-size: 12.5px; }
.chart { height: 300px; margin: 10px -8px 0; } .chart.wide { height: 330px; width: 100%; }
.refs { color: var(--muted); font-size: 12.5px; overflow-wrap: anywhere; }
.meta { color: var(--muted); font-size: 12.5px; }
.interp { border-left: 3px solid var(--line); margin-left: 2px; padding-left: 10px; }
.err { color:#a02020; } .warn-note { color:#8a5b00; }
.donot { color:#a02020; font-size: 12.5px; }
.not-computed { color:#a02020; } .not-computed small { color: var(--muted); }
code { background: var(--bg); border-radius: 4px; padding: 0 4px; font-size: 12px; }
a { color:#2b55b0; text-decoration: none; } a:hover { text-decoration: underline; }
footer { margin-top: 40px; color: var(--muted); font-size: 12px; }
.masking-note { background:#f3e3fb; border-radius: 6px; padding: 6px 10px;
     font-size: 12.5px; color:#5b2275; }
"""


def _masking_banner(profile_cols: List[Dict[str, Any]]) -> str:
    pii_names = [str(p.get("column")) for p in profile_cols if is_pii_column(p)]
    if not pii_names:
        return ""
    return (
        '<p class="masking-note"><b>Privacy:</b> probable PII columns ('
        + _esc(", ".join(pii_names))
        + ") are shown with masked values by default (SECURITY §4). "
        "Full values exist only in the access-controlled JSON artifact.</p>"
    )


def render_html_report(bundle: ProfileBundle, *, mask_pii: bool = True) -> str:
    """Render the whole §10 HTML report as one self-contained string.

    Consumes the canonical result document (:func:`reporting.result_document`)
    of the same :class:`ProfileBundle` the JSON writer pins — one input
    shape, zero recomputation. ``mask_pii=False`` exposes raw sample values
    and exists only for explicitly authorised internal use; the default
    honours SECURITY §4 (\"Default: mask\")."""
    doc = result_document(bundle)
    run, profile = doc["run"], doc["profile"]
    checks, findings, recs = doc["checks"], doc["findings"], doc["recommendations"]
    cols = list(profile.get("columns") or [])
    column_names = {str(c.get("column", "")) for c in cols}

    charts: List[Dict[str, Any]] = []
    title = f"Data Profile — {_esc(run['dataset']['dataset_id'])} · {_esc(run['run_id'])}"
    body = (
        f"<h1>{title}</h1>"
        '<p class="sub">Self-contained offline report · generated by the Data Profiling Engine · '
        "every number carries its <span class=\"badge badge-exact\">exact</span>/"
        "<span class=\"badge badge-sampled\">sampled</span>/"
        "<span class=\"badge badge-approx\">approximate</span>/"
        "<span class=\"badge badge-notcomputed\">not_computed</span> method badge (ADR-015)</p>"
        + (_masking_banner(cols) if mask_pii else "")
        + _status_counts(checks, findings)
        + _run_metadata(run)
        + _column_table(profile, charts, mask_pii)
        + _quality_table(checks)
        + _findings_section(findings, column_names)
        + _recommendations_section(recs)
        + "<footer>Payloads embedded above are rendered from the canonical run result; "
          "charts and text are computed offline — this file fetches nothing (ADR-005).</footer>"
    )

    plotly_src = _plotly_source()
    chart_specs = _js_json(charts)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title}</title>"
        f"<style>{_CSS}</style>"
        f"<script>{plotly_src}</script>"
        "</head><body>"
        + body
        + f'<script type="application/json" id="dpe-charts">{chart_specs}</script>'
        "<script>(function(){var specs=JSON.parse(document.getElementById('dpe-charts').textContent);"
        "specs.forEach(function(s){Plotly.newPlot(s.id,s.data,s.layout,"
        "{responsive:true,displaylogo:false,modeBarButtonsToRemove:['lasso2d','select2d']});});})();"
        "</script></body></html>\n"
    )


def write_html_report(bundle: ProfileBundle, out_path: "Path | str", *, mask_pii: bool = True) -> Path:
    """Render + write the HTML report to ``out_path`` (the CLI ``--html-out``
    target). Mirrors :func:`reporting.write_profile_json`'s contract: creates
    parents, UTF-8, trailing newline, returns the path."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": byte-stable across OSes; default text mode = CRLF on Windows
    out.write_text(render_html_report(bundle, mask_pii=mask_pii), encoding="utf-8", newline="\n")
    return out


def emit_html_report_artifact(
    bundle: ProfileBundle, store: "Any", *, mask_pii: bool = True
) -> str:
    """Write the report through an :class:`~data_profiling_engine.storage.artifact_store.ArtifactStore`
    under the canonical ``html_report`` kind (``artifacts/<run_id>/report.html``)
    and return the store-relative ref for ``run_artifacts`` / ``result_refs``.

    Retention comes from the store's own kind policy (SECURITY §2: run results
    survive upgrades, explicit-delete only) — the report inherits it for free."""
    content = render_html_report(bundle, mask_pii=mask_pii)
    return store.write_text(bundle.run.run_id, "html_report", content)


__all__ = [
    "PLOTLY_ASSET",
    "PII_LOGICAL_TYPES",
    "is_pii_column",
    "mask_value",
    "render_html_report",
    "write_html_report",
    "emit_html_report_artifact",
]
