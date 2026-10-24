"""Discovery package — physical→logical/semantic type inference (M2-T2).

Consumes a :class:`RelationHandle` (M2-T1) + optional :class:`DatasetConfig`
(CONTRACTS §9) and produces a :class:`DiscoveryReport` whose ``ColumnProfile``
skeletons are already contract-valid for the type-triple + unit fields; the
metric sub-blocks (missingness / uniqueness / stats / distribution /
categorical / temporal) are populated by later M2-T4..T6 profilers.

Public surface::

    from data_profiling_engine.discovery import discover

    report = discover(con, handle, config=cfg)   # cfg may be None

Behavioral rules honoured here (AGENTS §2–§3, ENGINE §4.1):

- Never loads full columns into Python RAM — probes are scalar aggregates
  plus one bounded reservoir sample of ≤N distinct VARCHAR values (ADR-007).
- Config declarations win; inference never overwrites a user assertion
  (ENGINE §4.1 last bullet).
- Currency/unit is only ``known`` when config says so; inference emits
  ``assumed``/``unknown`` (never silently asserted).
- Every column carries a ``confidence`` + rationale so misclassification is
  visible downstream (planner / findings).
"""
from __future__ import annotations

from typing import Optional

import duckdb

from data_profiling_engine.connectors.base import RelationHandle
from data_profiling_engine.discovery.inference import infer_column
from data_profiling_engine.discovery.signals import (
    DiscoveryError,
    collect_column_signals,
)
from data_profiling_engine.discovery.types import (
    ColumnDiscovery,
    ColumnSignals,
    Confidence,
    DiscoveryReport,
    LogicalType,
)
from data_profiling_engine.schemas.models import DatasetConfig


def discover(
    con: duckdb.DuckDBPyConnection,
    handle: RelationHandle,
    config: Optional[DatasetConfig] = None,
) -> DiscoveryReport:
    """Run the bounded probe set + inference cascade over every source column
    of ``handle`` (row-id plumbing is already excluded by
    :attr:`RelationHandle.column_names`)."""
    pk_columns: Optional[frozenset] = None
    if config and config.dataset.primary_key:
        pk_columns = frozenset(config.dataset.primary_key)

    discoveries = []
    for ordinal, (name, physical) in enumerate(handle.columns):
        signals = collect_column_signals(con, handle, name, physical)
        discoveries.append(
            infer_column(
                name=name,
                ordinal=ordinal,
                physical=physical,
                signals=signals,
                config=config,
                pk_columns=pk_columns,
            )
        )
    return DiscoveryReport(dataset_id=handle.dataset_id, columns=discoveries)


__all__ = [
    "discover",
    "DiscoveryError",
    "DiscoveryReport",
    "ColumnDiscovery",
    "ColumnSignals",
    "Confidence",
    "LogicalType",
]
