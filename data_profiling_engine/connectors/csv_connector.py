"""CSV connector — DuckDB ``read_csv`` relation, read-only (M2-T1, ADR-011).

Row-id policy (ENGINE §5.2): *generated line number*. DuckDB 2.0 removed the
CSV reader's ``file_row_number`` option, so ``_row_id`` is
``row_number() OVER ()`` over the sequential single-file scan — data-row order
as file line index (1 = first data row, header excluded). Window evaluation
precedes any outer filter, so a row-id recovered from a violation sample
addresses the same line on every re-run of the same file (verified in
tests/test_connectors.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

from data_profiling_engine.connectors.base import CSV, Connector

_SUFFIXES = (".csv",)


class CsvConnector(Connector):
    """CSV file → DuckDB view via ``read_csv`` (header/delim/encoding auto-detect)."""

    name = CSV
    suffixes = _SUFFIXES

    def _table_function(self, path_literal: str) -> str:
        return f"read_csv({path_literal}, header=true, auto_detect=true)"

    def _bare_table_function(self, path_literal: str) -> str:
        return self._table_function(path_literal)  # csv has no reader plumbing

    def _row_id_expr(self) -> str:
        return "row_number() OVER ()"

    def _extra_metadata(self, resolved: Path) -> Dict[str, object]:
        """CONTRACTS §3.1 ``source_metadata`` facts for csv (connector/delimiter/
        encoding); delimiter + encoding are DuckDB auto-detect results."""
        return {"connector": CSV, "delimiter": "auto", "encoding": "auto"}
