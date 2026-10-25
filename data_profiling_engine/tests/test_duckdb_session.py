"""Reference tests for the owned DuckDB ceilings (M7-T2c; ADR-021).

Two things are verified independently, per TESTING §3.1 (we own the engine, so
nothing here trusts DuckDB's defaults to be the intended ones):

* the *policy* — pure arithmetic over a faked RAM size, no connection opened;
* the *effect* — a real connection reading its own settings back, and an
  aggregation whose result is checked against a pure-Python reference computed
  under a deliberately tight memory limit.

Spill *throughput* at scale is NOT asserted here: DuckDB's ``duckdb_memory()``
counters report live buffers only and read ``0`` once a query finishes
(measured), so the end-to-end evidence that RSS stops tracking dataset size is
the ladder re-measure recorded in TESTING §4.3.3, not a unit test.
"""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Dict

import pytest

import duckdb

from data_profiling_engine import duckdb_session as session

_GIB = 1024 ** 3
_MIB = 1024 ** 2


# ---------------------------------------------------------------- policy (pure)


@pytest.mark.parametrize(
    "value, expected",
    [
        (1024, 1024),
        (7.5 * _MIB, int(7.5 * _MIB)),
        ("4096", 4096),
        ("512MB", 512 * 1000 ** 2),          # decimal unit, as DuckDB parses it
        ("4GiB", 4 * _GIB),                  # binary unit
        ("4.0 GiB", int(4.0 * _GIB)),        # DuckDB's own read-back format
        (" 256 MB ", 256 * 1000 ** 2),
        ("1024 B", 1024),
        ("-5 B", -5),  # parse-only; validate_settings is what rejects it
    ],
)
def test_parse_size_accepts_decimal_binary_and_bare_bytes(value: object, expected: int) -> None:
    assert session.parse_size(value) == expected  # type: ignore[arg-type]


def test_parse_size_rejects_garbage_loudly() -> None:
    with pytest.raises(session.SessionConfigError):
        session.parse_size("lots")
    with pytest.raises(session.SessionConfigError):
        session.parse_size("")


