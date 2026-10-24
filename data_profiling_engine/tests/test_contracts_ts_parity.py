"""M1-T5 — TypeScript contract mirror parity against the canonical JSON Schemas.

CONTRACTS §1 requires the front-end to mirror the contracts "generated from /
validated against the canonical JSON Schemas"; ``frontend/src/types/contracts.ts``
is that TS mirror of ``schemas/models.py``. This test keeps the two honest: it
parses the TS source and asserts, for every root schema, that

  * the declared interface property names == the canonical ``properties`` set,
  * the non-optional fields == the canonical ``required`` set,
  * every root-level ``enum`` property is typed with a named TS enum whose value
    tuple matches the canonical enum exactly (order included), and
  * a strict root (``additionalProperties: false``) carries no index signature.

Parsing is deliberately line-oriented and relies on the machine-readable format
documented at the top of ``contracts.ts`` (one field per line, ``<Name>Values``
enum tuples, ``?`` for optional, ``| null`` for nullable, ``[key: string]`` index
signature for open blocks). If the TS file drifts from that format the extractor
raises rather than silently passing.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from data_profiling_engine import schemas as S

CONTRACTS_TS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "contracts.ts"
)

# schema file -> root TS interface name (the object the schema's top level maps to)
ROOT_INTERFACES = {
    "provenance.schema.json": "Provenance",
    "profiling_run.schema.json": "ProfilingRun",
    "dataset_profile.schema.json": "DatasetProfile",
    "column_profile.schema.json": "ColumnProfile",
    "quality_check_result.schema.json": "QualityCheckResult",
    "finding.schema.json": "Finding",
    "finding_workflow.schema.json": "FindingWorkflow",
    "recommendation.schema.json": "Recommendation",
    "dataset_config.schema.json": "DatasetConfig",
}

_ENUM_RE = re.compile(
    r"export const (\w+)Values\s*=\s*\[(.*?)\]\s*as const;", re.DOTALL
)
_INTERFACE_RE = re.compile(
    r"export interface (\w+)\s*\{(.*?)\n\}", re.DOTALL
)
_FIELD_RE = re.compile(r"^\s*([A-Za-z_]\w*)(\?)?:\s*(.+?);\s*$")
_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_INDEX_SIG_RE = re.compile(r"^\s*\[key: string\]:")


def _source() -> str:
    assert CONTRACTS_TS.is_file(), f"missing TS mirror: {CONTRACTS_TS}"
    return CONTRACTS_TS.read_text(encoding="utf-8")


def _parse_enums(text: str) -> dict[str, list[str]]:
    """Return {EnumName: [values...]} from the `<Name>Values` const tuples."""
    out: dict[str, list[str]] = {}
    for name, body in _ENUM_RE.findall(text):
        out[name] = _STR_RE.findall(body)
    return out


def _parse_interfaces(text: str) -> dict[str, dict]:
    """Return {Interface: {"fields": [(name, optional, type)], "open": bool}}."""
    out: dict[str, dict] = {}
    for name, body in _INTERFACE_RE.findall(text):
        fields: list[tuple[str, bool, str]] = []
        is_open = False
        for line in body.splitlines():
            if not line.strip() or line.strip().startswith("*") or line.strip().startswith("/**"):
                continue
            if _INDEX_SIG_RE.match(line):
                is_open = True
                continue
            m = _FIELD_RE.match(line)
            if m:
                fname, opt, ftype = m.group(1), m.group(2), m.group(3).strip()
                fields.append((fname, opt is not None, ftype))
        out[name] = {"fields": fields, "open": is_open}
    return out


@pytest.fixture(scope="module")
def ts_text() -> str:
    return _source()


@pytest.fixture(scope="module")
def ts_enums(ts_text: str) -> dict[str, list[str]]:
    enums = _parse_enums(ts_text)
    assert enums, "no <Name>Values enums parsed — TS format drifted from contract"
    return enums


@pytest.fixture(scope="module")
def ts_interfaces(ts_text: str) -> dict[str, dict]:
    ifaces = _parse_interfaces(ts_text)
    for schema_name, iface in ROOT_INTERFACES.items():
        assert iface in ifaces, f"root interface {iface!r} missing ({schema_name})"
    return ifaces


def test_all_18_contract_enums_are_mirrored(ts_enums: dict[str, list[str]]):
    """Every enum declared in models.py must exist on the TS side too."""
    expected = {
        "MetricMethod", "ProfileMode", "ReportLanguage", "RunStatus", "SourceKind",
        "SemanticType", "UnitStatus", "QualityDimension", "CheckStatus", "Severity",
        "ThresholdOperator", "FindingConfidence", "HypothesisStatus",
        "WorkflowStatus", "ReasonType", "RecommendationStatus", "RuleType",
        "ConditionOperator",
    }
    assert set(ts_enums) == expected


def test_top_level_property_names_match_canonical(ts_interfaces):
    for schema_name, iface in ROOT_INTERFACES.items():
        canon = set(S.RAW_SCHEMAS[schema_name]["properties"])
        mine = {name for name, _, _ in ts_interfaces[iface]["fields"]}
        assert mine == canon, (
            schema_name,
            "only-in-ts", mine - canon,
            "only-in-schema", canon - mine,
        )


def test_required_sets_match_canonical(ts_interfaces):
    for schema_name, iface in ROOT_INTERFACES.items():
        canon = set(S.RAW_SCHEMAS[schema_name].get("required", []))
        mine = {name for name, optional, _ in ts_interfaces[iface]["fields"] if not optional}
        assert mine == canon, (schema_name, mine ^ canon)


def test_strict_roots_have_no_index_signature(ts_interfaces):
    for schema_name, iface in ROOT_INTERFACES.items():
        assert S.RAW_SCHEMAS[schema_name]["additionalProperties"] is False, schema_name
        assert ts_interfaces[iface]["open"] is False, (
            f"{iface} must be strict (canonical additionalProperties=false)"
        )


@pytest.mark.parametrize("schema_name", sorted(ROOT_INTERFACES))
def test_root_enum_properties_use_matching_named_ts_enums(schema_name, ts_interfaces, ts_enums):
    props = S.RAW_SCHEMAS[schema_name]["properties"]
    iface = ROOT_INTERFACES[schema_name]
    fields = {name: ftype for name, _, ftype in ts_interfaces[iface]["fields"]}
    checked = 0
    for prop, spec in props.items():
        enum_values = spec.get("enum")
        if not enum_values:
            continue
        checked += 1
        ftype = fields[prop]
        assert ftype in ts_enums, (
            f"{schema_name}.{prop} should be typed with a named TS enum, got {ftype!r}"
        )
        assert ts_enums[ftype] == enum_values, (
            f"{schema_name}.{prop}: {ts_enums[ftype]} != {enum_values}"
        )
    # sanity: the schema set we know has these root enums, so the loop did work
    if schema_name == "provenance.schema.json":
        assert checked == 1


def test_every_interface_property_type_resolves(ts_interfaces):
    """No typo'd field type: every referenced contract identifier is defined.

    Value-typing is free-form (JsonValue / primitives / arrays), so this only
    guards against referencing a contract interface or enum that isn't declared.
    Contract interfaces/enums are always PascalCase, so only PascalCase tokens
    are checked (lowercase `string`/`key` and TS builtins like `Array` are not
    contract references).
    """
    interface_names = set(ts_interfaces)
    enum_names = {
        m.group(1)
        for m in re.finditer(r"export const (\w+)Values", _source())
    }
    resolvable = interface_names | enum_names | {"JsonValue"}
    builtins = {"Array", "Record"}
    for iface, meta in ts_interfaces.items():
        for name, _, ftype in meta["fields"]:
            # PascalCase tokens only == references to declared contract types
            for tok in re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", ftype):
                if tok in builtins:
                    continue
                assert tok in resolvable, f"{iface}.{name}: unknown type token {tok!r}"
