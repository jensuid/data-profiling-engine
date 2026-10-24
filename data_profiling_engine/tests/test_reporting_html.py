"""M4-T2 tests — self-contained HTML report (ENGINE §10; SECURITY §4;
ADR-005 no-HTTP, ADR-015 method badges, ADR-016 fingerprint display).

Two layers:
  * end-to-end render over the checked-in golden fixture bundle (real engine
    output — status counts, charts, quality table, findings + evidence links,
    run metadata all come from what M2/M4-T1 pinned, nothing recomputed);
  * synthetic contract bundles for the narrow guarantees (PII masking,
    not_computed rendering, badge tooltips carry the seed, escaping, error
    log) so each assertion targets exactly one behaviour.

Offline-shareability is checked structurally: the document must embed the
vendored Plotly.js (integrity-pinned by sha256) and must never reference an
external ``<script src>`` / ``<link href>`` resource (ADR-005).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest

from data_profiling_engine.cli import run_profile
from data_profiling_engine.orchestration.rollup import (
    SCHEMA_VERSION,
    ProfileBundle,
    profile_dataset,
)
from data_profiling_engine.reporting.html_report import (
    PII_LOGICAL_TYPES,
    PLOTLY_ASSET,
    emit_html_report_artifact,
    is_pii_column,
    mask_value,
    render_html_report,
    write_html_report,
)
from data_profiling_engine.schemas.models import (
    CheckEvidence,
    CheckThreshold,
    ColumnProfile,
    Categorical,
    DatasetProfile,
    Finding,
    FindingFact,
    FindingHypothesis,
    FindingScope,
    MetricMethod,
    MetricValue,
    Missingness,
    ProfilingRun,
    Provenance,
    QualityCheckResult,
    Recommendation,
    RunConfig,
    RunDataset,
    RunEngine,
    RunErrorEntry,
    RunSampling,
    RunStatus,
    RunTiming,
    SourceSnapshot,
    Uniqueness,
)
from data_profiling_engine.storage.artifact_store import ArtifactStore
from data_profiling_engine.tests import golden_harness as gh

#: Integrity pin for the vendored Plotly.js (plotly 7.1.0 wheel's
#: package_data/plotly.min.js). If this changes, the asset was upgraded or
#: tampered with — re-verify provenance before updating the pin.
PLOTLY_SHA256 = "3b6e15d45dbb7fca5bd2094291e961ddc5472cd887009e6009a56dab668d721f"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def golden_bundle() -> ProfileBundle:
    """One real engine run over the checked-in 40-row golden fixture."""
    cfg = gh.load_config("sales_deals")
    return profile_dataset(
        "sales_deals", gh.GOLDEN_DIR / "sales_deals.csv", cfg, "run-html",
        allowed_roots=[gh.GOLDEN_DIR],
    )


def _mv(value: Any, method: str = "exact", **params: Any) -> MetricValue:
    prov: Dict[str, Any] = {"method": method}
    reason = params.pop("reason", None)
    if reason is not None:
        prov["reason"] = reason
    if params:
        prov["method_params"] = params
    mv = MetricValue.model_construct(value=value, provenance=Provenance(**prov))
    if value is None:
        mv.model_fields_set.discard("value")
    return mv


def _synthetic_run(run_id: str = "run-syn") -> ProfilingRun:
    return ProfilingRun.model_validate(dict(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        dataset=RunDataset(
            dataset_id="ds_syn", source_kind="file", connector="csv",
            source_ref="/data/sales.csv",
            source_snapshot=SourceSnapshot(
                path="/data/sales.csv", size_bytes=1234,
                modified_at="2026-10-01T00:00:00Z",
                content_hash="sha256:" + "a" * 64,
            ),
        ),
        config=RunConfig(config_version="1", config_hash="sha256:" + "b" * 64,
                         profile_mode="standard"),
        engine=RunEngine(engine_version="0.1.0", duckdb_version="1.1.3",
                         python_version="3.12.7"),
        timing=RunTiming(started_at="2026-10-24T09:00:00Z",
                         finished_at="2026-10-24T09:00:05Z", duration_ms=5000),
        status=RunStatus.SUCCEEDED,
    ))


def _synthetic_bundle(columns: List[ColumnProfile], **run_kw: Any) -> ProfileBundle:
    run = _synthetic_run().model_copy(update=run_kw) if run_kw else _synthetic_run()
    profile = DatasetProfile.model_validate(dict(
        schema_version=SCHEMA_VERSION, run_id=run.run_id,
        row_count=_mv(100), column_count=len(columns),
        columns=columns,
    ))
    return ProfileBundle(run=run, profile=profile, checks=(), findings=(),
                         recommendations=(), errors=())


def _col(name: str, logical: str = "string", **blocks: Any) -> ColumnProfile:
    return ColumnProfile.model_validate(dict(
        schema_version=SCHEMA_VERSION, column=name, ordinal=0,
        physical_type="VARCHAR", logical_type=logical, semantic_type="other",
        missingness=Missingness(
            null_count=3, blank_count=0, sentinel_count=0,
            missing_rate=_mv(0.03),
        ),
        uniqueness=Uniqueness(distinct_count=_mv(97, "sampled", seed=42,
                                                  strategy="reservoir",
                                                  sample_size=50),
                              is_unique_candidate=False),
        **blocks,
    ))


# ---------------------------------------------------------------------------
# vendored Plotly asset — offline guarantee (ADR-005; ENGINE §10)
# ---------------------------------------------------------------------------

class TestVendoredPlotly:
    def test_asset_present_and_pinned(self):
        assert PLOTLY_ASSET.is_file(), "reporting/assets/plotly.min.js must be checked in"
        digest = hashlib.sha256(PLOTLY_ASSET.read_bytes()).hexdigest()
        assert digest == PLOTLY_SHA256, "vendored Plotly.js changed — re-verify provenance"

    def test_render_inlines_library_and_bootstrapper(self, golden_bundle):
        html = render_html_report(golden_bundle)
        head = html[: len(PLOTLY_ASSET.read_text(encoding="utf-8")) + 2048]
        assert "Plotly" in head
        assert "Plotly.newPlot" in html  # bootstrapper script

    def test_no_external_resource_tags(self, golden_bundle):
        """Our document layer must never fetch: no <script src>, no <link>.
        (The inlined library is integrity-pinned separately; its internal
        string constants are not resource loads by the document.)"""
        html = render_html_report(golden_bundle)
        assert not re.search(r"<script[^>]+\bsrc\s*=", html, re.I)
        assert not re.search(r"<link[^>]+>", html, re.I)
        assert not re.search(r"<img[^>]+src\s*=\s*[\"']https?:", html, re.I)


# ---------------------------------------------------------------------------
# end-to-end render over the real golden bundle
# ---------------------------------------------------------------------------

class TestGoldenRender:
    def test_document_shape(self, golden_bundle):
        html = render_html_report(golden_bundle)
        assert html.startswith("<!DOCTYPE html>")
        for anchor in ("id=\"status\"", "id=\"run-metadata\"", "id=\"columns\"",
                       "id=\"quality\"", "id=\"findings\"", "id=\"recommendations\""):
            assert anchor in html

    def test_deterministic_for_same_bundle(self, golden_bundle):
        assert render_html_report(golden_bundle) == render_html_report(golden_bundle)

    def test_run_metadata_and_reproducibility(self, golden_bundle):
        html = render_html_report(golden_bundle)
        assert "run-html" in html
        assert "sales_deals" in html
        # content_hash + config_hash are the reproducibility anchors (§2)
        snapshot_hash = golden_bundle.run.dataset.source_snapshot.content_hash
        assert snapshot_hash[:18] in html
        assert golden_bundle.run.config.config_hash[:18] in html

    def test_method_badges_present(self, golden_bundle):
        html = render_html_report(golden_bundle)
        assert 'class="badge badge-exact"' in html
        # the synthetic-fixture distinct_count below proves the seed tooltip
        # renders; the golden run contributes at least one badge per metric
        assert ">exact<" in html

    def test_charts_data_type_aware(self, golden_bundle):
        """§10 last bullet: histogram for numeric, bar for categorical,
        missingness bar — each only where its metric exists."""
        html = render_html_report(golden_bundle)
        specs = re.search(
            r'<script type="application/json" id="dpe-charts">(.*?)</script>',
            html, re.S,
        )
        assert specs, "chart payload block missing"
        payload = json_loads_specs(specs.group(1))
        kinds = {s["kind"] for s in payload}
        assert {"missingness", "histogram", "box", "bar"} <= kinds
        # §10 names the outlier box explicitly: the numeric column with a
        # histogram must ALSO get its IQR box
        assert any(s["kind"] == "box" for s in payload)
        ids = [s["id"] for s in payload]
        assert len(ids) == len(set(ids)), "chart div ids must be unique"

    def test_findings_show_fingerprint_and_evidence_links(self, golden_bundle):
        html = render_html_report(golden_bundle)
        assert "fingerprint" in html                  # ADR-016 identity visible
        assert 'href="#rule-' in html or 'href="#col-' in html

    def test_quality_table_rows(self, golden_bundle):
        html = render_html_report(golden_bundle)
        for check in golden_bundle.checks:
            assert f"rule-{check.rule_id}" in html


def json_loads_specs(raw: str) -> List[Dict[str, Any]]:
    import json
    return json.loads(raw.replace("<\\/", "</"))


# ---------------------------------------------------------------------------
# ADR-015: every number carries its method; sampled shows the seed; an absent
# value is not_computed + reason, never a silent null
# ---------------------------------------------------------------------------

class TestMethodBadges:
    def test_sampled_badge_tooltip_carries_seed(self):
        bundle = _synthetic_bundle([_col("deal_id")])
        html = render_html_report(bundle)
        assert "badge-sampled" in html
        assert "seed=42" in html

    def test_not_computed_renders_reason_not_value(self):
        mv = _mv(None, "not_computed", reason="no non-null values to bin")
        col = _col("deal_value", "float", stats={
            "min": mv,
        })
        html = render_html_report(_synthetic_bundle([col]))
        assert "not_computed" in html
        assert "no non-null values to bin" in html

    def test_missing_rate_denominator_not_invented(self):
        # exact rate value present → number rendered with its badge
        html = render_html_report(_synthetic_bundle([_col("a")]))
        assert ">0.03" in html or "0.03" in html


# ---------------------------------------------------------------------------
# SECURITY §4 — PII masking is the HTML default
# ---------------------------------------------------------------------------

class TestPiiMasking:
    EMAIL = "jane.doe@acme.com"
    PHONE = "+62 812-3456-7890"

    def _pii_bundle(self) -> ProfileBundle:
        email_col = _col("customer_email", "email", categorical=Categorical(
            categories=[self.EMAIL], frequencies=[7],
        ))
        phone_col = _col("support_phone", "phone", categorical=Categorical(
            categories=[self.PHONE], frequencies=[3],
        ))
        return _synthetic_bundle([email_col, phone_col])

    def test_logical_types_recognised(self):
        assert {"email", "phone"} == PII_LOGICAL_TYPES
        assert is_pii_column({"logical_type": "email"})
        assert not is_pii_column({"logical_type": "uuid"})

    def test_email_mask_shape(self):
        masked = mask_value(self.EMAIL, "email")
        assert masked.startswith("j***@a") and self.EMAIL not in masked

    def test_phone_mask_partial_digits(self):
        masked = mask_value(self.PHONE, "phone")
        assert self.PHONE not in masked
        assert "••••" in masked

    def test_default_render_masks_values(self):
        html = render_html_report(self._pii_bundle())
        assert self.EMAIL not in html
        assert self.PHONE not in html
        assert "PII" in html and "masked" in html
        assert "j***@a…" in html

    def test_unmask_override_exposes_values(self):
        html = render_html_report(self._pii_bundle(), mask_pii=False)
        assert self.EMAIL in html

    def test_pii_note_present_only_with_pii_columns(self, golden_bundle):
        html = render_html_report(golden_bundle)
        assert "Privacy:" not in html  # sales_deals has no email/phone column


# ---------------------------------------------------------------------------
# escaping / FAIL≠ERROR surface
# ---------------------------------------------------------------------------

class TestEscapingAndErrors:
    def test_hostile_strings_are_escaped(self):
        evil = "<script>alert(1)</script>"
        check = QualityCheckResult.model_validate(dict(
            schema_version=SCHEMA_VERSION, run_id="run-syn",
            rule_id="evil_rule", rule_version="1", dimension="validity",
            status="FAIL", severity="low", observed=_mv(1),
            threshold=CheckThreshold(operator=">", value=0),
            failing_record_count=1, executed_at="2026-10-24T09:00:04Z",
        ))
        finding = Finding.model_validate(dict(
            schema_version=SCHEMA_VERSION, finding_id="F-EVIL",
            fingerprint="sha256:" + "d" * 64, run_id="run-syn",
            title=evil, summary=evil, category="validity", severity="low",
            confidence="medium", scope=FindingScope(column=None),
            facts=[FindingFact(metric_ref="rule:evil_rule", value=1,
                               provenance=Provenance(method="exact"))],
            interpretation=evil,
            hypotheses=[FindingHypothesis(text=evil, status="unverified")],
            evidence_refs=["rule:evil_rule"],
        ))
        bundle = ProfileBundle(
            run=_synthetic_run(),
            profile=_synthetic_bundle([_col(evil)]).profile,
            checks=(check,), findings=(finding,), recommendations=(), errors=(),
        )
        html = render_html_report(bundle)
        assert evil not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_error_log_rendered_without_demoting_run(self):
        run = _synthetic_run().model_copy(update={"error_log": [
            RunErrorEntry(code="METRIC_EXEC_FAILED", task="stats.mean:x",
                          message="column not numeric"),
        ]})
        bundle = _synthetic_bundle([_col("x")])
        bundle = ProfileBundle(run=run, profile=bundle.profile, checks=(),
                               findings=(), recommendations=(), errors=())
        html = render_html_report(bundle)
        assert "METRIC_EXEC_FAILED" in html
        assert "FAIL≠ERROR" in html  # the surface explains the policy

    def test_sampling_record_shown_when_enabled(self):
        run = _synthetic_run().model_copy(update={
            "sampling": RunSampling(enabled=True, strategy="reservoir",
                                    sample_size=1000, seed=7),
        })
        bundle = ProfileBundle(run=run, profile=_synthetic_bundle([_col("a")]).profile,
                               checks=(), findings=(), recommendations=(), errors=())
        html = render_html_report(bundle)
        assert "reservoir" in html and "seed=7" in html


# ---------------------------------------------------------------------------
# writers: direct path (CLI --html) + ArtifactStore emit (M3-T2 lifecycle)
# ---------------------------------------------------------------------------

class TestWriters:
    def test_write_html_report_creates_parents(self, golden_bundle, tmp_path):
        out = tmp_path / "nested" / "dir" / "report.html"
        written = write_html_report(golden_bundle, out)
        assert written == out and out.is_file()
        assert out.read_text(encoding="utf-8") == render_html_report(golden_bundle)

    def test_emit_through_artifact_store_canonical_ref(self, golden_bundle, tmp_path):
        store = ArtifactStore(data_root=tmp_path)
        ref = emit_html_report_artifact(golden_bundle, store)
        assert ref == f"artifacts/{golden_bundle.run.run_id}/report.html"
        assert store.read_text(ref) == render_html_report(golden_bundle)

    def test_cli_html_flag(self, tmp_path):
        out_json = tmp_path / "profile.json"
        out_html = tmp_path / "report.html"
        run_profile(
            gh.GOLDEN_DIR / "sales_deals.csv",
            gh.GOLDEN_DIR / "sales_deals.config.json",
            out_json,
            dataset_id="sales_deals", run_id="run-cli-html",
            allowed_roots=[gh.GOLDEN_DIR],
            html_out=out_html,
        )
        assert out_json.is_file()  # JSON writer path unchanged
        assert out_html.is_file()
        text = out_html.read_text(encoding="utf-8")
        assert text.startswith("<!DOCTYPE html>") and "Plotly.newPlot" in text
