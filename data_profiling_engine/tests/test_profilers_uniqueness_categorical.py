"""M2-T5 reference tests — uniqueness + categorical profilers.

TESTING §3.1: every executed metric is asserted against an **independent
reference** (hand-computed literals + Polars), never against the engine's own
SQL. The 12-row fixture below is hand-checkable; coverage:

- Uniqueness.distinct_count exact vs Polars ``n_unique`` on the whole relation
  (small-N STANDARD mode).
- Uniqueness.is_unique_candidate derived from distinct == non-null on
  identifier + duplicate columns + an all-null column.
- Uniqueness.duplicate_groups auxiliary (contract_target=None) vs hand count
  of DISTINCT values with freq > 1.
- HLL approximate path: n_rows > LARGE_ROW_LIMIT flips distinct_count to
  method=approximate + algorithm='HLL' (§2.2, ADR-015); value stays within
  documented HLL error bounds of the exact count.
- Sampled path: distinct_count sampled with seed (bounded relation),
  deterministic across reruns.
- Categorical.frequencies top-K by count desc / cat asc vs Polars group_by.
- Categorical.rare_categories threshold = max(1, floor(0.001 * non_null)).
- Categorical.label_inconsistencies: same lower(trim) with ≥2 raw variants.
- Low-cardinality gate: a >CATEGORICAL_MAX_DISTINCT column omits the whole
  categorical block (§4 gate-1 reads uniqueness.distinct_count).
- §6.4 partial-failure: an errored uniqueness metric does not abort siblings.
- Every emitted ColumnProfile validates against the frozen §3.2 schema and a
  rerun is byte-stable.

HAND fixture (12 rows, id/amount/status):

  idx  id  amount  status
    1   1     10   'Active'
    2   2     20   'Active'
    3   3     30   'active'
    4   4     40   'ACTIVE'
    5   5     10   'Closed'
    6   6     20   'Closed'
    7   7     30   'Pending'
    8   8     40   'pending'
    9   9     50   'N/A'
   10  10     60   ''
   11  11   NULL   NULL
   12  12     50   'Shut Down'

id:      distinct 12, non_null 12 → is_unique_candidate True, dup groups 0.
amount:  distinct 6 {10,20,30,40,50,60}, non_null 11 → False, dup groups 5
         ({10,20,30,40,50} appear 2x each).
status:  distinct 9 (raw, non-null)
         {Active, active, ACTIVE, Closed, Pending, pending, N/A, '', Shut Down},
         non_null 11 → False, dup groups 2 ({Active x2, Closed x2}).
         Frequencies desc/asc: Active(2), Closed(2), ''(1), ACTIVE(1), N/A(1),
         Pending(1), Shut Down(1), active(1), pending(1).
         Rare (threshold = max(1, floor(11*0.001)) = 1): every count==1
         category → 7 rare.
         Label inconsistency groups: 'active'→{Active,active,ACTIVE} 3 variants;
         'pending'→{Pending,pending} 2 variants.
"""
from __future__ import annotations

from typing import List, Optional

import duckdb
import polars as pl
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery import discover
from data_profiling_engine.engine.metrics import (
    LARGE_ROW_LIMIT,
    MetricRegistry,
    default_registry,
)
from data_profiling_engine.engine.metrics.profiler import (
    CATEGORICAL_MAX_DISTINCT,
    RARE_FREQUENCY_SHARE,
    execute_uniqueness_and_categorical,
)
from data_profiling_engine.engine.planner import plan_execution
from data_profiling_engine.errors import ErrorCode
from data_profiling_engine.schemas.models import (
    DatasetConfig,
    MetricMethod,
    ProfileMode,
    SourceSnapshot,
)

# --- fixture rows ----------------------------------------------------------

_ROWS = [
    (1, 10, "Active"),
    (2, 20, "Active"),
    (3, 30, "active"),
    (4, 40, "ACTIVE"),
    (5, 10, "Closed"),
    (6, 20, "Closed"),
    (7, 30, "Pending"),
    (8, 40, "pending"),
    (9, 50, "N/A"),
    (10, 60, ""),
    (11, None, None),
    (12, 50, "Shut Down"),
]

