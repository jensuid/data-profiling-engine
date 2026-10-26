"""M7-T5 — frozen-bundle package-data guard (SECURITY §6.2).

The packaged walk of TESTING §5.1 died at ``profile.start`` with
``BRIDGE_REQUEST_INVALID: handler failed: KeyError``: PyInstaller shipped
``data_profiling_engine/reporting/assets`` but **not**
``data_profiling_engine/schemas``. :data:`schemas.RAW_SCHEMAS` is built by
globbing the *package directory*, so in the frozen app the registry was empty and
the first ``S.validate("dataset_config.schema.json", …)`` raised ``KeyError``.
Dev mode and every unit test stayed green — the defect only exists in a bundle,
which is why the gate is specified on the packaged build.

Two things are pinned here:

* every runtime package-asset directory is named in ``data_profiling_engine.spec``
  (a newly added asset dir cannot be forgotten; dev-only bench baselines are
  exempt), and
* ``--smoke`` reports ``SCHEMAS_SMOKE_RESULT`` / ``SCHEMAS_SMOKE_FAILED``, so the
  next regression of this class shows up in the §5.2 marker list instead of at a
  user's first Start Profile.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from data_profiling_engine import schemas as S

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "data_profiling_engine"
SCHEMA_DIR = PACKAGE_DIR / "schemas"
SPEC_PATH = REPO_ROOT / "data_profiling_engine.spec"

#: Package asset directories the FROZEN app never reads. The benchmark baselines
#: are CLI/dev artefacts and the benchmark module is not bundled at all.
DEV_ONLY_ASSET_DIRS = {PACKAGE_DIR / "benchmark"}


def _runtime_asset_dirs() -> list[Path]:
    """Directories under the package holding non-.py files a run may read."""
    dirs: set[Path] = set()
    for path in PACKAGE_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(PACKAGE_DIR)
        if {"tests", "__pycache__"} & set(rel.parts):
            continue
        if path.suffix in {".py", ".pyc"} or path.name == ".DS_Store":
            continue
        if path.parent in DEV_ONLY_ASSET_DIRS:
            continue
        dirs.add(path.parent)
    return sorted(dirs)


def test_the_guard_actually_sees_the_package_assets() -> None:
    """Guard the guard: an empty scan would make the coverage test vacuous."""
    names = {p.relative_to(PACKAGE_DIR).as_posix() for p in _runtime_asset_dirs()}
    assert "schemas" in names
    assert "reporting/assets" in names


@pytest.mark.parametrize(
    "asset_dir", _runtime_asset_dirs(), ids=lambda p: p.relative_to(PACKAGE_DIR).as_posix()
)
def test_runtime_asset_dir_is_collected_by_the_spec(asset_dir: Path) -> None:
    spec = SPEC_PATH.read_text(encoding="utf-8")
    rel = asset_dir.relative_to(PACKAGE_DIR).as_posix()
    slashed = f"data_profiling_engine/{rel}"
    dotted = "data_profiling_engine." + rel.replace("/", ".")
    assert slashed in spec or dotted in spec, (
        f"package data {slashed!r} is read at runtime but data_profiling_engine.spec "
        "never collects it — add it to the spec's ``datas`` (SECURITY §6.2)"
    )


def test_required_schemas_exist_on_disk_and_are_loaded() -> None:
    """``REQUIRED_SCHEMAS`` is what the smoke marker checks for; the registry must
    load every file on disk so presence and usability cannot drift apart."""
    on_disk = {p.name for p in SCHEMA_DIR.glob("*.schema.json")}
    assert set(S.REQUIRED_SCHEMAS) <= on_disk
    assert set(S.RAW_SCHEMAS) == on_disk


def test_schemas_smoke_reports_success(capsys: pytest.CaptureFixture[str]) -> None:
    from data_profiling_engine import main

    main._run_schemas_smoke()
    out = capsys.readouterr().out
    assert out.startswith(
        f"SCHEMAS_SMOKE_RESULT: count={len(S.RAW_SCHEMAS)} "
        f"required={len(S.REQUIRED_SCHEMAS)} probe_errors="
    ), out


def test_schemas_smoke_fails_when_a_schema_was_not_collected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from data_profiling_engine import main

    # Exactly the frozen-bundle state: the directory glob found nothing.
    monkeypatch.setattr(S, "RAW_SCHEMAS", {})
    main._run_schemas_smoke()
    out = capsys.readouterr().out
    assert out.startswith("SCHEMAS_SMOKE_FAILED: missing required schema(s)"), out
    assert "dataset_config.schema.json" in out


def test_schemas_smoke_fails_when_the_registry_loads_but_is_not_applied(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from data_profiling_engine import main

    monkeypatch.setattr(S, "validate", lambda *_a, **_kw: [])
    main._run_schemas_smoke()
    out = capsys.readouterr().out
    assert out.startswith("SCHEMAS_SMOKE_FAILED: dataset_config.schema.json accepted"), out
