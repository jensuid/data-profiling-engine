"""M2-T1 — Connector tests: CSV + Parquet → DuckDB relation (TESTING §3.1).

Every assertion is checked against an **independent reference** (hashlib,
Polars, hand-computed literals) — never against the connector's own logic:

- SourceSnapshot fields (CONTRACTS §2) vs hashlib/Path.stat, schema-conformance.
- content_hash stability + true read-only (source bytes/mtime untouched).
- Path allow-list + suffix allow-list (ADR-011: no Excel/other paths reachable).
- Row-id policy (ENGINE §5.2): CSV generated line number, Parquet row-group+
  offset; deterministic across re-registers and stable under filter pushdown.
- Failure mapping to registered §8 codes (DATASET_READ_FAILED / SOURCE_NOT_ALLOWED).
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import polars as pl
import pytest

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors import (
    CSV,
    PARQUET,
    ROW_ID_COLUMN,
    SUFFIX_TO_CONNECTOR,
    ConnectorError,
    DatasetReadFailed,
    RelationHandle,
    SourceNotAllowed,
    connector_for_path,
    get_connector,
)
from data_profiling_engine.errors import ErrorCode

# ---- fixtures -------------------------------------------------------------

CSV_ROWS = [
    (1, 100.5, "Closed Won"),
    (2, None, "Open"),
    (3, 50.0, "Closed Won"),
]


@pytest.fixture()
def sales_csv(tmp_path: Path) -> Path:
    p = tmp_path / "sales.csv"
    p.write_text(
        "deal_id,deal_value,status\n"
        "1,100.5,Closed Won\n"
        "2,,Open\n"
        "3,50,Closed Won\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def sales_parquet(tmp_path: Path, sales_csv: Path) -> Path:
    """Same data via DuckDB COPY — native typed columnar file."""
    p = tmp_path / "sales.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT * FROM read_csv('{sales_csv}')) TO '{p}' (FORMAT parquet)"
    )
    return p


def _load(con: duckdb.DuckDBPyConnection, handle: RelationHandle) -> list:
    return con.execute(
        f'SELECT * FROM "{handle.view_name}" ORDER BY {ROW_ID_COLUMN}'
    ).fetchall()


# ---- factory / registry (ADR-011 closed MVP set) ---------------------------


def test_suffix_dispatch():
    assert connector_for_path("a/b/x.CSV") == CSV
    assert connector_for_path("x.parquet") == PARQUET


def test_non_mvp_suffix_rejected():
    with pytest.raises(SourceNotAllowed):
        connector_for_path("sheet.xlsx")
    with pytest.raises(SourceNotAllowed):
        connector_for_path("no_extension_file")


def test_get_connector_explicit_and_unknown():
    c = get_connector("d1", "whatever.csv", connector=CSV)
    assert c.name == CSV
    with pytest.raises(SourceNotAllowed):
        get_connector("d1", "x.csv", connector="excel")  # not in MVP registry
    assert sorted(SUFFIX_TO_CONNECTOR) == [".csv", ".parquet"]


def test_error_codes_are_registered():
    # connector errors must carry formal §8 codes, never invented strings
    assert ConnectorError.__init__ is not None
    assert DatasetReadFailed("x").code == ErrorCode.DATASET_READ_FAILED
    assert SourceNotAllowed("x").code == ErrorCode.SOURCE_NOT_ALLOWED
    from data_profiling_engine.errors import is_registered

    assert is_registered(DatasetReadFailed("x").code)
    assert is_registered(SourceNotAllowed("x").code)


# ---- CSV relation ----------------------------------------------------------


def test_csv_relation_content(sales_csv):
    con = duckdb.connect()
    h = get_connector("ds_sales", sales_csv).register(con)
    assert h.connector == CSV
    assert h.column_names == ("deal_id", "deal_value", "status")
    # independent reference: Polars reads the same file
    ref = pl.read_csv(sales_csv)
    assert list(h.column_names) == ref.columns
    assert _load(con, h) == [
        (1, 100.5, "Closed Won", 1),
        (2, None, "Open", 2),
        (3, 50.0, "Closed Won", 3),
    ]
    assert ref.shape[0] == con.execute(
        f'SELECT count(*) FROM "{h.view_name}"'
    ).fetchone()[0]


def test_csv_physical_types(sales_csv):
    con = duckdb.connect()
    h = get_connector("ds_sales", sales_csv).register(con)
    types = dict(h.columns)
    assert types["deal_id"] == "BIGINT"
    assert types["deal_value"] == "DOUBLE"  # 100.5 forces float column
    assert types["status"] == "VARCHAR"


# ---- Parquet relation ------------------------------------------------------


def test_parquet_relation_content(sales_parquet):
    con = duckdb.connect()
    h = get_connector("ds_sales_pq", sales_parquet).register(con)
    assert h.connector == PARQUET
    # file_row_number plumbing must NOT leak into the relation
    assert h.column_names == ("deal_id", "deal_value", "status")
    ref = pl.read_parquet(sales_parquet)
    assert list(h.column_names) == ref.columns
    rows = _load(con, h)
    assert [r[:3] for r in rows] == [
        (1, 100.5, "Closed Won"),
        (2, None, "Open"),
        (3, 50.0, "Closed Won"),
    ]


# ---- row-id policy (ENGINE §5.2) -------------------------------------------


@pytest.mark.parametrize("fixture", ["sales_csv", "sales_parquet"])
def test_row_id_is_sequential_and_deterministic(fixture, request):
    path = request.getfixturevalue(fixture)
    con = duckdb.connect()
    h1 = get_connector("ds", path).register(con)
    h2 = get_connector("ds", path).register(con)  # re-register same source
    ids1 = [r[3] for r in _load(con, h1)]
    ids2 = [r[-1] for r in con.execute(
        f'SELECT {ROW_ID_COLUMN} FROM "{h2.view_name}" ORDER BY {ROW_ID_COLUMN}'
    ).fetchall()]
    assert ids1 == list(range(1, 4))  # 1-based, matches data line/row order
    assert ids1 == ids2


@pytest.mark.parametrize("fixture", ["sales_csv", "sales_parquet"])
def test_row_id_stable_under_filter(fixture, request):
    """Violation-sample semantics: filtering the relation must NOT renumber —
    a captured _row_id addresses the same source row on any later full scan."""
    path = request.getfixturevalue(fixture)
    con = duckdb.connect()
    h = get_connector("ds", path).register(con)
    rids = con.execute(
        f'SELECT {ROW_ID_COLUMN} FROM "{h.view_name}" WHERE status = ?', ["Closed Won"]
    ).fetchall()
    assert [r[0] for r in rids] == [1, 3]  # hand-computed: rows 1 and 3


def test_row_id_parquet_multi_rowgroup(tmp_path):
    """'row group+offset' policy verified across row-group boundaries."""
    csv = tmp_path / "big.csv"
    n = 300_000  # > DuckDB default 120K row-group ⇒ ≥3 groups
    csv.write_text("i\n" + "\n".join(str(i) for i in range(1, n + 1)), encoding="utf-8")
    pq = tmp_path / "big.parquet"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT * FROM read_csv('{csv}')) TO '{pq}' (FORMAT parquet)")
    h = get_connector("ds_big", pq).register(con)
    got = con.execute(f'SELECT min({ROW_ID_COLUMN}), max({ROW_ID_COLUMN}), count(*) '
                      f'FROM "{h.view_name}"').fetchone()
    assert got == (1, n, n)
    # offset correctness mid-file (independent: the data column itself)
    bad = con.execute(
        f'SELECT count(*) FROM "{h.view_name}" WHERE {ROW_ID_COLUMN} <> i'
    ).fetchone()[0]
    assert bad == 0


# ---- SourceSnapshot (CONTRACTS §2) -----------------------------------------


@pytest.mark.parametrize("fixture", ["sales_csv", "sales_parquet"])
def test_snapshot_fields_match_independent_reference(fixture, request):
    path = request.getfixturevalue(fixture)
    snap = get_connector("ds", path).build_snapshot()
    st = path.stat()
    assert snap.path == str(path.resolve())
    assert snap.size_bytes == st.st_size
    expected = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert snap.modified_at == expected
    digest = hashlib.sha256(path.read_bytes()).hexdigest()  # one-shot reference
    assert snap.content_hash == f"sha256:{digest}"


def test_snapshot_dump_validates_and_preserves_hash_on_rerun(sales_csv):
    """content_hash is the audit anchor: unchanged bytes ⇒ unchanged hash, and
    the snapshot must fit the §2 open object (dump → no schema errors)."""
    before = os.stat(sales_csv)
    c = get_connector("ds", sales_csv)
    s1, s2 = c.build_snapshot(), c.build_snapshot()
    assert s1.content_hash == s2.content_hash
    after = os.stat(sales_csv)
    assert (after.st_size, after.st_mtime) == (before.st_size, before.st_mtime)
    con = duckdb.connect()
    h = c.register(con)  # registering must not touch the file either
    assert h.snapshot.content_hash == s1.content_hash
    errs = S.validate("profiling_run.schema.json", _run_with(h))
    assert errs == [], errs


def _run_with(h: RelationHandle) -> dict:
    """Minimal §2-valid ProfilingRun carrying the connector's snapshot."""
    return {
        "schema_version": "1.0",
        "run_id": "01J8Z4K2M7AAAAAAAAAAAAAAAA",
        "dataset": {
            "dataset_id": h.dataset_id,
            "source_kind": "file",
            "connector": h.connector,
            "source_ref": Path(h.snapshot.path).name,
            "source_snapshot": h.snapshot.model_dump(),
        },
        "config": {"config_version": "3", "config_hash": "sha256:abcd", "profile_mode": "standard"},
        "engine": {"engine_version": "0.1.0"},
        "timing": {"started_at": "2026-10-24T09:00:00Z", "duration_ms": 1},
        "status": "SUCCEEDED",
    }