# Hand-checked truth
STATUS_DISTINCT = 9            # raw non-null distinct values (incl. '')
STATUS_NON_NULL = 11           # 12 rows − 1 NULL
AMOUNT_DISTINCT = 6            # {10,20,30,40,50,60}
ID_DISTINCT = 12

STATUS_FREQ_ORDER = [
    # (category, count), ordered by count DESC then cat ASC
    ("Active", 2), ("Closed", 2), ("", 1), ("ACTIVE", 1), ("N/A", 1),
    ("Pending", 1), ("Shut Down", 1), ("active", 1), ("pending", 1),
]
STATUS_RARE = [c for c, n in STATUS_FREQ_ORDER if n == 1]  # threshold is 1 here


def _snapshot() -> SourceSnapshot:
    return SourceSnapshot(content_hash="sha256:" + "1" * 64)


def _seed_values(rows) -> str:
    parts = []
    for i, a, s in rows:
        a_lit = "NULL" if a is None else str(a)
        s_lit = "NULL" if s is None else "'" + s.replace("'", "''") + "'"
        parts.append(f"({i},{a_lit},{s_lit})")
    return ",".join(parts)


def _handle(con: duckdb.DuckDBPyConnection, view: str = "src",
            rows=None) -> RelationHandle:
    rows = rows if rows is not None else _ROWS
    con.execute(
        f'CREATE OR REPLACE VIEW "{view}" AS '
        f"SELECT CAST(v0 AS BIGINT) AS id, CAST(v1 AS BIGINT) AS amount, "
        f"CAST(v2 AS VARCHAR) AS status, row_number() OVER () AS _row_id "
        f"FROM (VALUES {_seed_values(rows)}) t(v0, v1, v2)"
    )
    return RelationHandle(
        dataset_id="ds", connector="manual", view_name=view, snapshot=_snapshot(),
        columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
        row_id_column="_row_id",
    )


def _config(mode: str = "standard", sampling: Optional[dict] = None,
            sections: Optional[List[str]] = None) -> DatasetConfig:
    profiling: dict = {"mode": mode}
    if sampling:
        profiling["sampling"] = sampling
    if sections:
        profiling["sections"] = sections
    return DatasetConfig.model_validate({
        "config_version": 3,
        "dataset": {"name": "ds"},
        "profiling": profiling,
        "quality_checks": [{"id": "r", "rule": "not_null", "column": "id",
                            "dimension": "completeness", "severity": "high"}],
    })


def _run(con, h, cfg, mode=ProfileMode.STANDARD, registry=None, n_rows=12,
         sampling=None, sections=None):
    report = discover(con, h, cfg)
    plan = plan_execution(
        report, mode, n_rows,
        sampling if sampling is not None else (cfg.profiling.sampling if cfg else None),
        sections if sections is not None else (cfg.profiling.sections if cfg else None),
        registry or default_registry(),
    )
    return execute_uniqueness_and_categorical(con, h, report, plan, cfg, registry)


def _by_name(profiles, name):
    for p in profiles:
        if p.column == name:
            return p
    raise AssertionError(f"column {name!r} missing")


@pytest.fixture
def con_h():
    con = duckdb.connect()
    h = _handle(con)
    yield con, h
    con.close()


# ---------------------------------------------------------------------------
# Uniqueness — exact path, small N, STANDARD mode
# ---------------------------------------------------------------------------


