"""``profiler-cli`` — headless engine entry point (M2-T11; GATE M2).

The spine the whole M2 engine path terminates in: a one-shot, no-UI command
that opens a CSV/Parquet source, runs discovery → planning → the metric /
quality / findings / recommendation pipeline (M2-T4..T10), folds the result
into the frozen §2 / §3.1 contract rollups (``orchestration.rollup``), and
writes the canonical JSON document (``reporting``) — GATE M2 / BLUEPRINT
Phase 1.

No HTTP / localhost / dev server (ADR-005); the CLI runs the compute in the
calling process against DuckDB (heavy work stays off any UI thread — there is
no UI here). MVP connectors are CSV + Parquet only (ADR-011).

Usage::

    python -m data_profiling_engine.cli profile \\
        --input sales_deals.csv --config config.yaml \\
        --mode standard --out profile.json [--dataset-id ds] [--run-id RID]

``--config`` is the §9 dataset-config in its YAML **authoring** form (BLUEPRINT
Phase 1 ``--config x.yaml``); a JSON document also loads, since JSON is a
strict subset of YAML and the machine contract — the bridge/worker payload —
is the same validated object. A config that fails schema validation is
rejected before any profiling starts (AGENTS §5).
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import List, Optional

import yaml

from data_profiling_engine import schemas as S
from data_profiling_engine.connectors.base import ConnectorError
from data_profiling_engine.orchestration.rollup import profile_dataset
from data_profiling_engine.reporting import write_profile_json
from data_profiling_engine.reporting.csv_export import write_issue_csv
from data_profiling_engine.reporting.html_report import write_html_report
from data_profiling_engine.schemas.models import DatasetConfig, ProfileMode

_CONFIG_MODES = tuple(m.value for m in ProfileMode)


class CliError(Exception):
    """User-facing CLI failure (bad config, unreadable source). Carries an
    exit code; never a stack trace to stdout (SECURITY §5)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def load_config(path: "Path | str") -> DatasetConfig:
    """Read + validate a §9 dataset-config file. Parsed with the YAML loader
    (a JSON document is valid YAML, so both authoring forms load). Structural
    schema check first (``dataset_config.schema.json``), then the pydantic
    model — an invalid config is rejected here, before any compute (AGENTS §5)."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CliError(f"config is not valid YAML/JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CliError("config must be a mapping object")
    errors = S.validate("dataset_config.schema.json", document)
    if errors:
        raise CliError("config failed schema validation:\n  - " + "\n  - ".join(errors))
    try:
        return DatasetConfig.model_validate(document)
    except Exception as exc:  # pydantic ValidationError
        raise CliError(f"config failed model validation: {exc}") from exc


def _derive_dataset_id(input_path: Path) -> str:
    return input_path.stem or "dataset"


def _new_run_id() -> str:
    # ULID/UUIDv7 is time-sortable (CONTRACTS §1); a v4 hex is a fine unique
    # CLI run id — the bridge/job manager supplies ordered ids in M5.
    return str(uuid.uuid4())


def run_profile(
    input_path: "Path | str",
    config_path: "Path | str",
    out_path: "Path | str",
    *,
    mode: Optional[str] = None,
    dataset_id: Optional[str] = None,
    run_id: Optional[str] = None,
    allowed_roots: Optional[List[Path]] = None,
    html_out: Optional["Path | str"] = None,
    csv_out: Optional["Path | str"] = None,
) -> Path:
    """Execute one profiling run and write the canonical JSON. Returns the
    output path. ``mode`` overrides ``config.profiling.mode`` when given.
    ``html_out`` additionally emits the self-contained HTML report
    (ENGINE §10, M4-T2). ``csv_out`` additionally emits the CSV issue export
    (ENGINE §10, M4-T3) — one row per failing/warning finding→check pair.
    Both carry only aggregate/identifier data, PII-safe by construction
    (SECURITY §4)."""
    config = load_config(config_path)
    if mode is not None:
        if mode not in _CONFIG_MODES:
            raise CliError(f"unknown --mode {mode!r}; expected one of {_CONFIG_MODES}")
        payload = json.loads(config.model_dump_json())
        payload["profiling"]["mode"] = mode
        config = load_config_from_dict(payload)

    src = Path(input_path)
    ds_id = dataset_id or _derive_dataset_id(src)
    rid = run_id or _new_run_id()
    out = Path(out_path)

    bundle = profile_dataset(
        ds_id, src, config, rid,
        allowed_roots=allowed_roots,
        result_refs={"profile_json": str(out)},
    )
    written = write_profile_json(bundle, out)
    if html_out is not None:
        write_html_report(bundle, html_out)
    if csv_out is not None:
        write_issue_csv(bundle, csv_out)
    return written


def load_config_from_dict(document: dict) -> DatasetConfig:
    """Validate an already-parsed config dict (schema + model), same rejection
    policy as :func:`load_config`."""
    errors = S.validate("dataset_config.schema.json", document)
    if errors:
        raise CliError("config failed schema validation:\n  - " + "\n  - ".join(errors))
    try:
        return DatasetConfig.model_validate(document)
    except Exception as exc:  # pydantic ValidationError
        raise CliError(f"config failed model validation: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="profiler-cli",
        description="Data Profiling Engine — headless profiling CLI (M2, no UI).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile", help="profile a CSV/Parquet source to JSON")
    profile.add_argument("--input", required=True, help="path to the .csv / .parquet source")
    profile.add_argument("--config", required=True, help="path to the §9 dataset-config JSON")
    profile.add_argument(
        "--mode", choices=_CONFIG_MODES, default=None,
        help="override config.profiling.mode",
    )
    profile.add_argument("--out", required=True, help="path to write the canonical JSON result")
    profile.add_argument("--dataset-id", default=None, help="dataset id (default: input stem)")
    profile.add_argument("--run-id", default=None, help="run id (default: generated uuid)")
    profile.add_argument(
        "--allow-root", action="append", dest="allowed_roots", default=None,
        help="restrict source access to this root (repeatable; path allow-list)",
    )
    profile.add_argument(
        "--html", dest="html_out", default=None,
        help="also write the self-contained offline HTML report (M4-T2) to this path",
    )
    profile.add_argument(
        "--csv", dest="csv_out", default=None,
        help="also write the CSV issue export (M4-T3, ENGINE §10) to this path",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "profile":  # pragma: no cover - argparse enforces the subcommand
        return 2
    allowed_roots = [Path(r) for r in (args.allowed_roots or [])]
    try:
        out = run_profile(
            args.input, args.config, args.out,
            mode=args.mode, dataset_id=args.dataset_id, run_id=args.run_id,
            allowed_roots=allowed_roots, html_out=args.html_out,
            csv_out=args.csv_out,
        )
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except ConnectorError as exc:
        # §8 registry code surfaces in the message; the process exit code stays
        # a small int (ConnectorError.code is an ErrorCode, not an exit status).
        print(f"error [{exc.code.value}]: {exc.message}", file=sys.stderr)
        return 2
    print(f"profiled -> {out}")
    if args.html_out:
        print(f"html report -> {args.html_out}")
    if args.csv_out:
        print(f"issue csv -> {args.csv_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
