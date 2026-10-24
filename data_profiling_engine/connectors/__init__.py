"""Connectors package — CSV + Parquet → DuckDB relations (M2-T1, ADR-011).

MVP allow-list of sources: **csv** and **parquet only**. Phase-2 connectors
plug in by subclassing :class:`Connector` and registering here — no contract
change (ADR-011 consequence clause).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Type

from data_profiling_engine.connectors.base import (
    CSV,
    PARQUET,
    ROW_ID_COLUMN,
    Connector,
    ConnectorError,
    DatasetReadFailed,
    RelationHandle,
    SourceNotAllowed,
)
from data_profiling_engine.connectors.csv_connector import CsvConnector
from data_profiling_engine.connectors.parquet_connector import ParquetConnector

#: Contract-neutral connector name → implementation (CONTRACTS §2 dataset.connector).
CONNECTOR_REGISTRY: Dict[str, Type[Connector]] = {
    CSV: CsvConnector,
    PARQUET: ParquetConnector,
}

#: File suffix → connector name; the only extensions MVP may open (ADR-011).
SUFFIX_TO_CONNECTOR: Dict[str, str] = {
    ".csv": CSV,
    ".parquet": PARQUET,
}


def connector_for_path(path: "Path | str") -> str:
    """Detect the connector from the file suffix; reject anything else (MVP)."""
    suffix = Path(path).suffix.lower()
    name = SUFFIX_TO_CONNECTOR.get(suffix)
    if name is None:
        raise SourceNotAllowed(
            f"no MVP connector for suffix {suffix!r}; allowed: "
            f"{sorted(SUFFIX_TO_CONNECTOR)} (ADR-011)"
        )
    return name


def get_connector(
    dataset_id: str,
    path: "Path | str",
    connector: Optional[str] = None,
    allowed_roots: "Optional[List[Path]]" = None,
) -> Connector:
    """Instantiate the right connector; ``connector`` (contract string) wins,
    otherwise detected from the suffix. Unknown names → ``SOURCE_NOT_ALLOWED``."""
    name = connector or connector_for_path(path)
    cls = CONNECTOR_REGISTRY.get(name)
    if cls is None:
        raise SourceNotAllowed(
            f"unknown connector {name!r}; MVP registry: {sorted(CONNECTOR_REGISTRY)}"
        )
    return cls(dataset_id=dataset_id, path=path, allowed_roots=allowed_roots)


__all__ = [
    "CSV",
    "PARQUET",
    "ROW_ID_COLUMN",
    "Connector",
    "ConnectorError",
    "DatasetReadFailed",
    "SourceNotAllowed",
    "RelationHandle",
    "CsvConnector",
    "ParquetConnector",
    "CONNECTOR_REGISTRY",
    "SUFFIX_TO_CONNECTOR",
    "connector_for_path",
    "get_connector",
]
