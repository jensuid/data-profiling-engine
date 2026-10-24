"""Parquet connector — DuckDB ``read_parquet`` relation, read-only (M2-T1, ADR-011).

Row-id policy (ENGINE §5.2): *row group + offset*. DuckDB's
``file_row_number=true`` yields the 0-based global row index within the file
(= cumulative row-group offset); we add 1 so ``_row_id`` is 1-based like the
CSV policy while staying exactly the "row group+offset" the spec names.
Stable under filtering because the window column is produced inside the
``read_parquet`` scan before any outer WHERE.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

from data_profiling_engine.connectors.base import PARQUET, Connector

_SUFFIXES = (".parquet",)


class ParquetConnector(Connector):
    """Parquet file → DuckDB view via ``read_parquet`` (native types preserved)."""

    name = PARQUET
    suffixes = _SUFFIXES

    def _table_function(self, path_literal: str) -> str:
        return f"read_parquet({path_literal}, file_row_number=true)"

    def _bare_table_function(self, path_literal: str) -> str:
        return f"read_parquet({path_literal})"

    def _row_id_expr(self) -> str:
        return "src.file_row_number + 1"

    def _plumbing_columns(self) -> Tuple[str, ...]:
        # stripped from the visible schema; consumed only by _row_id_expr()
        return ("file_row_number",)

    def _extra_metadata(self, resolved: Path) -> Dict[str, object]:
        """§3.1 source_metadata facts for parquet (native types, no coercion)."""
        return {"connector": PARQUET}
