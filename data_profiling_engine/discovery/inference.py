"""Physical→logical→semantic inference + config overlay (M2-T2, ENGINE §4.1).

Rule order per column (a later rule may only *refine* an earlier one, never
silently overwrite a config declaration — ENGINE §4.1 "config wins"):

1. **Physical family → base logical + confidence** (unambiguous typed sources —
   DATE/TIMESTAMP/BOOLEAN/UUID/numeric ints/floats/decimals — settle at HIGH).
2. **VARCHAR pattern refinement** — regex over the bounded reservoir sample
   (email / url / uuid / phone / ISO date-time), plus length + cardinality
   signals to split categorical_nominal ↔ text_freeform ↔ identifier-like.
3. **Name heuristics** — ``_id`` / ``_at`` / ``amount`` etc. nudge semantic_type
   and promote a numeric column to ``numeric_currency`` (still only an
   *assumption* unless config declares the unit).
4. **Config overlay** — declared ``primary_key`` forces ``semantic_type =
   identifier`` (HIGH confidence, ``config_declared=True``); declared ``units``
   are copied verbatim into ``ColumnProfile.unit``.
5. **Unit resolution** — only ``numeric_currency`` columns get a unit block by
   default; status is ``known`` ONLY when config declared it (AGENTS §3 — never
   silently assert), else ``assumed`` if a currency symbol is present in the
   sample, else ``unknown``.

The rationale string records which rules fired so misclassification is visible
downstream (planner/findings) — the ``confidence`` field is the machine flag.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from data_profiling_engine.discovery.signals import _family
from data_profiling_engine.discovery.types import (
    ColumnDiscovery,
    ColumnSignals,
    Confidence,
    LogicalType,
)
from data_profiling_engine.schemas.models import (
    ColumnProfile,
    ColumnUnit,
    DatasetConfig,
    SemanticType,
    UnitStatus,
)

SCHEMA_VERSION = "1.0"

# --- pattern regexes (ENGINE §4.1: "regex for email/phone/date/URL/id") -----

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-().]{6,18}$")
_ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Numeric-formatted string, optionally wrapped in a currency symbol / ISO code
# and thousands separators — used only to *recognise* a currency-shaped VARCHAR,
# never to convert values (that is M2-T4's job with proper Provenance).
_NUMERIC_LIKE_RE = re.compile(
    r"^\s*(?:[$€£¥₹]|USD|EUR|GBP|JPY|IDR|AUD|CAD|CHF|CNY|INR|SGD|MYR|THB|VND)?\s*"
    r"[-+]?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:[$€£¥₹]|USD|EUR|GBP|JPY|IDR|AUD|CAD|CHF|CNY|INR|SGD|MYR|THB|VND)?\s*$"
)

#: The two probable-PII shapes of the ENGINE §4.1 vocabulary, published for the
#: consumers SECURITY §4 names (report masking, the bridge's ``dataset.preview``)
#: so a value is judged by exactly the pattern discovery would use — one source
#: of truth for "this looks like an address / a phone number".
PII_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("email", _EMAIL_RE),
    ("phone", _PHONE_RE),
)

# --- name heuristics --------------------------------------------------------

_NAME_ID = re.compile(r"(?:^|_)(?:[a-z0-9]+_)?(?:id|uuid|guid|pk)(?:$|_)|_code$", re.IGNORECASE)
_NAME_TEMPORAL = re.compile(r"(?:_at|_date|_time|_on|timestamp(?:_at)?|datetime|created|updated)$", re.IGNORECASE)
_NAME_CURRENCY = re.compile(r"(amount|price|value|cost|total|revenue|fee|salary|balance|paid|spend|sales_usd)", re.IGNORECASE)
_NAME_STATUS = re.compile(r"^(?:status|state|stage|flag|kind|type|category)$|_(?:status|state|stage|flag|type)$", re.IGNORECASE)
_NAME_BOOL = re.compile(r"^(?:is|has|can|should)_[a-z0-9_]+$", re.IGNORECASE)
_NAME_FREEFORM = re.compile(r"(description|notes?|comment|summary|body|content|message|text)", re.IGNORECASE)

# Currency symbols / ISO codes recognised in a VARCHAR sample for the "unambiguous
# symbol" clause of ENGINE §4.1 — enough to mark an *assumption*, never `known`.
_CURRENCY_TOKEN_RE = re.compile(
    r"^\s*([$€£¥₹]|USDT?|USD|EUR|GBP|JPY|IDR|AUD|CAD|CHF|CNY|RUB|KRW|SGD|MYR|THB|VND|PHP|INR|BRL|MXZ|ZAR)\b",
    re.IGNORECASE,
)
_SYMBOL_TO_ISO = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR",
}

# Thresholds — bounded and documented; small samples are treated cautiously.
_PATTERN_MIN_SAMPLE = 3
_PATTERN_HIGH_RATIO = 0.90
_PATTERN_MEDIUM_RATIO = 0.70
_CARD_DIM_MAX_DISTINCT = 25
_CARD_DIM_MAX_RATIO = 0.05
_CARD_UNIQUE_MIN_NOTNULL = 2  # ≥2 non-null rows before a "unique" claim means anything
_LEN_SHORT = 40
_LEN_LONG = 80


def _is_categorical_shape(signals: ColumnSignals) -> bool:
    """Any-type rule: some duplicates exist AND cardinality stays in the
    label-range (≤25 distinct). Works for small fixtures (n_notnull=3, distinct=2)
    and production tables (n_notnull=1M, distinct=20) alike; ratio-only
    thresholds miss one of the two regimes."""
    return (
        signals.n_notnull >= 2
        and 0 < signals.n_distinct <= _CARD_DIM_MAX_DISTINCT
        and signals.n_distinct < signals.n_notnull
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _match_ratio(sample: Tuple[str, ...], regex: re.Pattern) -> float:
    if not sample:
        return 0.0
    hits = sum(1 for s in sample if regex.match(s.strip() if s else ""))
    return hits / len(sample)


def _detect_currency_symbol(sample: Tuple[str, ...]) -> Optional[str]:
    """Return an ISO-ish code if a *majority* of the sample shares one currency
    token; None otherwise. Never returns ``known`` — that path requires config."""
    if not sample:
        return None
    hits = []
    for s in sample:
        m = _CURRENCY_TOKEN_RE.match(s or "")
        if m:
            tok = m.group(1)
            hits.append(_SYMBOL_TO_ISO.get(tok, tok.upper()))
    if not hits or len(hits) < _PATTERN_MIN_SAMPLE:
        return None
    # majority rule (unambiguous ⇒ >90% same token)
    top = max(set(hits), key=hits.count)
    if hits.count(top) / len(sample) >= _PATTERN_HIGH_RATIO:
        return top
    return None


def _base_from_physical(physical: str) -> Tuple[LogicalType, SemanticType, Confidence, str]:
    """Return (logical, semantic_default, confidence, rationale_fragment)."""
    fam = _family(physical)
    p = physical.upper()
    if fam == "bool":
        return LogicalType.BOOLEAN, SemanticType.OTHER, Confidence.HIGH, "physical BOOLEAN → boolean"
    if fam == "integer":
        return LogicalType.INTEGER, SemanticType.MEASURE, Confidence.HIGH, f"physical {p} → integer"
    if fam == "float":
        logical = LogicalType.DECIMAL if p.startswith("DECIMAL") else LogicalType.FLOAT
        return logical, SemanticType.MEASURE, Confidence.HIGH, f"physical {p} → {logical.value}"
    if fam == "temporal":
        if p.startswith("TIMESTAMP"):
            return LogicalType.TEMPORAL_DATETIME, SemanticType.TIME, Confidence.HIGH, f"physical {p} → temporal_datetime"
        if p == "DATE":
            return LogicalType.TEMPORAL_DATE, SemanticType.TIME, Confidence.HIGH, "physical DATE → temporal_date"
        if p.startswith("TIME"):
            return LogicalType.TEMPORAL_TIME, SemanticType.TIME, Confidence.HIGH, "physical TIME → temporal_time"
    if fam == "uuid":
        return LogicalType.UUID, SemanticType.IDENTIFIER, Confidence.HIGH, "physical UUID → identifier"
    if fam == "varchar":
        return LogicalType.STRING, SemanticType.TEXT, Confidence.LOW, "physical VARCHAR → string (await refinement)"
    if fam == "binary":
        return LogicalType.BINARY, SemanticType.OTHER, Confidence.HIGH, "physical BLOB → binary"
    if fam == "nested":
        return LogicalType.NESTED, SemanticType.OTHER, Confidence.HIGH, "physical nested → nested"
    return LogicalType.UNKNOWN, SemanticType.OTHER, Confidence.LOW, f"physical {p} → unknown"


def _refine_varchar(
    name: str, signals: ColumnSignals
) -> Tuple[LogicalType, SemanticType, Confidence, str]:
    """Pattern + length + cardinality refinement for a VARCHAR column."""
    sample = signals.sample
    n_pat = len(sample)
    parts: list[str] = []
    # Strong format patterns (require ≥3 sample values to avoid single-value false hits).
    if n_pat >= _PATTERN_MIN_SAMPLE:
        for regex, logical, semantic, tag in (
            (_EMAIL_RE, LogicalType.EMAIL, SemanticType.IDENTIFIER, "email"),
            (_UUID_RE, LogicalType.UUID, SemanticType.IDENTIFIER, "uuid"),
            (_URL_RE, LogicalType.URL, SemanticType.TEXT, "url"),
            (_PHONE_RE, LogicalType.PHONE, SemanticType.IDENTIFIER, "phone"),
        ):
            ratio = _match_ratio(sample, regex)
            if ratio >= _PATTERN_HIGH_RATIO:
                parts.append(f"{tag} pattern {ratio:.0%}")
                return logical, semantic, Confidence.HIGH, "; ".join(parts)
            if ratio >= _PATTERN_MEDIUM_RATIO:
                parts.append(f"{tag} pattern {ratio:.0%}")
                return logical, semantic, Confidence.MEDIUM, "; ".join(parts)
        # ISO date / datetime embedded as string
        dt_ratio = _match_ratio(sample, _ISO_DATETIME_RE)
        d_ratio = _match_ratio(sample, _ISO_DATE_RE)
        if dt_ratio >= _PATTERN_HIGH_RATIO:
            return LogicalType.TEMPORAL_DATETIME, SemanticType.TIME, Confidence.HIGH, f"ISO datetime pattern {dt_ratio:.0%}"
        if dt_ratio >= _PATTERN_MEDIUM_RATIO:
            return LogicalType.TEMPORAL_DATETIME, SemanticType.TIME, Confidence.MEDIUM, f"ISO datetime pattern {dt_ratio:.0%}"
        if d_ratio >= _PATTERN_HIGH_RATIO:
            return LogicalType.TEMPORAL_DATE, SemanticType.TIME, Confidence.HIGH, f"ISO date pattern {d_ratio:.0%}"
        if d_ratio >= _PATTERN_MEDIUM_RATIO:
            return LogicalType.TEMPORAL_DATE, SemanticType.TIME, Confidence.MEDIUM, f"ISO date pattern {d_ratio:.0%}"

    # Numeric-shaped VARCHAR with a currency-flavoured name → promote to
    # numeric_currency so _resolve_unit can pick a known/assumed/unknown status.
    if (
        n_pat >= _PATTERN_MIN_SAMPLE
        and _NAME_CURRENCY.search(name)
        and _match_ratio(sample, _NUMERIC_LIKE_RE) >= _PATTERN_HIGH_RATIO
    ):
        return (
            LogicalType.NUMERIC_CURRENCY,
            SemanticType.MEASURE,
            Confidence.MEDIUM,
            "VARCHAR currency-name + numeric-formatted sample → numeric_currency",
        )

    # Name-driven temporal on VARCHAR (e.g. "created_at" stored as text).
    if _NAME_TEMPORAL.search(name) and n_pat >= _PATTERN_MIN_SAMPLE and (dt_ratio_any(sample) >= _PATTERN_MEDIUM_RATIO):
        return LogicalType.TEMPORAL_DATETIME, SemanticType.TIME, Confidence.MEDIUM, "name=*_at/_date + partial ISO parse"

    max_len = signals.max_len or 0
    ratio = signals.cardinality_ratio
    n_distinct = signals.n_distinct

    # Low-cardinality short strings → categorical.
    if max_len <= _LEN_SHORT and _is_categorical_shape(signals):
        sem = SemanticType.STATUS if _NAME_STATUS.search(name) else SemanticType.DIMENSION
        return LogicalType.CATEGORICAL_NOMINAL, sem, Confidence.MEDIUM, (
            f"short (max_len={max_len}) + duplicate low-cardinality ({n_distinct}/{signals.n_notnull}) → categorical_nominal"
        )
    # Long free-form text.
    if max_len >= _LEN_LONG or _NAME_FREEFORM.search(name):
        return LogicalType.TEXT_FREEFORM, SemanticType.TEXT, Confidence.MEDIUM, (
            f"long text (max_len={max_len}) or free-form name → text_freeform"
        )
    # High-cardinality, near-unique → identifier-like string id.
    if (
        signals.n_notnull >= _CARD_UNIQUE_MIN_NOTNULL
        and n_distinct == signals.n_notnull
        and (ratio >= 0.9 or _NAME_ID.search(name))
    ):
        return LogicalType.STRING, SemanticType.IDENTIFIER, Confidence.MEDIUM, (
            f"near-unique string (distinct={n_distinct}) → identifier candidate"
        )
    # Name-driven identifier (e.g. ``external_ref``).
    if _NAME_ID.search(name):
        return LogicalType.STRING, SemanticType.IDENTIFIER, Confidence.MEDIUM, "name matches id/code heuristic"
    return LogicalType.STRING, SemanticType.TEXT, Confidence.LOW, "VARCHAR default: no strong signal"


def dt_ratio_any(sample: Tuple[str, ...]) -> float:
    """Combined ISO date / datetime match rate — used for the *name temporal* path."""
    if not sample:
        return 0.0
    return sum(1 for s in sample if _ISO_DATE_RE.match(s) or _ISO_DATETIME_RE.match(s)) / len(sample)


def _nudge_numeric_semantic(
    name: str, logical: LogicalType, signals: ColumnSignals
) -> Tuple[LogicalType, SemanticType, Confidence, str]:
    """Integer/float/decimal column: currency name / id name / low-cardinality dimension."""
    if _NAME_ID.search(name):
        return logical, SemanticType.IDENTIFIER, Confidence.MEDIUM, f"numeric + name matches id/code → identifier (logical={logical.value})"
    if _NAME_CURRENCY.search(name):
        return LogicalType.NUMERIC_CURRENCY, SemanticType.MEASURE, Confidence.MEDIUM, f"numeric + currency-name heuristic → numeric_currency (unit unconfirmed)"
    ratio = signals.cardinality_ratio
    if _is_categorical_shape(signals):
        sem = SemanticType.STATUS if _NAME_STATUS.search(name) else SemanticType.DIMENSION
        return (
            LogicalType.CATEGORICAL_NOMINAL,
            sem,
            Confidence.MEDIUM,
            f"integer/float with duplicate low-cardinality ({signals.n_distinct}/{signals.n_notnull}) → categorical_nominal ({sem.value})",
        )
    return logical, SemanticType.MEASURE, Confidence.HIGH, f"numeric measure (logical={logical.value})"


def _nudge_boolean_semantic(name: str) -> Tuple[SemanticType, Confidence, str]:
    if _NAME_BOOL.match(name) or _NAME_STATUS.search(name):
        return SemanticType.STATUS, Confidence.MEDIUM, "boolean with is_/has_/status name → status"
    return SemanticType.OTHER, Confidence.HIGH, "boolean → other"


def _nudge_temporal_semantic(_name: str) -> Tuple[SemanticType, Confidence, str]:
    return SemanticType.TIME, Confidence.HIGH, "temporal → time"


def _resolve_unit(
    logical: LogicalType,
    signals: ColumnSignals,
    config: Optional[DatasetConfig],
    name: str,
    config_declared: bool,
) -> Tuple[Optional[ColumnUnit], str]:
    """ENGINE §4.1: only ``known`` when config declares it; else assumed/unknown.
    Non-currency columns carry no unit block at all."""
    if config and config.dataset.units and name in config.dataset.units:
        assertion = config.dataset.units[name]
        unit = ColumnUnit(**assertion.model_dump(exclude_unset=True))
        return unit, f"unit from config (status={unit.status})"
    if logical is not LogicalType.NUMERIC_CURRENCY:
        return None, ""
    if signals.sample:
        iso = _detect_currency_symbol(signals.sample)
        if iso:
            return ColumnUnit(status=UnitStatus.ASSUMED, currency=iso), (
                f"currency symbol detected → assumed {iso}"
            )
    return ColumnUnit(status=UnitStatus.UNKNOWN), "numeric_currency without unit declaration → unknown"


# ---------------------------------------------------------------------------
# top-level per-column inference
# ---------------------------------------------------------------------------


def infer_column(
    name: str,
    ordinal: int,
    physical: str,
    signals: ColumnSignals,
    config: Optional[DatasetConfig] = None,
    pk_columns: Optional[frozenset] = None,
) -> ColumnDiscovery:
    """Apply the ENGINE §4.1 rule cascade and emit a ColumnProfile skeleton."""
    logical, semantic, confidence, rationale = _base_from_physical(physical)
    fam = _family(physical)

    if fam == "varchar":
        logical, semantic, conf2, r2 = _refine_varchar(name, signals)
        rationale = f"{rationale}; {r2}" if r2 else rationale
        # Confidence takes the *higher* of the two paths for the VARCHAR branch
        # — physical alone was LOW; the refinement may promote it.
        confidence = conf2

    elif fam in ("integer", "float"):
        logical, semantic, conf2, r2 = _nudge_numeric_semantic(name, logical, signals)
        rationale = f"{rationale}; {r2}"
        if conf2 is not Confidence.HIGH:
            confidence = conf2  # only demote from HIGH-physical when the nudge is heuristic

    elif fam == "bool":
        semantic, conf2, r2 = _nudge_boolean_semantic(name)
        rationale = f"{rationale}; {r2}"
        confidence = conf2

    elif fam == "temporal":
        semantic, conf2, r2 = _nudge_temporal_semantic(name)
        rationale = f"{rationale}; {r2}"
        confidence = conf2

    # --- config overlay: primary_key declaration wins over inference -------
    config_declared = False
    if pk_columns and name in pk_columns:
        semantic = SemanticType.IDENTIFIER
        confidence = Confidence.HIGH
        config_declared = True
        rationale += "; declared primary_key (config wins) → identifier"

    unit, unit_note = _resolve_unit(logical, signals, config, name, config_declared)
    if unit_note:
        if unit is not None and str(unit.status) == UnitStatus.KNOWN.value:
            config_declared = True
            confidence = Confidence.HIGH
        rationale += f"; {unit_note}"

    profile = ColumnProfile(
        schema_version=SCHEMA_VERSION,
        column=name,
        ordinal=ordinal,
        physical_type=physical,
        logical_type=logical.value,
        semantic_type=semantic,
        unit=unit,
    )
    return ColumnDiscovery(
        profile=profile,
        logical=logical,
        confidence=confidence,
        rationale=rationale,
        signals=signals,
        config_declared=config_declared,
    )


__all__ = [
    "infer_column",
    "SCHEMA_VERSION",
    "PII_PATTERNS",
]
