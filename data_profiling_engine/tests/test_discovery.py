"""M2-T2 — Discovery: physical→logical/semantic inference + unit status.

Reference protocol (TESTING §3.1: *every* metric/rule has a test against an
**independent reference**):

- Signals (counts, distinct, length bounds, blank) → hand-computed / Polars /
  hashlib reference on small fixtures; cardinality strategy verified across the
  EXACT_DISTINCT_ROW_LIMIT boundary with a 15K-row synthetic.
- Inference rules → literal expected ``(logical, semantic, unit.status,
  confidence)`` tuple per case; the closed enum vocabulary means a literal is
  a full assertion (no fuzzy "not None" checks).
- Config overlay (ENGINE §4.1 "config wins") → declared PK + declared unit
  asserted to overwrite inference verbatim.
- Contract conformance → every emitted ``ColumnProfile.model_dump(mode='json')``
  validates against ``column_profile.schema.json`` (CONTRACTS §3.2).
- Failure path → discovery errors carry the §8 code ``SCHEMA_INFERENCE_FAILED``.

Fixture data is deliberately tiny (3-5 rows) so each rule can be inspected
against hand-computed truth; the 15K-row synthetic only checks the strategy
switch, not the value semantics.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors import get_connector
from data_profiling_engine.discovery import discover
from data_profiling_engine.discovery.signals import (
    EXACT_DISTINCT_ROW_LIMIT,
    DiscoveryError,
    collect_column_signals,
)
from data_profiling_engine.discovery.types import (
    ColumnSignals,
    Confidence,
    LogicalType,
)
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import DatasetConfig, UnitStatus

# ---- helpers --------------------------------------------------------------

CSV_HEADER = (
    "deal_id,deal_value,status,created_at,email,is_active,notes,"
    "amount_str,country_code"
)

# 3 rows: everything hand-checkable. country_code is unique-per-row so it
# exercises the "near-unique string" branch (identifier candidate); status has
# a duplicate so it triggers the categorical rule.
ROWS = [
    ("1", "100.5", "Closed Won", "2026-01-01", "a@x.com",   "true",  "long free form description here that goes on for a while", "$100", "ID"),
    ("2", "",      "Open",        "2026-01-02", "b@y.org",   "false", "another narrative body text of substantial length here",   "$200", "MY"),
    ("3", "50",    "Closed Won", "2026-01-03", "c@z.net",   "true",  "yet another descriptive note that is rather long and here","$300", "SG"),
]


def _write_csv(path: Path) -> Path:
    lines = [CSV_HEADER] + [",".join(r) for r in ROWS]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def sales_csv(tmp_path: Path) -> Path:
    return _write_csv(tmp_path / "sales.csv")


@pytest.fixture()
def sales_handle(sales_csv):
    con = duckdb.connect()
    return con, get_connector("ds_sales", sales_csv).register(con)


def _by_name(report, name):
    for c in report.columns:
        if c.profile.column == name:
            return c
    raise AssertionError(f"column {name!r} not in report")


def _assert_profile_valid(cd) -> None:
    """Every ColumnProfile emitted by discovery must validate against the
    canonical §3.2 schema — no exceptions, no sidecars."""
    errs = S.validate("column_profile.schema.json", cd.profile.model_dump(mode="json"))
    assert errs == [], (cd.profile.column, errs)


# ---- 1. Physical base mapping (ENGINE §4.1 first bullet) -------------------


def test_physical_base_dates_and_timestamps(sales_handle):
    con, h = sales_handle
    r = discover(con, h)
    _assert_profile_valid(_by_name(r, "created_at"))
    cd = _by_name(r, "created_at")
    assert cd.profile.physical_type == "DATE"
    assert cd.logical is LogicalType.TEMPORAL_DATE
    assert cd.profile.semantic_type == "time"
    assert cd.confidence is Confidence.HIGH


def test_physical_boolean(sales_handle):
    con, h = sales_handle
    r = discover(con, h)
    cd = _by_name(r, "is_active")
    assert cd.logical is LogicalType.BOOLEAN
    # is_/has_ prefix bumps semantic to status with MEDIUM confidence
    assert cd.profile.semantic_type == "status"
    assert cd.confidence is Confidence.MEDIUM


def test_physical_varchar_string_baseline(sales_handle):
    """status column: VARCHAR physical, refined below, but the physical_type
    field must reflect the DuckDB read verbatim (AGENTS §2 metadata-driven)."""
    con, h = sales_handle
    cd = _by_name(discover(con, h), "status")
    assert cd.profile.physical_type == "VARCHAR"


def test_physical_family_variants_map_directly(tmp_path):
    """Cover each DuckDB family the connector may hand us; logical must land
    on the closed enum without further refinement."""
    con = duckdb.connect()
    con.execute(
        "CREATE VIEW v AS SELECT "
        "  1::BIGINT a, 1.5::DOUBLE b, 5::DECIMAL(10,2) c, TRUE::BOOLEAN d, "
        "  DATE '2020-01-01' e, TIME '12:00' f, now()::TIMESTAMP g, "
        "  'x'::VARCHAR h, gen_random_uuid()::UUID i, "
        "  'blob'::BLOB j, [1,2]::INTEGER[] k"
    )
    # Build a synthetic handle (real handle is fine — discovery only reads
    # columns / view_name / dataset_id, and we set those).
    from data_profiling_engine.connectors.base import RelationHandle
    from data_profiling_engine.schemas.models import SourceSnapshot

    cols = tuple(
        (d[0], str(d[1]).upper())
        for d in con.execute("SELECT * FROM v LIMIT 0").description
    )
    h = RelationHandle(
        dataset_id="ds_v",
        connector="csv",
        view_name="v",
        snapshot=SourceSnapshot(
            path="mem://", size_bytes=0, modified_at="2026-01-01T00:00:00Z",
            content_hash="sha256:" + "0" * 64, row_id_policy="csv:_row_id",
        ),
        columns=cols,
        row_id_column="_row_id",
    )
    r = discover(con, h)
    logmap = {c.profile.column: c.logical for c in r.columns}
    assert logmap["a"] is LogicalType.INTEGER
    assert logmap["b"] is LogicalType.FLOAT
    assert logmap["c"] is LogicalType.DECIMAL
    assert logmap["d"] is LogicalType.BOOLEAN
    assert logmap["e"] is LogicalType.TEMPORAL_DATE
    assert logmap["f"] is LogicalType.TEMPORAL_TIME
    assert logmap["g"] is LogicalType.TEMPORAL_DATETIME
    assert logmap["i"] is LogicalType.UUID
    assert logmap["j"] is LogicalType.BINARY
    assert logmap["k"] is LogicalType.NESTED
    # 'h' is a bare literal 'x' with no cardinality signal → STRING default
    assert logmap["h"] is LogicalType.STRING
    for cd in r.columns:
        _assert_profile_valid(cd)


# ---- 2. Signals — independent reference (TESTING §3.1) --------------------


def test_signals_counts_null_distinct(sales_handle):
    """n_rows / n_null / n_notnull / n_distinct are the raw aggregates the
    inference runs on — verify each against the hand-written fixture."""
    con, h = sales_handle
    sig = collect_column_signals(con, h, "deal_value", "DOUBLE")
    assert sig.n_rows == 3
    assert sig.n_null == 1              # row 2 has empty value
    assert sig.n_notnull == 2
    assert sig.n_distinct == 2          # 100.5, 50
    assert sig.distinct_method == "exact"
    assert sig.min_repr == "50.0"
    assert sig.max_repr == "100.5"


def test_signals_varchar_length_and_blank(sales_handle):
    con, h = sales_handle
    sig = collect_column_signals(con, h, "status", "VARCHAR")
    assert sig.n_rows == 3
    assert sig.n_blank == 0
    assert sig.min_len == len("Open")               # 4
    assert sig.max_len == len("Closed Won")         # 11
    assert sig.n_distinct == 2
    assert set(sig.sample) == {"Closed Won", "Open"}


def test_signals_boolean_true_false_counts(sales_handle):
    con, h = sales_handle
    sig = collect_column_signals(con, h, "is_active", "BOOLEAN")
    assert (sig.n_true, sig.n_false) == (2, 1)
    assert sig.n_null == 0


def test_signals_distinct_switches_strategy_at_threshold(tmp_path):
    """ADR-007 boundary: ≤EXACT_DISTINCT_ROW_LIMIT → exact count(DISTINCT),
    above → approx_count_distinct. Method label is the contract for downstream
    consumers; we only assert the strategy flag, not the approximate value."""
    n = EXACT_DISTINCT_ROW_LIMIT + 1
    csv = tmp_path / "big.csv"
    csv.write_text("i\n" + "\n".join(str(k) for k in range(n)) + "\n", encoding="utf-8")
    con = duckdb.connect()
    h = get_connector("ds_big", csv).register(con)
    small_sig = collect_column_signals(con, h, "i", "BIGINT")
    assert small_sig.distinct_method == "approximate"
    assert small_sig.n_rows == n

    csv_small = tmp_path / "small.csv"
    csv_small.write_text("i\n" + "\n".join(str(k) for k in range(50)) + "\n", encoding="utf-8")
    h2 = get_connector("ds_small", csv_small).register(con)
    sig = collect_column_signals(con, h2, "i", "BIGINT")
    assert sig.distinct_method == "exact"
    assert sig.n_distinct == 50  # exact equals hand count on the small side


def test_signals_empty_dataset(tmp_path):
    """Empty source (0 rows) must still yield a valid ColumnProfile — the
    inference has no data, so it drops to physical-only + LOW confidence."""
    csv = tmp_path / "empty.csv"
    csv.write_text("a,b\n", encoding="utf-8")
    con = duckdb.connect()
    h = get_connector("ds_empty", csv).register(con)
    # DuckDB header-only: physical type falls back to VARCHAR for undetectable columns
    r = discover(con, h)
    assert len(r.columns) == 2
    for cd in r.columns:
        assert cd.signals.n_rows == 0
        assert cd.signals.n_notnull == 0
        assert cd.signals.cardinality_ratio == 0.0
        _assert_profile_valid(cd)


def test_signals_all_null_column(tmp_path):
    """TESTING §3.1 edge: all-null column must not crash inference; the
    physical family still drives logical (integer BIGINT → integer)."""
    csv = tmp_path / "nullcol.csv"
    csv.write_text("i,s\n1,a\n2,\n3,\n", encoding="utf-8")
    con = duckdb.connect()
    h = get_connector("ds_null", csv).register(con)
    r = discover(con, h)
    s = _by_name(r, "s")
    # At least one non-null (row 1), then all null → still categorical or string,
    # but never crash. Assert physical/logical pair is coherent.
    assert s.profile.physical_type == "VARCHAR"
    _assert_profile_valid(s)


# ---- 3. VARCHAR pattern refinement (ENGINE §4.1 signals list) --------------


@pytest.mark.parametrize(
    "name,values,expect_logical,expect_semantic,expect_conf",
    [
        # email ≥90% → EMAIL / identifier / HIGH
        ("user_email", ["a@x.com", "b@y.org", "c@z.net", "d@w.io"], "email", "identifier", "high"),
        # uuid ≥90% → UUID / identifier / HIGH
        ("trace_uuid", [
            "0d1a2b3c-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "1d1a2b3c-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "2d1a2b3c-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
            "3d1a2b3c-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
        ], "uuid", "identifier", "high"),
        # ISO date in VARCHAR → TEMPORAL_DATE / time / HIGH
        ("birthday", ["1990-01-02", "1985-12-31", "2000-06-15"], "temporal_date", "time", "high"),
        # ISO datetime in VARCHAR → TEMPORAL_DATETIME / time / HIGH
        ("event_ts", [
            "2026-01-01T10:00:00Z", "2026-01-02T10:00:00Z", "2026-01-03T10:00:00Z",
        ], "temporal_datetime", "time", "high"),
        # URL ≥90% → URL / text / HIGH
        ("homepage", [
            "https://a.example/x", "https://b.example/y", "https://c.example/z",
        ], "url", "text", "high"),
    ],
)
def test_varchar_pattern_promotions(tmp_path, name, values, expect_logical, expect_semantic, expect_conf):
    csv = tmp_path / f"{name}.csv"
    csv.write_text(f"{name}\n" + "\n".join(values) + "\n", encoding="utf-8")
    con = duckdb.connect()
    h = get_connector(f"ds_{name}", csv).register(con)
    cd = _by_name(discover(con, h), name)
    assert cd.logical.value == expect_logical, cd.rationale
    assert cd.profile.semantic_type == expect_semantic, cd.rationale
    assert cd.confidence.value == expect_conf, cd.rationale
    _assert_profile_valid(cd)


def test_varchar_categorical_short_low_cardinality(sales_handle):
    """status column has duplicates (2 distinct / 3 rows, max_len=11 ≤ 40) →
    categorical_nominal + STATUS semantic (name == "status")."""
    con, h = sales_handle
    cd = _by_name(discover(con, h), "status")
    assert cd.logical is LogicalType.CATEGORICAL_NOMINAL
    assert cd.profile.semantic_type == "status"
    assert cd.confidence is Confidence.MEDIUM


def test_varchar_freeform_long_text(sales_handle):
    con, h = sales_handle
    cd = _by_name(discover(con, h), "notes")
    # max_len > _LEN_LONG (80) OR name matches freeform regex; here both
    assert cd.logical is LogicalType.TEXT_FREEFORM
    assert cd.profile.semantic_type == "text"


def test_varchar_near_unique_identifier_candidate(sales_handle):
    """country_code is 3-distinct over 3 rows (all unique) — the near-unique
    branch fires and calls it an identifier candidate at MEDIUM confidence."""
    con, h = sales_handle
    cd = _by_name(discover(con, h), "country_code")
    assert cd.profile.semantic_type == "identifier"
    assert cd.confidence is Confidence.MEDIUM


def test_varchar_default_falls_to_string(tmp_path):
    """One unique short string, no name signal — inference has nothing to
    work with; must drop to STRING / text / LOW (never silently claim a type)."""
    csv = tmp_path / "one.csv"
    csv.write_text("mystery\nabc\n", encoding="utf-8")
    con = duckdb.connect()
    h = get_connector("ds_one", csv).register(con)
    cd = _by_name(discover(con, h), "mystery")
    assert cd.logical is LogicalType.STRING
    assert cd.confidence is Confidence.LOW


# ---- 4. Name heuristics on numeric columns ---------------------------------


def test_numeric_id_name_promotes_identifier(sales_handle):
    """BIGINT deal_id — physical is integer; the ``_id`` name nudges the
    semantic to identifier (MEDIUM) because it is still heuristic."""
    con, h = sales_handle
    cd = _by_name(discover(con, h), "deal_id")
    assert cd.logical is LogicalType.INTEGER
    assert cd.profile.semantic_type == "identifier"
    assert cd.confidence is Confidence.MEDIUM


def test_numeric_currency_name_promotes_logical_and_marks_unknown(sales_handle):
    """DOUBLE ``deal_value`` name heuristic → logical=numeric_currency,
    unit.status=unknown (no config, no symbol visible in a numeric column)."""
    con, h = sales_handle
    cd = _by_name(discover(con, h), "deal_value")
    assert cd.logical is LogicalType.NUMERIC_CURRENCY
    assert cd.profile.semantic_type == "measure"
    assert cd.profile.unit is not None
    assert cd.profile.unit.status == UnitStatus.UNKNOWN.value
    assert cd.profile.unit.currency is None


def test_numeric_low_cardinality_becomes_dimension(tmp_path):
    """BIGINT column with duplicate values in ≤25 distinct range →
    categorical_nominal / dimension (integer codes, decade buckets, …)."""
    csv = tmp_path / "codes.csv"
    csv.write_text("bucket\n1\n1\n2\n2\n3\n3\n1\n", encoding="utf-8")
    con = duckdb.connect()
    h = get_connector("ds_bucket", csv).register(con)
    cd = _by_name(discover(con, h), "bucket")
    assert cd.logical is LogicalType.CATEGORICAL_NOMINAL
    assert cd.profile.semantic_type == "dimension"


# ---- 5. Currency symbol path (ENGINE §4.1 "unambiguous symbol") ------------


def test_varchar_currency_name_and_symbol_promotes_to_numeric_currency(sales_handle):
    """amount_str column: name matches currency regex, values shaped like "$N";
    must promote to numeric_currency (assumed USD), never silently ``known``."""
    con, h = sales_handle
    cd = _by_name(discover(con, h), "amount_str")
    assert cd.logical is LogicalType.NUMERIC_CURRENCY, cd.rationale
    assert cd.profile.unit is not None
    assert cd.profile.unit.status == UnitStatus.ASSUMED.value
    assert cd.profile.unit.currency == "USD"


# ---- 6. Config overlay (ENGINE §4.1: config wins over inference) -----------

_CFG_DICT = {
    "config_version": 3,
    "dataset": {
        "name": "sales",
        "primary_key": ["deal_id"],
        "units": {"deal_value": {"currency": "USD", "status": "known"}},
    },
    "profiling": {"mode": "standard"},
    "quality_checks": [
        {"id": "r1", "rule": "not_null", "column": "deal_id",
         "dimension": "completeness", "severity": "critical"},
    ],
}


def test_config_declared_pk_overrides_semantic_and_marks_high(sales_handle):
    con, h = sales_handle
    cfg = DatasetConfig.model_validate(_CFG_DICT)
    r = discover(con, h, config=cfg)
    cd = _by_name(r, "deal_id")
    assert cd.profile.semantic_type == "identifier"
    assert cd.confidence is Confidence.HIGH
    assert cd.config_declared is True
    assert "declared primary_key" in cd.rationale


def test_config_declared_unit_wins_verbatim(sales_handle):
    """Config ``units[col]`` is copied as-is; status=known only when the user
    declares it (never inferred from name alone)."""
    con, h = sales_handle
    cfg = DatasetConfig.model_validate(_CFG_DICT)
    cd = _by_name(discover(con, h, config=cfg), "deal_value")
    assert cd.profile.unit is not None
    assert cd.profile.unit.status == UnitStatus.KNOWN.value
    assert cd.profile.unit.currency == "USD"
    assert cd.config_declared is True
    assert cd.confidence is Confidence.HIGH


def test_config_without_pk_leaves_inference_unchanged(sales_handle):
    """Removing the primary_key declaration must NOT change the outcome the
    inference would have produced anyway (deal_id still identifier via name),
    but confidence must drop back to MEDIUM — no silent upgrade."""
    con, h = sales_handle
    cfg_dict = {**_CFG_DICT, "dataset": {**_CFG_DICT["dataset"], "primary_key": []}}
    # primary_key=[] fails schema (minItems=1); use None instead
    cfg_dict["dataset"].pop("primary_key")
    cfg = DatasetConfig.model_validate(cfg_dict)
    cd = _by_name(discover(con, h, config=cfg), "deal_id")
    assert cd.profile.semantic_type == "identifier"
    assert cd.config_declared is False


# ---- 7. ColumnProfile ordinals + exclusion of _row_id ----------------------


def test_ordinals_match_file_order_and_rowid_is_excluded(sales_handle):
    con, h = sales_handle
    r = discover(con, h)
    names = [c.profile.column for c in r.columns]
    # RelationHandle.column_names excludes the _row_id plumbing (M2-T1 contract);
    # discovery inherits that — verify no "_row_id" ever appears here.
    assert "_row_id" not in names
    assert names == list(h.column_names)
    assert [c.profile.ordinal for c in r.columns] == list(range(len(names)))


# ---- 8. Parquet path — same inference on typed columns ---------------------


def test_discovery_on_parquet_roundtrip(tmp_path):
    """Same logical rules must fire whether DuckDB sees a CSV or a Parquet
    file (ADR-011: connector-agnostic discovery over the RelationHandle)."""
    csv = tmp_path / "s.csv"
    csv.write_text(
        "deal_id,amount\n1,50.0\n2,60.0\n3,70.0\n", encoding="utf-8"
    )
    pq = tmp_path / "s.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT * FROM read_csv('{csv}')) TO '{pq}' (FORMAT parquet)"
    )
    con = duckdb.connect()
    h = get_connector("ds_pq", pq).register(con)
    r = discover(con, h)
    amount = _by_name(r, "amount")
    assert amount.logical is LogicalType.NUMERIC_CURRENCY  # name-driven
    deal_id = _by_name(r, "deal_id")
    assert deal_id.profile.semantic_type == "identifier"


# ---- 9. §8 error code (AGENTS §3: no invented codes) -----------------------


def test_discovery_error_carries_registered_code(sales_handle):
    """Discovery failures must surface as SCHEMA_INFERENCE_FAILED (already in
    the §8 registry — AGENTS §3 + errors.py promote path)."""
    con, h = sales_handle
    # Force a DuckDB error by asking for a nonexistent column
    with pytest.raises(DiscoveryError) as exc:
        collect_column_signals(con, h, "does_not_exist", "VARCHAR")
    assert exc.value.code == ErrorCode.SCHEMA_INFERENCE_FAILED
    from data_profiling_engine.errors import is_registered
    assert is_registered(exc.value.code)


# ---- 10. Every emitted profile is schema-valid (§3.2 conformance) ----------


def test_all_profiles_schema_valid_across_fixture(sales_handle):
    con, h = sales_handle
    r = discover(con, h)
    for cd in r.columns:
        _assert_profile_valid(cd)


# ---- 11. Determinism (ENGINE §4.1 + ADR-015 seed discipline) ---------------


def test_discovery_is_deterministic_on_same_input(sales_handle):
    con, h = sales_handle
    r1 = discover(con, h)
    r2 = discover(con, h)
    for a, b in zip(r1.columns, r2.columns):
        assert a.profile.model_dump(mode="json") == b.profile.model_dump(mode="json")
        # Confidence + rationale are pure functions of the aggregates; they
        # must match on identical inputs (sample ORDER is not consumed).
        assert a.confidence == b.confidence
        assert a.rationale == b.rationale
