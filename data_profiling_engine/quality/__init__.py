"""Quality package — rule registry, compiler (→ DuckDB SQL), executor (M2-T7).

ARCHITECTURE §3 ``quality/``; ENGINE §5. This is the *judging* layer of the
"measure ≠ judge ≠ interpret ≠ act" pipeline (§1): profilers (M2-T4..T6)
measured ``ColumnProfile`` families; the quality engine judges the bound
relation against the declarative ``dataset_config.quality_checks`` catalog
(CONTRACTS §9) and emits one :class:`QualityCheckResult` (§4.1) per rule. It
produces **no new ColumnProfile block** — results feed the dataset-level
quality layer that M2-T8 findings read.

Public surface::

    from data_profiling_engine.quality import run_quality_checks

    result = run_quality_checks(con, handle, config, run_id, discovery=report)
    for check in result.checks:      # PASS / FAIL / WARN / ERROR (§4.1)
        ...

Obligations honoured across the submodules (AGENTS §2–§3):

- One SQL statement per rule (ENGINE §5.2), user values **bound not
  interpolated** (ADR-017 discipline), never a full materialisation (ADR-007).
- ``FAIL`` ≠ ``ERROR``: an un-executable rule is ``ERROR`` (§5.3), never a
  fabricated violation; ``WARN`` rules never hard-fail.
- §6.4 per-task isolation: one broken rule does not abort the run.
"""
from __future__ import annotations

from data_profiling_engine.quality.compiler import (
    CompiledRule,
    RuleCompileError,
    compile_rule,
)
from data_profiling_engine.quality.executor import (
    RULE_VERSION,
    QualityRunResult,
    run_quality_checks,
)
from data_profiling_engine.quality.registry import (
    RuleDefinition,
    RuleRegistryError,
    catalog,
    default_dimension,
    is_active,
    lookup,
)

__all__ = [
    "run_quality_checks",
    "QualityRunResult",
    "RULE_VERSION",
    "compile_rule",
    "CompiledRule",
    "RuleCompileError",
    "lookup",
    "is_active",
    "default_dimension",
    "catalog",
    "RuleDefinition",
    "RuleRegistryError",
]