class TestUniquenessExact:
    def test_distinct_count_matches_hand(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        assert res.errors == ()
        assert _by_name(res.profiles, "id").uniqueness.distinct_count.value == ID_DISTINCT
        assert _by_name(res.profiles, "amount").uniqueness.distinct_count.value == AMOUNT_DISTINCT
        assert _by_name(res.profiles, "status").uniqueness.distinct_count.value == STATUS_DISTINCT

    def test_distinct_count_matches_polars_independent(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        s = pl.Series("status", [r[2] for r in _ROWS])
        # Polars n_unique counts NULL as a distinct value; SQL count(DISTINCT) excludes it
        sql_val = _by_name(res.profiles, "status").uniqueness.distinct_count.value
        assert sql_val == s.drop_nulls().n_unique() == STATUS_DISTINCT
        a = pl.Series("amount", [r[1] for r in _ROWS])
        assert _by_name(res.profiles, "amount").uniqueness.distinct_count.value == a.drop_nulls().n_unique()

    def test_exact_provenance_no_method_params(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        for col in ("id", "amount", "status"):
            mv = _by_name(res.profiles, col).uniqueness.distinct_count
            assert mv.provenance.method == MetricMethod.EXACT.value
            assert "method_params" not in mv.provenance.model_dump()

    def test_is_unique_candidate_true_for_identifier_false_for_duplicates(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        assert _by_name(res.profiles, "id").uniqueness.is_unique_candidate is True
        assert _by_name(res.profiles, "amount").uniqueness.is_unique_candidate is False
        assert _by_name(res.profiles, "status").uniqueness.is_unique_candidate is False


class TestUniquenessAllNull:
    def test_all_null_column_distinct_zero_unique_vacuously_true(self):
        """0 == 0 is an honest derivation (an all-null column has no observed
        duplicate key; Missingness owns the NULL semantics, Uniqueness just
        reports no evidence of collision)."""
        con = duckdb.connect()
        con.execute(
            'CREATE VIEW "srcn" AS SELECT CAST(v0 AS BIGINT) AS id, '
            "CAST(v1 AS BIGINT) AS amount, CAST(v2 AS VARCHAR) AS status, "
            "row_number() OVER () AS _row_id FROM (VALUES "
            "(1,NULL,NULL),(2,NULL,NULL),(3,NULL,NULL)) t(v0,v1,v2)"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srcn",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        res = _run(con, h, _config(), n_rows=3)
        amount = _by_name(res.profiles, "amount").uniqueness
        assert amount.distinct_count.value == 0
        assert amount.is_unique_candidate is True
        con.close()


# ---------------------------------------------------------------------------
# Duplicate groups auxiliary (contract_target=None — M2-T8 signal, never lands
# on ColumnProfile)
# ---------------------------------------------------------------------------


class TestDuplicateGroupsAuxiliary:
    def test_counts_match_hand_reference(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        # id: no duplicates, amount: {10,20,30,40,50} each appear twice → 5,
        # status: {Active x2, Closed x2} → 2 (others singletons, NULL excluded)
        assert res.duplicate_group_counts == {"id": 0, "amount": 5, "status": 2}

    def test_duplicate_groups_never_appears_on_column_profile(self, con_h):
        """§3.2 Closed Uniqueness model has only distinct_count + is_unique_candidate."""
        con, h = con_h
        res = _run(con, h, _config())
        for p in res.profiles:
            dumped = p.uniqueness.model_dump() if p.uniqueness else {}
            assert "duplicate_groups" not in dumped


# ---------------------------------------------------------------------------
# HLL approximate path (§2.2 + ADR-015 labelling)
# ---------------------------------------------------------------------------


class TestHLLApproximate:
    def test_large_n_switches_distinct_to_approximate_hll(self, con_h):
        con, h = con_h
        # pretend relation is large; planner gates on n_rows > LARGE_ROW_LIMIT
        assert LARGE_ROW_LIMIT >= 100
        res = _run(con, h, _config(), n_rows=LARGE_ROW_LIMIT + 1)
        for col in ("id", "amount", "status"):
            mv = _by_name(res.profiles, col).uniqueness.distinct_count
            assert mv.provenance.method == MetricMethod.APPROXIMATE.value
            assert mv.provenance.method_params["algorithm"] == "HLL"

    def test_hll_estimate_within_documented_error_bounds(self, con_h):
        """DuckDB HLL is ~1-2 % error on small cardinalities; on our 12-row
        fixture with 9 distinct we accept abs tolerance of 5 (loose but still
        a meaningful assertion — TESTING §3.1 approx-within-bounds)."""
        con, h = con_h
        res = _run(con, h, _config(), n_rows=LARGE_ROW_LIMIT + 1)
        # HLL over tiny relations returns the exact value in practice (DuckDB
        # still sketches); assert close-to-truth, labelled approximate.
        for col, truth in (("id", ID_DISTINCT), ("amount", AMOUNT_DISTINCT),
                           ("status", STATUS_DISTINCT)):
            mv = _by_name(res.profiles, col).uniqueness.distinct_count
            assert abs(int(mv.value) - truth) <= 5

    def test_is_unique_candidate_still_derives_from_hll_value(self, con_h):
        """Boolean has no provenance carrier (CONTRACTS §3.2) — honesty rides
        on the underlying distinct_count MetricValue's method label."""
        con, h = con_h
        res = _run(con, h, _config(), n_rows=LARGE_ROW_LIMIT + 1)
        id_uq = _by_name(res.profiles, "id").uniqueness
        assert id_uq.distinct_count.provenance.method == MetricMethod.APPROXIMATE.value
        # exact 12 vs HLL 12 → True still
        assert id_uq.is_unique_candidate is True


# ---------------------------------------------------------------------------
# Sampled path (bounded relation + seed)
# ---------------------------------------------------------------------------


class TestSampledPath:
    def _sampling_cfg(self, seed=20261024):
        return {"enabled": True, "strategy": "reservoir", "size": 6, "seed": seed}

    def test_distinct_count_sampled_carries_seed(self):
        con = duckdb.connect()
        h = _handle(con)
        cfg = _config(sampling=self._sampling_cfg())
        res = _run(con, h, cfg)
        mv = _by_name(res.profiles, "status").uniqueness.distinct_count
        # EXPENSIVE_EXACT + sampling_on (non-deep) → SAMPLED
        assert mv.provenance.method == MetricMethod.SAMPLED.value
        assert mv.provenance.method_params["seed"] == 20261024
        assert res.sample_view is not None
        # bounded relation ≤ size
        n = con.execute(f'SELECT count(*) FROM "{res.sample_view}"').fetchone()[0]
        assert n <= 6
        con.close()

    def test_sampled_is_deterministic_same_seed(self):
        vals = []
        for _ in range(2):
            con = duckdb.connect()
            h = _handle(con)
            cfg = _config(sampling=self._sampling_cfg(seed=7))
            res = _run(con, h, cfg)
            vals.append(_by_name(res.profiles, "status").uniqueness.distinct_count.value)
            con.close()
        assert vals[0] == vals[1]


# ---------------------------------------------------------------------------
# Categorical: frequencies / rare / label inconsistencies
# ---------------------------------------------------------------------------


class TestCategoricalFrequencies:
    def test_top_k_frequency_order_matches_hand(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        cat = _by_name(res.profiles, "status").categorical
        assert cat.categories == [c for c, _ in STATUS_FREQ_ORDER]
        assert cat.frequencies == [n for _, n in STATUS_FREQ_ORDER]

    def test_frequencies_match_polars_group_by(self, con_h):
        """Independent reference: Polars group_by + sort matches the SQL."""
        con, h = con_h
        res = _run(con, h, _config())
        s = pl.Series("status", [r[2] for r in _ROWS]).drop_nulls()
        tbl = s.value_counts().sort(["count", "status"], descending=[True, False])
        engine = _by_name(res.profiles, "status").categorical
        assert engine.categories == tbl["status"].to_list()
        assert engine.frequencies == tbl["count"].to_list()

    def test_nulls_excluded_from_frequencies(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        # NULL row would otherwise dominate; the SQL filters col IS NOT NULL
        assert None not in _by_name(res.profiles, "status").categorical.categories

    def test_numeric_column_gets_no_categorical_block(self, con_h):
        """Registry applicability (_STRINGISH_LOGICAL) gates numeric cols out
        before this runner sees a categorical decision."""
        con, h = con_h
        res = _run(con, h, _config())
        assert _by_name(res.profiles, "amount").categorical is None
        assert _by_name(res.profiles, "id").categorical is None


class TestCategoricalRare:
    def test_rare_matches_threshold_hand(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        cat = _by_name(res.profiles, "status").categorical
        # 11 non-null × 0.001 → floor 0 → max(1, 0) = 1, so every count==1 cat
        assert max(1, int(STATUS_NON_NULL * RARE_FREQUENCY_SHARE)) == 1
        assert sorted(cat.rare_categories) == sorted(STATUS_RARE)

    def test_rare_empty_when_no_singleton(self):
        con = duckdb.connect()
        con.execute(
            'CREATE VIEW "srcr" AS SELECT CAST(v0 AS BIGINT) AS id, '
            "CAST(v1 AS BIGINT) AS amount, CAST(v2 AS VARCHAR) AS status, "
            "row_number() OVER () AS _row_id FROM (VALUES "
            "(1,10,'a'),(2,20,'a'),(3,30,'b'),(4,40,'b')) t(v0,v1,v2)"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srcr",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        res = _run(con, h, _config(), n_rows=4)
        assert _by_name(res.profiles, "status").categorical.rare_categories == []
        con.close()


class TestLabelInconsistencies:
    def test_detects_case_variants(self, con_h):
        con, h = con_h
        res = _run(con, h, _config())
        groups = _by_name(res.profiles, "status").categorical.label_inconsistencies
        by_norm = {g["normalized"]: g for g in groups}
        # 'active' collapsed variants: DuckDB VARCHAR sort is byte/ASCII order,
        # so raw variants come back ['ACTIVE','Active','active'] with counts
        # 1 (ACTIVE), 2 (Active), 1 (active).
        assert "active" in by_norm
        assert by_norm["active"]["variants"] == ["ACTIVE", "Active", "active"]
        assert by_norm["active"]["counts"] == [1, 2, 1]
        # 'pending' collapsed variants: Pending, pending
        assert "pending" in by_norm
        assert sorted(by_norm["pending"]["variants"]) == ["Pending", "pending"]
        # 'closed' has a single raw variant → not listed
        assert "closed" not in by_norm

    def test_whitespace_trim_collapses(self):
        con = duckdb.connect()
        con.execute(
            'CREATE VIEW "srcw" AS SELECT CAST(v0 AS BIGINT) AS id, '
            "CAST(v1 AS BIGINT) AS amount, CAST(v2 AS VARCHAR) AS status, "
            "row_number() OVER () AS _row_id FROM (VALUES "
            "(1,10,' alpha '),(2,20,'alpha'),(3,30,'beta')) t(v0,v1,v2)"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srcw",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        res = _run(con, h, _config(), n_rows=3)
        groups = _by_name(res.profiles, "status").categorical.label_inconsistencies
        norms = {g["normalized"] for g in groups}
        assert "alpha" in norms
        con.close()


# ---------------------------------------------------------------------------
# Low-cardinality gate (STATE M2-T5: §4 gate-1 reads uniqueness.distinct_count)
# ---------------------------------------------------------------------------


class TestCategoricalHighCardinalityGate:
    def test_block_absent_when_distinct_exceeds_threshold(self):
        con = duckdb.connect()
        n = CATEGORICAL_MAX_DISTINCT + 200
        con.execute(
            f'CREATE VIEW "srch" AS SELECT i AS id, i AS amount, '
            f"'c' || i AS status, row_number() OVER () AS _row_id "
            f"FROM range({n}) t(i)"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srch",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        res = _run(con, h, _config(), n_rows=n)
        # the profiler still emits uniqueness (distinct_count computed) —
        # only the categorical block is skipped
        status = _by_name(res.profiles, "status")
        assert status.uniqueness is not None
        assert status.uniqueness.distinct_count.value == n
        assert status.categorical is None
        con.close()


# ---------------------------------------------------------------------------
# Section gating + not_computed
# ---------------------------------------------------------------------------


class TestSectionGating:
    def test_uniqueness_section_disabled_omits_block(self, con_h):
        """All uniqueness decisions become not_computed via §4 gate 2; the
        runner skips the whole ColumnProfile.uniqueness block (never a
        fabrication of absent values)."""
        con, h = con_h
        res = _run(
            con, h, _config(),
            sections=["schema", "missing_values", "categorical", "descriptive_statistics"],
        )
        for p in res.profiles:
            assert p.uniqueness is None

    def test_categorical_section_disabled_omits_block_keeps_uniqueness(self, con_h):
        con, h = con_h
        res = _run(
            con, h, _config(),
            sections=["schema", "missing_values", "uniqueness", "descriptive_statistics"],
        )
        for p in res.profiles:
            assert p.categorical is None
        assert _by_name(res.profiles, "status").uniqueness is not None


# ---------------------------------------------------------------------------
# §6.4 partial failure
# ---------------------------------------------------------------------------


class TestPartialFailure:
    def test_uniqueness_metric_error_does_not_abort_the_run(self, con_h):
        con, h = con_h
        reg = MetricRegistry([
            s for s in default_registry().specs
            if s.metric_id != "uniqueness.distinct_count"
        ] + [
            # replace with a template that references a non-existent function
            type(next(s for s in default_registry().specs
                      if s.metric_id == "uniqueness.distinct_count"))(
                metric_id="uniqueness.distinct_count",
                family="uniqueness",
                display_name="distinct count (broken)",
                contract_target="column_profile.uniqueness.distinct_count",
                scope=next(s for s in default_registry().specs
                           if s.metric_id == "uniqueness.distinct_count").scope,
                cost_class=next(s for s in default_registry().specs
                                if s.metric_id == "uniqueness.distinct_count").cost_class,
                section="uniqueness",
                supports_approximate=False,
                sql_template="SELECT no_such_fn({col}) FROM {table}",
            )
        ])
        res = _run(con, h, _config(), registry=reg)
        # errored metric → not_computed + reason on its MetricValue
        status = _by_name(res.profiles, "status")
        assert status.uniqueness is not None
        assert status.uniqueness.distinct_count.provenance.method == MetricMethod.NOT_COMPUTED.value
        assert status.uniqueness.distinct_count.provenance.reason
        # §6.4: sibling metrics (is_unique_candidate + duplicate_groups) still
        # executed / errored independently, run not aborted
        assert any(e.task and e.task.startswith("uniqueness.distinct_count:status")
                   for e in res.errors)
        assert any(e.code == ErrorCode.METRIC_NOT_COMPUTED.value for e in res.errors)


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


class TestEdges:
    def test_empty_relation(self):
        con = duckdb.connect()
        con.execute(
            'CREATE VIEW "srce" AS SELECT CAST(v0 AS BIGINT) AS id, '
            "CAST(v1 AS BIGINT) AS amount, CAST(v2 AS VARCHAR) AS status, "
            "row_number() OVER () AS _row_id FROM (VALUES (1,1,'x')) t(v0,v1,v2) "
            "WHERE false"
        )
        h = RelationHandle(
            dataset_id="ds", connector="manual", view_name="srce",
            snapshot=_snapshot(),
            columns=(("id", "BIGINT"), ("amount", "BIGINT"), ("status", "VARCHAR")),
            row_id_column="_row_id",
        )
        res = _run(con, h, _config(), n_rows=0)
        for col in ("id", "amount", "status"):
            u = _by_name(res.profiles, col).uniqueness
            assert u.distinct_count.value == 0
            assert u.is_unique_candidate is True   # 0 == 0
        # empty → categorical block emitted with empty arrays (low-cardinality gate 0 ≤ 1000)
        cat = _by_name(res.profiles, "status").categorical
        assert cat is not None
        assert cat.categories == [] and cat.frequencies == []
        assert cat.rare_categories == [] and cat.label_inconsistencies == []
        con.close()


# ---------------------------------------------------------------------------
# Contract conformance + determinism (TESTING §3.3 / §3.4)
# ---------------------------------------------------------------------------


class TestContractConformance:
    def test_every_profile_schema_valid(self, con_h):
        con, h = con_h
        for mode in (ProfileMode.STANDARD, ProfileMode.QUICK, ProfileMode.DEEP):
            res = _run(con, h, _config(mode=mode.value), mode=mode)
            for p in res.profiles:
                errs = S.validate("column_profile.schema.json",
                                  p.model_dump(mode="json"))
                assert errs == [], (mode, p.column, errs)

    def test_identical_rerun_is_byte_stable(self, con_h):
        con, h = con_h
        a = _run(con, h, _config()).profiles
        b = _run(con, h, _config()).profiles
        assert [p.model_dump_json() for p in a] == [p.model_dump_json() for p in b]
