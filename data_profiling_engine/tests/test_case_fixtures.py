"""Case-fixture regression tests — a parametrized pass over the four datasets
under ``tests/fixtures/cases/``, each run through the headless ``profiler-cli``
(``cli.run_profile``, the same spine ``profiler-cli profile`` calls) and asserted
against the engine behavior the fixture was authored to exercise.

Unlike the golden corpus (``fixtures/golden/`` pins *exact bytes*), these cases
pin *behavioral outcomes* — logical-type refinement, quality-check status,
finding categories, method provenance — so a small metric shift does not break
them, but a silent loss of the covered code path does.

Coverage map (one fixture = one distinct engine path):
* ``clean_orders``     — happy path: every §9 quality check PASS; config-declared
                         unit drives ``numeric_currency`` known/USD (ENGINE §9).
* ``dirty_contacts``   — PII pattern refinement (email/phone logical types),
                         near-total dominant-category WARN finding, sentinel +
                         completeness FAILs (ADR-017, findings detectors).
* ``outliers_spend``   — ``business_note`` downgrades a would-be FAIL to WARN,
                         ``outlier_scan`` Tukey WARN, symbol-shaped VARCHAR
                         inferred ``numeric_currency`` assumed/USD.
* ``sampled_events``   — reservoir sampling degrades a heavy metric
                         (``distinct_count``) to ``method="sampled"`` carrying the
                         config ``seed`` (ADR-015 honesty).

Adding a case = drop ``<name>.csv`` + ``<name>.config.json`` in the dir and a
named ``_assert_*`` into ``CASE_ASSERTIONS``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.cli import run_profile
from data_profiling_engine.reporting import RESULT_DOC_KEYS

CASES_DIR = Path(__file__).resolve().parent / "fixtures" / "cases"


# ---------------------------------------------------------------------------
# harness: run one case through profiler-cli and hand back the result document
# ---------------------------------------------------------------------------
def _run_case(name: str, tmp_path: Path) -> Dict[str, Any]:
    """Invoke the profiler-cli spine (``cli.run_profile``) on ``<name>.csv`` +
    ``<name>.config.json`` from the read-only cases dir, then read back the
    canonical JSON it wrote. Also asserts the emitted document is contract-valid
    (every block against its JSON Schema) — the same gate the golden corpus
    enforces, so a fixture can never regress into emitting an off-contract doc.
    """
    src = CASES_DIR / f"{name}.csv"
    cfg = CASES_DIR / f"{name}.config.json"
    assert src.is_file(), f"missing case fixture: {src}"
    assert cfg.is_file(), f"missing case config: {cfg}"

    out = tmp_path / f"{name}.json"
    written = run_profile(src, cfg, out, allowed_roots=[CASES_DIR])
    assert written == out and out.is_file()

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert set(doc) == set(RESULT_DOC_KEYS)
    assert S.validate("profiling_run.schema.json", doc["run"]) == []
    assert S.validate("dataset_profile.schema.json", doc["profile"]) == []
    for col in doc["profile"]["columns"]:
        assert S.validate("column_profile.schema.json", col) == []
    for check in doc["checks"]:
        assert S.validate("quality_check_result.schema.json", check) == []
    for finding in doc["findings"]:
        assert S.validate("finding.schema.json", finding) == []
    for rec in doc["recommendations"]:
        assert S.validate("recommendation.schema.json", rec) == []
    return doc


def _columns(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {c["column"]: c for c in doc["profile"]["columns"]}


def _check(doc: Dict[str, Any], rule_id: str) -> Dict[str, Any]:
    for c in doc["checks"]:
        if c["rule_id"] == rule_id:
            return c
    raise AssertionError(f"no check with rule_id {rule_id!r} in case")


# ---------------------------------------------------------------------------
# per-case intended outcomes
# ---------------------------------------------------------------------------
def _assert_clean_orders(doc: Dict[str, Any]) -> None:
    # the happy path: every declared quality check passes (no FAIL/WARN/ERROR).
    assert doc["checks"], "clean_orders should run its §9 checks"
    statuses = {c["rule_id"]: c["status"] for c in doc["checks"]}
    assert set(statuses) == {
        "order_id_unique", "quantity_not_null", "status_domain",
        "quantity_range", "unit_price_positive",
    }
    assert all(s == "PASS" for s in statuses.values()), statuses
    # config-declared unit drives the currency semantic + known/USD unit status.
    unit_price = _columns(doc)["unit_price"]
    assert unit_price["logical_type"] == "numeric_currency"
    assert unit_price["unit"] == {"status": "known", "currency": "USD"}


def _assert_dirty_contacts(doc: Dict[str, Any]) -> None:
    cols = _columns(doc)
    # PII pattern refinement: email/phone columns get their dedicated logical
    # types (not the generic string), from >=3 distinct pattern-matching values.
    assert cols["email"]["logical_type"] == "email"
    assert cols["phone"]["logical_type"] == "phone"
    # completeness rules on the fields with real nulls FAIL.
    assert _check(doc, "email_present")["status"] == "FAIL"
    assert _check(doc, "phone_present")["status"] == "FAIL"
    # dominant-category finding: 'source' is constant → a near-total
    # single-category WARN (findings.detectors DOMINANT_CATEGORY_SHARE).
    dominant = [
        f for f in doc["findings"]
        if f["category"] == "accuracy_flagged"
        and f["scope"].get("column") == "source"
        and "single category" in f["title"]
    ]
    assert dominant, "expected a dominant-category WARN finding on 'source'"


def _assert_outliers_spend(doc: Dict[str, Any]) -> None:
    # business_note downgrades a would-be FAIL to a WARN (ENGINE §5.3).
    assert _check(doc, "gross_non_negative")["status"] == "WARN"
    # outlier_scan flags the extreme gross value(s) as a WARN.
    assert _check(doc, "gross_outliers")["status"] == "WARN"
    # symbol-shaped VARCHAR amount is inferred numeric_currency; unit status is
    # 'assumed' (currency symbol heuristic) with USD, since config didn't declare it.
    amount = _columns(doc)["amount_usd"]
    assert amount["logical_type"] == "numeric_currency"
    assert amount["unit"] == {"status": "assumed", "currency": "USD"}


def _assert_sampled_events(doc: Dict[str, Any]) -> None:
    # heavy metric is honestly degraded to sampled, carrying the config seed
    # (ADR-015); reservoir sample_size/strategy are recorded in method_params.
    dc = _columns(doc)["event_id"]["uniqueness"]["distinct_count"]
    prov = dc["provenance"]
    assert prov["method"] == "sampled"
    assert prov["method_params"]["seed"] == 123
    # the run block records sampling as enabled with the same seed.
    sampling = doc["run"]["sampling"]
    assert sampling["enabled"] is True
    assert sampling["seed"] == 123


CASE_ASSERTIONS: Dict[str, Callable[[Dict[str, Any]], None]] = {
    "clean_orders": _assert_clean_orders,
    "dirty_contacts": _assert_dirty_contacts,
    "outliers_spend": _assert_outliers_spend,
    "sampled_events": _assert_sampled_events,
}


# ---------------------------------------------------------------------------
# the parametrized test
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case_name", list(CASE_ASSERTIONS))
def test_case_fixture_behaves_as_intended(case_name: str, tmp_path: Path) -> None:
    """Run ``<case_name>`` through profiler-cli and assert the specific engine
    path it was authored to cover (see the module docstring's coverage map)."""
    doc = _run_case(case_name, tmp_path)
    CASE_ASSERTIONS[case_name](doc)