@pytest.mark.parametrize(
    "ram_bytes, expected_limit",
    [
        (64 * _GIB, session.MEMORY_LIMIT_CAP_BYTES),                  # capped
        (16 * _GIB, 4 * _GIB),                                        # exactly the cap
        (8 * _GIB, 2 * _GIB),                                         # 25 % of RAM
        # 25 % would be 128 MiB — below the quantile floor — and the floor is
        # then held under 90 % of RAM, so a tiny box still gets a usable pool
        (512 * _MIB, (int(512 * _MIB * 0.9) // _MIB) * _MIB),
        (None, session.MEMORY_LIMIT_CAP_BYTES),                       # unknown RAM
    ],
)
def test_default_memory_limit_is_quarter_ram_capped_and_floored(
    monkeypatch: pytest.MonkeyPatch, ram_bytes: object, expected_limit: int
) -> None:
    monkeypatch.setattr(session, "total_ram_bytes", lambda: ram_bytes)  # type: ignore[arg-type]
    monkeypatch.setattr(session, "default_thread_count", lambda: 4)
    assert session.default_memory_limit_bytes() == expected_limit


@pytest.mark.parametrize(
    "threads, expected_floor",
    [
        (1, session.MEMORY_LIMIT_FLOOR_MIN_BYTES),        # 2x1x128 MiB < 512 MiB band
        (2, 2 * 2 * session.QUANTILE_BUFFER_BYTES),       # 512 MiB
        (4, 1024 * _MIB),                                 # 1 GiB on this dev box
        (16, session.MEMORY_LIMIT_CAP_BYTES),             # clamped to the cap
        (64, session.MEMORY_LIMIT_CAP_BYTES),
    ],
)
def test_floor_keeps_one_quantile_buffer_per_thread(threads: int, expected_floor: int) -> None:
    """Measured: a 256 MB ceiling makes median/quantile_cont raise
    ``OutOfMemoryException: failed to allocate data of size 128.0 MiB``, which is
    how the 10M tight pass ended with 28 per-task ERRORs instead of a smaller
    profile. The floor is therefore derived from threads, not a magic constant."""
    assert session.memory_limit_floor_bytes(threads) == expected_floor
    assert session.memory_limit_floor_bytes(threads) >= 2 * session.QUANTILE_BUFFER_BYTES


def test_default_memory_limit_is_whole_mebibytes_for_odd_ram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DuckDB displays one decimal, so a request it cannot echo exactly would
    make the read-back check ambiguous. Round to whole MiB instead."""
    monkeypatch.setattr(session, "total_ram_bytes", lambda: 12_345_678_901)
    monkeypatch.setattr(session, "default_thread_count", lambda: 4)
    assert session.default_memory_limit_bytes() % _MIB == 0


def test_resolve_settings_precedence_kwarg_over_env_over_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(session, "total_ram_bytes", lambda: 16 * _GIB)
    monkeypatch.setenv(session.MEMORY_LIMIT_ENV, "2GiB")
    monkeypatch.setenv(session.TEMP_DIR_ENV, str(tmp_path / "env"))

    env = session.resolve_settings()
    assert session.parse_size(env["memory_limit"]) == 2 * _GIB
    assert env["temp_directory"] == str(tmp_path / "env")

    explicit = session.resolve_settings(
        memory_limit="1GiB", temp_directory=tmp_path / "explicit"
    )
    assert session.parse_size(explicit["memory_limit"]) == 1 * _GIB
    assert explicit["temp_directory"] == str(tmp_path / "explicit")


@pytest.mark.parametrize(
    "settings, fragment",
    [
        ({"memory_limit": "0 B", "temp_directory": "/tmp/x", "max_temp_directory_size": "8 B"},
         "minimum"),
        ({"memory_limit": f"{200 * _GIB} B", "temp_directory": "/tmp/x",
          "max_temp_directory_size": "8 B"}, "exceeds physical RAM"),
        ({"memory_limit": "64 B", "temp_directory": "/tmp/x", "max_temp_directory_size": "8 B"},
         "minimum"),
        ({"memory_limit": "64 MiB", "temp_directory": "/tmp/x", "max_temp_directory_size": "-5 B"},
         "positive"),
        ({"memory_limit": "64 MiB", "temp_directory": ".tmp", "max_temp_directory_size": "8 B"},
         "relative"),
    ],
)
def test_validate_settings_refuses_a_ceiling_that_would_unbound_the_engine(
    settings: Dict[str, str], fragment: str
) -> None:
    """DuckDB accepts all of these silently (a negative spill cap even wraps to
    ~16 384 PiB), so the module must not hand them over."""
    with pytest.raises(session.SessionConfigError, match=fragment):
        session.validate_settings(settings)


# ---------------------------------------------------------------- effect (live DB)


def test_connect_installs_the_requested_ceilings_and_reads_them_back(
    tmp_path: Path,
) -> None:
    con = session.connect(memory_limit="256MB", temp_directory=tmp_path)
    try:
        observed = session.current_settings(con)
    finally:
        con.close()
    # 256 MB decimal == 244.1 MiB as DuckDB displays it; never more than asked.
    assert session.parse_size(observed["memory_limit"]) <= 256 * 1000 ** 2
    assert session.parse_size(observed["memory_limit"]) >= 0.99 * 256 * 1000 ** 2
    assert Path(str(observed["temp_directory"])).resolve() == tmp_path.resolve()
    assert observed["max_temp_directory_size"] is not None


def test_default_limit_is_stricter_than_duckdb_machine_default(
    tmp_path: Path,
) -> None:
    """The whole point of ADR-021: DuckDB's own default (80 % of RAM) is not a
    ceiling we chose, and it is looser than the engine policy on any box where
    profiling shares the machine with its own UI process."""
    raw_default = duckdb.connect().execute(
        "SELECT current_setting('memory_limit')"
    ).fetchone()[0]
    ours = session.connect(temp_directory=tmp_path)
    try:
        applied = session.current_settings(ours)["memory_limit"]
    finally:
        ours.close()
    assert session.parse_size(applied) < session.parse_size(raw_default)  # type: ignore[arg-type]
    assert session.parse_size(applied) <= session.MEMORY_LIMIT_CAP_BYTES  # type: ignore[arg-type]


def test_blank_env_override_means_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``VAR=`` is how a CI/docker environment writes "unset"; it must not become
    an empty literal handed to DuckDB (or a parse error at connect)."""
    monkeypatch.setattr(session, "total_ram_bytes", lambda: 16 * _GIB)
    monkeypatch.setenv(session.MEMORY_LIMIT_ENV, "   ")
    monkeypatch.setenv(session.TEMP_DIR_ENV, "")
    settings = session.resolve_settings(temp_directory=tmp_path)
    assert session.parse_size(settings["memory_limit"]) == session.default_memory_limit_bytes()
    assert settings["temp_directory"] == str(tmp_path)


def test_default_scratch_dir_is_owner_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spill blocks are fragments of the user's data, so the default workspace
    DuckDB writes them into is created private (SECURITY §4) and lives in the OS
    temp root, never the CWD or the app bundle (SECURITY §2)."""
    monkeypatch.setattr(
        "data_profiling_engine.storage.paths.default_temp_root", lambda **kw: tmp_path
    )
    scratch = session.default_scratch_dir()
    assert scratch == (tmp_path / session.SCRATCH_SUBDIR).resolve()
    assert scratch.is_dir()
    if platform.system() != "Windows":  # ACLs are inherited there (paths.py)
        assert scratch.stat().st_mode & 0o077 == 0


def test_env_temp_dir_is_used_verbatim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scratch = tmp_path / "given"
    scratch.mkdir()
    monkeypatch.setenv(session.TEMP_DIR_ENV, str(scratch))
    con = session.connect(memory_limit="128MiB")
    try:
        observed = session.current_settings(con)["temp_directory"]
    finally:
        con.close()
    assert Path(str(observed)) == scratch


def test_invalid_override_fails_at_connect_not_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(session.MEMORY_LIMIT_ENV, "unbounded")
    with pytest.raises(session.SessionConfigError):
        session.connect(temp_directory=tmp_path)

    monkeypatch.setenv(session.MEMORY_LIMIT_ENV, f"{200 * _GIB}")
    with pytest.raises(session.SessionConfigError, match="RAM"):
        session.connect(temp_directory=tmp_path)


def test_aggregation_under_a_tight_ceiling_matches_a_python_reference(
    tmp_path: Path,
) -> None:
    """The ceiling must not corrupt or abort the computation (ENGINE §11: an
    un-executable step is an ERROR, never a wrong number). 256 MB is far below
    what the group-by would otherwise want for 200 000 distinct-ish keys."""
    rows = 200_000
    modulus = 40_009  # a prime, so keys stay highly non-trivial
    con = session.connect(memory_limit="256MB", temp_directory=tmp_path)
    try:
        got = dict(
            con.execute(
                f"SELECT (i * 2654435761) % {modulus} AS k, count(*) AS c "
                f"FROM range({rows}) t(i) GROUP BY k"
            ).fetchall()
        )
    finally:
        con.close()

    expected: Dict[int, int] = {}
    for i in range(rows):
        key = (i * 2654435761) % modulus
        expected[key] = expected.get(key, 0) + 1

    assert got == expected
    assert sum(got.values()) == rows


def test_package_never_opens_an_unbounded_connection() -> None:
    """A ceiling that can be forgotten is a ceiling that will be forgotten: the
    ladder (TESTING §4.3.3) measured RSS because five call sites each called
    ``duckdb.connect()`` directly. New code must use the helper."""
    root = Path(session.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "duckdb_session.py" or "tests" in path.parts:
            continue
        if "duckdb.connect(" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"bare duckdb.connect() in: {offenders}"


def test_session_constants_are_self_consistent() -> None:
    assert session.MEMORY_LIMIT_FLOOR_MIN_BYTES <= session.memory_limit_floor_bytes()
    assert session.memory_limit_floor_bytes() <= session.MEMORY_LIMIT_CAP_BYTES
    assert session.default_memory_limit_bytes() <= session.MEMORY_LIMIT_CAP_BYTES
    assert session.MIN_MEMORY_LIMIT_BYTES <= session.MEMORY_LIMIT_FLOOR_MIN_BYTES
