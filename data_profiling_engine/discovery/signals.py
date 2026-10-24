"""Bounded DuckDB aggregates feeding the inference rules (M2-T2, ENGINE §4.1).

ADR-007 (never load a full dataset into Python RAM) is honoured here: every
probe is a single-row aggregate, plus one bounded reservoir sample of ≤N
distinct VARCHAR values used purely for pattern detection. Nothing else from
the source crosses the DuckDB→Python boundary at discovery time.

Cardinality switches strategy at :data:`EXACT_DISTINCT_ROW_LIMIT`:
- below → ``count(DISTINCT col)`` (exact, cheap; a full hash is feasible)
- above → ``approx_count_distinct(col)`` (HLL; error ~1-2% is fine for the
  inference decisions this module makes — the contract-grade ``distinct_count``
  metric with formal Provenance is M2-T5's job)

A DuckDB execution failure surfaces as :class:`DiscoveryError` carrying the
registered §8 code ``SCHEMA_INFERENCE_FAILED`` (AGENTS §3 — never invent a code).
"""
from __future__ import annotations

from typing import List, Optional

import duckdb

from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery.types import ColumnSignals
from data_profiling_engine.errors import ErrorCode

#: Row count above which distinct-count switches to HLL (DuckDB default).
EXACT_DISTINCT_ROW_LIMIT = 10_000

#: Sampled values pulled per VARCHAR column for pattern sniffing.
SAMPLE_DISTINCT_LIMIT = 32

#: Reservoir size backing the sample (a full-scan DISTINCT on high-cardinality
#: columns would build an unbounded hash — reservoir keeps the working set fixed).
RESERVOIR_ROWS = 512


class DiscoveryError(Exception):
    """Discovery-layer failure carrying a registered CONTRACTS §8 code."""

    code = ErrorCode.SCHEMA_INFERENCE_FAILED

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _q(ident: str) -> str:
    """Double-quote a DuckDB identifier safely (embedded ``"`` doubled)."""
    return '"' + ident.replace('"', '""') + '"'


def _family(physical: str) -> str:
    """Coarse physical-family bucket driving which probes to run.

    ``physical`` is the string the connector captured from ``description[i][1]``
    uppercased (e.g. ``BIGINT``, ``DECIMAL(10,2)``, ``TIMESTAMP WITH TIME ZONE``,
    ``INTEGER[]``, ``UUID``, ``VARCHAR``).
    """
    p = physical.upper()
    if p.endswith("[]") or p.startswith(("STRUCT", "MAP", "UNION")):
        return "nested"
    if p == "BOOLEAN":
        return "bool"
    if p == "VARCHAR" or p == "TEXT":
        return "varchar"
    if p.startswith(("TIMESTAMP", "DATE", "TIME")):
        return "temporal"
    if p.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
                     "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")):
        return "integer"
    if p.startswith(("FLOAT", "DOUBLE", "DECIMAL")):
        return "float"
    if p == "UUID":
        return "uuid"
    if p.startswith("BLOB") or p == "BITSTRING":
        return "binary"
    return "other"


def collect_column_signals(
    con: duckdb.DuckDBPyConnection, handle: RelationHandle, col: str, physical: str
) -> ColumnSignals:
    """Run the bounded probe set for one column; every SQL is a scalar
    aggregate or a fixed-size reservoir sample — no full rows materialise."""
    view = _q(handle.view_name)
    c = _q(col)
    fam = _family(physical)

    try:
        n_rows, n_null, n_notnull = con.execute(
            f"SELECT count(*), count(*) - count({c}), count({c}) FROM {view}"
        ).fetchone()
        n_rows = int(n_rows or 0)
        n_null = int(n_null or 0)
        n_notnull = int(n_notnull or 0)

        if n_rows <= EXACT_DISTINCT_ROW_LIMIT:
            n_distinct = int(con.execute(
                f"SELECT count(DISTINCT {c}) FROM {view}"
            ).fetchone()[0] or 0)
            distinct_method = "exact"
        else:
            n_distinct = int(con.execute(
                f"SELECT approx_count_distinct({c}) FROM {view}"
            ).fetchone()[0] or 0)
            distinct_method = "approximate"

        kwargs = dict(
            n_rows=n_rows,
            n_null=n_null,
            n_notnull=n_notnull,
            n_distinct=n_distinct,
            distinct_method=distinct_method,
        )

        if fam == "varchar":
            n_blank, min_len, max_len = con.execute(
                f"SELECT "
                f"count(*) FILTER (WHERE {c} IS NOT NULL AND trim({c}) = ''), "
                f"min(length({c})) FILTER (WHERE {c} IS NOT NULL), "
                f"max(length({c})) FILTER (WHERE {c} IS NOT NULL) "
                f"FROM {view}"
            ).fetchone()
            kwargs["n_blank"] = int(n_blank or 0)
            kwargs["min_len"] = int(min_len) if min_len is not None else None
            kwargs["max_len"] = int(max_len) if max_len is not None else None
            kwargs["sample"] = _distinct_sample(con, view, c)

        elif fam in ("integer", "float", "temporal"):
            # CAST to VARCHAR: fetching a TIMESTAMP WITH TIME ZONE scalar into
            # Python requires pytz (DuckDB constructs a tz-aware datetime),
            # which is not a dependency we ship. Discovery only needs the
            # stringified range for signals inspection, so we do the cast in
            # DuckDB and never hand a tz-aware datetime to Python.
            mn, mx = con.execute(
                f"SELECT CAST(min({c}) AS VARCHAR), CAST(max({c}) AS VARCHAR) FROM {view}"
            ).fetchone()
            kwargs["min_repr"] = None if mn is None else str(mn)
            kwargs["max_repr"] = None if mx is None else str(mx)

        elif fam == "bool":
            n_true, n_false = con.execute(
                f"SELECT count(*) FILTER (WHERE {c} = TRUE), "
                f"count(*) FILTER (WHERE {c} = FALSE) FROM {view}"
            ).fetchone()
            kwargs["n_true"] = int(n_true or 0)
            kwargs["n_false"] = int(n_false or 0)

        return ColumnSignals(**kwargs)
    except duckdb.Error as exc:
        raise DiscoveryError(
            f"discovery probe failed on column {col!r} of {handle.view_name!r}: {exc}"
        ) from exc


def _distinct_sample(
    con: duckdb.DuckDBPyConnection, view: str, c: str
) -> List[str]:
    """Up to SAMPLE_DISTINCT_LIMIT non-null distinct strings, drawn from a
    bounded reservoir so no full-value dictionary is built on high-cardinality
    columns. Order is not guaranteed and is not used."""
    sql = (
        f"SELECT DISTINCT {c} FROM ("
        f"  SELECT {c} FROM {view} WHERE {c} IS NOT NULL "
        f"  USING SAMPLE RESERVOIR ({RESERVOIR_ROWS} ROWS)"
        f") LIMIT {SAMPLE_DISTINCT_LIMIT}"
    )
    return [str(r[0]) for r in con.execute(sql).fetchall() if r[0] is not None]


__all__ = [
    "DiscoveryError",
    "collect_column_signals",
    "EXACT_DISTINCT_ROW_LIMIT",
    "SAMPLE_DISTINCT_LIMIT",
    "RESERVOIR_ROWS",
]
