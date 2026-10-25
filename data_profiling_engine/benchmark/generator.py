"""Synthetic workload generator (M7-T1; TESTING_BENCHMARKS §4.1).

Produces benchmark fixtures whose shape varies the §4.1 workload matrix —
rows / columns / cardinality (low·medium·high) / missingness (0–40%) /
distribution (normal·skewed) / source (CSV·Parquet) — so the harness can drive
the real engine spine against realistic scale without shipping a giant fixture
in the repo.

Design constraints (AGENTS §2, ADR-007):

- **Generated inside DuckDB, never in a Python row loop.** The whole table is
  emitted by one ``COPY (SELECT ... FROM range(n))`` statement, so a 100M-row
  file never materialises in Python RAM.
- **Deterministic without a global RNG.** Every pseudo-random value is a pure
  function of the row index and a per-column salt (a masked ``hash`` → uniform in
  ``[0,1)``), so generation is stable across DuckDB's parallel scan and thread
  count — a re-run of the same :class:`DatasetSpec` yields byte-equal output.
  ``seed`` shifts the salt space; two specs with the same seed+shape agree.
- **Read/write only within the caller's directory.** No connector here touches a
  source the engine profiles; this writes new benchmark artifacts only.

The five core column kinds mirror the ``sales_deals`` reference fixture so a
profile of a generated set exercises the same metric families (schema +
descriptive statistics + missingness + uniqueness + distributions + temporal)
the report layer consumes (ENGINE §10).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import duckdb

from data_profiling_engine import duckdb_session

#: Cardinality tiers → the number of distinct values a "category" column takes
#: (capped at the row count at generation time). TESTING §4.1 "cardinality
#: (low/high)"; medium sits between as the realistic default.
_CARDINALITY_TIERS: Dict[str, int] = {
    "low": 8,
    "medium": 1_000,
    "high": 1_000_000,
}

#: A small closed label set for the low-cardinality categorical column, so
#: ``valid_domain`` style rules and value-count metrics have a real domain.
_STAGE_LABELS: Tuple[str, ...] = (
    "Prospecting",
    "Negotiation",
    "Closed Won",
    "Closed Lost",
    "On Hold",
    "Churned",
)


@dataclass(frozen=True)
class DatasetSpec:
    """One §4.1 workload point. ``rows``/``columns`` are absolute; the rest are
    shape knobs. ``missingness`` is a fraction in ``[0, 0.4]`` (the matrix's
    0–40% band). ``distribution`` selects the numeric tail shape. ``seed`` is a
    reproducibility salt (never a run seed — that is the engine's job)."""

    rows: int
    columns: int
    cardinality: str = "medium"
    missingness: float = 0.1
    distribution: str = "normal"
    seed: int = 42

    def __post_init__(self) -> None:
        if self.rows < 1:
            raise ValueError("DatasetSpec.rows must be >= 1")
        # 5 core kinds are always emitted; ask for at least that many columns.
        if self.columns < 5:
            raise ValueError("DatasetSpec.columns must be >= 5 (the core kinds)")
        if not 0.0 <= self.missingness <= 0.4:
            raise ValueError("DatasetSpec.missingness must be within [0.0, 0.4]")
        if self.cardinality not in _CARDINALITY_TIERS:
            raise ValueError(
                f"cardinality must be one of {sorted(_CARDINALITY_TIERS)}"
            )
        if self.distribution not in ("normal", "skewed"):
            raise ValueError("distribution must be 'normal' or 'skewed'")


# -- expression builders ------------------------------------------------------

def _u01(i: str, salt: int) -> str:
    """Deterministic uniform in ``[0,1)`` from a masked, salted row hash.

    ``hash`` is stable for a given integer key across runs and threads; masking
    with ``2^31-1`` forces a non-negative BIGINT (``hash`` is signed) before the
    modulus, so no negative value ever reaches the ``/1e6`` division."""
    return (
        f"((hash(({i})::BIGINT * 2654435761 + {salt}) & 2147483647) % 1000000) "
        f"/ 1000000.0"
    )


def _std_normal(i: str, salt: int) -> str:
    """Standard-normal deviate via Box–Muller of two decorrelated uniforms.

    ``ln`` needs a strictly positive argument, so the first uniform is clamped
    to ``>= 1e-6`` (the top of the range is open at ``[0,1)`` anyway)."""
    u1 = f"greatest({_u01(i, salt * 2 + 1)}, 0.000001)"
    u2 = _u01(i, salt * 2 + 2)
    return f"(sqrt(-2.0 * ln({u1})) * cos(2.0 * pi() * ({u2})))"


def _missing_guard(i: str, salt: int, missingness: float, value_expr: str) -> str:
    """Wrap a value expression so a ``missingness`` fraction of rows read NULL
    (the §4.1 missingness dimension). Salt is offset so the null mask is
    independent of the value's own randomness."""
    if missingness <= 0.0:
        return value_expr
    return (
        f"CASE WHEN {_u01(i, salt + 90000)} < {missingness:.6f} "
        f"THEN NULL ELSE ({value_expr}) END"
    )


def _numeric_expr(i: str, salt: int, distribution: str, *, scale: float, mean: float) -> str:
    if distribution == "normal":
        core = f"{mean} + {scale} * {_std_normal(i, salt)}"
    else:  # skewed → log-normal: exp(sigma·z) gives the right heavy tail
        core = f"exp(0.9 * {_std_normal(i, salt)}) * {scale}"
    return f"round({core}, 2)"


def _categorical_expr(i: str, salt: int, card: int, prefix: str) -> str:
    """``prefix_<k>`` where k ∈ [0, card): a bounded, index-stable id set so the
    distinct-count / value-count metrics see exactly ``card`` categories."""
    return f"concat('{prefix}_', CAST(floor({_u01(i, salt)} * {card}) AS INTEGER))"


def _stage_list_sql() -> str:
    """The label set as a DuckDB list literal (single-quoted, escaped), indexed
    1-based by ``_categorical``-style ``floor(u * n) + 1`` for the low-cardinality
    ``stage`` column."""
    return "[" + ", ".join("'" + s.replace("'", "''") + "'" for s in _STAGE_LABELS) + "]"


def _temporal_expr(i: str, salt: int) -> str:
    """A date in a ~4-year window derived from a deterministic uniform — the
    temporal family (range / coverage) needs real DATE values (ENGINE §3)."""
    return (
        f"CAST(DATE '2020-01-01' + INTERVAL (CAST(floor({_u01(i, salt)} * 1460) "
        f"AS INTEGER)) DAY AS DATE)"
    )


def _build_columns(spec: DatasetSpec) -> List[Tuple[str, str]]:
    """Return the ``(column_name, sql_expression)`` list for a spec. The first
    five are the fixed core kinds; any remaining slots alternate skewed numeric /
    high-cardinality category so a wide fixture still exercises every family.

    ``card`` is clamped to the row count so a tiny fixture never claims more
    distinct values than rows."""
    i = "i"
    card = max(2, min(_CARDINALITY_TIERS[spec.cardinality], spec.rows))
    seed_off = spec.seed * 1_000_003  # shift the whole salt space so seeds decorrelate
    cols: List[Tuple[str, str]] = [
        # (column_name, sql_expression); missingness applied inside each expr.
        ("pk_id", f"({i})::BIGINT + 1"),
        (
            "amount",
            _missing_guard(
                i, 11 + seed_off, spec.missingness,
                _numeric_expr(i, 11 + seed_off, spec.distribution, scale=5000.0, mean=12000.0),
            ),
        ),
        (
            "stage",
            _missing_guard(
                i, 21 + seed_off, spec.missingness,
                f"({_stage_list_sql()})[CAST(floor({_u01(i, 21 + seed_off)} * "
                f"{len(_STAGE_LABELS)}) AS INTEGER) + 1]",
            ),
        ),
        (
            "category",
            _missing_guard(
                i, 31 + seed_off, spec.missingness,
                _categorical_expr(i, 31 + seed_off, card, "cat"),
            ),
        ),
        ("event_date", _missing_guard(i, 41 + seed_off, spec.missingness, _temporal_expr(i, 41 + seed_off))),
    ]
    # Fill remaining columns: even idx → numeric, odd → category, distinct salts.
    extra = spec.columns - len(cols)
    for k in range(extra):
        salt = 100 + k * 7 + seed_off
        if k % 2 == 0:
            name = f"metric_{k}"
            expr = _missing_guard(
                i, salt, spec.missingness,
                _numeric_expr(i, salt, spec.distribution, scale=100.0, mean=250.0),
            )
        else:
            name = f"group_{k}"
            expr = _missing_guard(
                i, salt, spec.missingness,
                _categorical_expr(i, salt, max(2, card // 2), f"g{k}"),
            )
        cols.append((name, expr))
    return cols


def generate_dataset(
    spec: DatasetSpec,
    out_path: "Path | str",
    *,
    fmt: str = "csv",
    con: "duckdb.DuckDBPyConnection | None" = None,
) -> Path:
    """Materialise one synthetic fixture to ``out_path`` and return its path.

    ``fmt`` ∈ ``{csv, parquet}`` — the only two MVP source formats (ADR-011).
    The entire table is produced by a single DuckDB ``COPY`` over ``range(n)``;
    Python holds no rows (ADR-007). The extension is enforced to match ``fmt``
    so a downstream connector auto-detects the right reader.
    """
    out = Path(out_path)
    expected_suffix = ".csv" if fmt == "csv" else ".parquet"
    if out.suffix.lower() != expected_suffix:
        out = out.with_suffix(expected_suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    cols = _build_columns(spec)
    select_list = ", ".join(f'({expr}) AS "{name}"' for name, expr in cols)
    query = f"SELECT {select_list} FROM range({spec.rows}) t(i)"
    if fmt == "csv":
        copy_opts = "(HEADER, DELIMITER ',')"
    elif fmt == "parquet":
        copy_opts = "(FORMAT PARQUET)"
    else:
        raise ValueError(f"unknown fmt {fmt!r}; expected 'csv' or 'parquet'")

    own_con = con is None
    con = con if con is not None else duckdb_session.connect()
    try:
        # one parallel-safe statement; nothing crosses into Python memory
        con.execute(f"COPY ({query}) TO '{str(out).replace(chr(39), chr(39)*2)}' {copy_opts}")
    finally:
        if own_con:
            con.close()
    return out


def column_names(spec: DatasetSpec) -> List[str]:
    """The generated fixture's column names, in file order (used to build a
    matching §9 dataset-config)."""
    return [name for name, _ in _build_columns(spec)]


def write_dataset_config(
    spec: DatasetSpec,
    config_path: "Path | str",
    *,
    dataset_name: str = "bench_ds",
    mode: str = "standard",
) -> Path:
    """Emit a §9 ``DatasetConfig`` JSON matching a generated fixture (schema
    ``additionalProperties=false`` requires only known keys; at least one
    quality check is mandatory per ``dataset_config.schema.json``).

    ``pk_id`` is declared as the primary key (a uniqueness check) and the
    ``amount`` numeric column carries a completeness ``not_null`` check — both
    reference real generated columns, so the config validates before profiling
    (AGENTS §5). The caller (harness) layers ``profiling.mode`` /
    ``profiling.sampling`` overrides on top of this base document."""
    out = Path(config_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    config: Dict[str, object] = {
        "config_version": 3,
        "dataset": {
            "name": dataset_name,
            "grain": "one_row_per_benchmark_entity",
            "primary_key": ["pk_id"],
        },
        "profiling": {"mode": mode},
        "quality_checks": [
            {
                "id": "pk_unique",
                "rule": "unique",
                "column": "pk_id",
                "dimension": "uniqueness",
                "severity": "critical",
            },
            {
                "id": "amount_present",
                "rule": "not_null",
                "column": "amount",
                "dimension": "completeness",
                "severity": "high",
            },
        ],
    }
    out.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return out


def spec_dict(spec: DatasetSpec) -> Dict[str, object]:
    """Plain-dict view of a spec, for embedding in a benchmark results doc so a
    recorded baseline states exactly which workload produced it (TESTING §4.4:
    numbers + hardware are product evidence)."""
    return asdict(spec)


__all__ = [
    "DatasetSpec",
    "generate_dataset",
    "write_dataset_config",
    "column_names",
    "spec_dict",
]