# ---- read-only + allow-list -------------------------------------------------


def test_read_only_no_write_no_new_files(tmp_path, sales_csv):
    con = duckdb.connect()
    h = get_connector("ds", sales_csv).register(con)
    con.execute(f'SELECT count(*) FROM "{h.view_name}"').fetchall()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sales.csv"]
    assert sales_csv.read_text(encoding="utf-8").startswith("deal_id,")


def test_allow_list_blocks_outside_path(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    inside = root / "in.csv"
    inside.write_text("a\n1\n", encoding="utf-8")
    outside = tmp_path / "outside.csv"  # sibling of the allow-list root
    outside.write_text("a\n1\n", encoding="utf-8")
    c = get_connector("ds", outside, allowed_roots=[root])
    with pytest.raises(SourceNotAllowed):
        c.build_snapshot()
    ok = get_connector("ds", inside, allowed_roots=[root])
    assert ok.build_snapshot().content_hash.startswith("sha256:")


# ---- failure mapping ---------------------------------------------------------


def test_missing_file(tmp_path):
    with pytest.raises(DatasetReadFailed):
        get_connector("ds", tmp_path / "ghost.csv").build_snapshot()


def test_directory_not_file(tmp_path):
    d = tmp_path / "looks.csv"  # suffix passes, regular-file check must not
    d.mkdir()
    with pytest.raises(DatasetReadFailed):
        get_connector("ds", d).register(duckdb.connect())


def test_corrupt_csv_content(sales_csv):
    """Suffix says csv but content is garbage → unparsable, registry-coded."""
    bad = sales_csv.parent / "bad.csv"
    bad.write_bytes(os.urandom(512))
    with pytest.raises(DatasetReadFailed):
        get_connector("ds", bad).register(duckdb.connect())


def test_corrupt_parquet_bytes(tmp_path):
    p = tmp_path / "x.parquet"
    p.write_bytes(b"PAR1" + os.urandom(200))
    with pytest.raises(DatasetReadFailed):
        get_connector("ds", p).register(duckdb.connect())


def test_wrong_connector_for_suffix(sales_parquet):
    """contract string csv on a parquet file → suffix guard rejects (security)."""
    c = get_connector("ds", sales_parquet, connector=CSV)
    with pytest.raises(SourceNotAllowed):
        c.build_snapshot()


def test_rowid_column_collision(sales_csv):
    p = sales_csv.parent / "rowid.csv"
    p.write_text("a,_row_id\n1,2\n", encoding="utf-8")
    with pytest.raises(DatasetReadFailed):
        get_connector("ds", p).register(duckdb.connect())


def test_filerownum_column_collision(tmp_path, sales_csv):
    p = tmp_path / "frn.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT *, 1 AS file_row_number FROM read_csv('{sales_csv}')) "
        f"TO '{p}' (FORMAT parquet)"
    )
    with pytest.raises(DatasetReadFailed):
        get_connector("ds", p).register(duckdb.connect())


def test_path_with_single_quote(sales_csv):
    p = sales_csv.parent / "we'ird, name.csv"
    p.write_text("a\n1\n2\n", encoding="utf-8")
    con = duckdb.connect()
    h = get_connector("ds", p).register(con)
    assert con.execute(f'SELECT count(*) FROM "{h.view_name}"').fetchone()[0] == 2
    assert h.snapshot.content_hash == "sha256:" + hashlib.sha256(
        p.read_bytes()
    ).hexdigest()
