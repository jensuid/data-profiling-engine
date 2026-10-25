"""Owned DuckDB connection + session settings (M7-T2c; ADR-021).

Every DuckDB connection the engine opens goes through :func:`connect`, so two
resource ceilings are never left to DuckDB's machine-derived defaults:

* ``memory_limit`` — DuckDB's default is ~80 % of physical RAM (measured
  ``12.7 GiB`` on a 16 GB box). Left unowned, peak worker RSS tracks the working
  set: the M7-T2a ladder measured 677 MB at 10M rows and 3 948 MB at 100M
  (TESTING §4.3.3) — sub-linear, but extrapolating to ~22.5 GiB at 1B rows, i.e.
  OOM. The engine default is ``min(4 GiB, 25 % of RAM)``, floored by
  :func:`memory_limit_floor_bytes` so the quantile metrics stay computable, so
  one worker leaves room for the UI process it runs beside (ADR-006) and for a
  second concurrent job (TESTING §4.1).
* ``temp_directory`` / ``max_temp_directory_size`` — for an *in-memory* database
  DuckDB reports ``temp_directory = '.tmp'``, i.e. relative to the **current
  working directory**. Under PyInstaller the CWD can sit inside or beside the
  application bundle, which SECURITY §2 forbids for anything user-writable, and
  an uncapped spill can fill the volume. The default is therefore the
  OS-standard temporary workspace (:func:`storage.paths.default_temp_root`) under
  a private ``duckdb_scratch/`` dir, with the spill size capped.

Spill blocks are derived, re-creatable fragments of user data: they never hold
the only copy of anything (SECURITY §2 — cache/temp may be cleared at any time),
and the directory is created owner-only (``0700``, SECURITY §4 file-access
scope).

Ceilings are overridable without touching code — via the env vars
``DPE_DUCKDB_MEMORY_LIMIT`` / ``DPE_DUCKDB_TEMP_DIR`` /
``DPE_DUCKDB_MAX_TEMP_DIR_SIZE`` (the packaged app's settings and every test
path use these or the explicit kwargs). An override is handed to DuckDB verbatim
and then **read back**: a ceiling that DuckDB will not accept fails loudly at
connect time instead of silently profiling with the machine default.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Union

import duckdb

#: Env overrides (the packaging/test hook, mirrors ``storage.paths`` policy).
MEMORY_LIMIT_ENV = "DPE_DUCKDB_MEMORY_LIMIT"
TEMP_DIR_ENV = "DPE_DUCKDB_TEMP_DIR"
MAX_TEMP_SIZE_ENV = "DPE_DUCKDB_MAX_TEMP_DIR_SIZE"

#: Engine default ceiling: a quarter of physical RAM, never above 4 GiB (ADR-021).
MEMORY_LIMIT_FRACTION = 0.25
MEMORY_LIMIT_CAP_BYTES = 4 * 1024 ** 3

#: DuckDB's quantile implementation allocates a fixed 128 MiB buffer that it
#: cannot spill (measured 2.0.0: ``median``/``quantile_cont`` under a 256 MB
#: ``memory_limit`` raise ``OutOfMemoryException: failed to allocate data of size
#: 128.0 MiB``; at 512 MB the same query succeeds). Since quantiles back the
#: distribution/median metrics, the floor must keep at least one buffer per worker
#: thread alive *plus* room for the rest of the query — hence ``2 × threads ×
#: buffer``, rounded into a sane band. Going below it does not make the profile
#: smaller, it makes quantile metrics ``ERROR`` (still never a wrong number).
QUANTILE_BUFFER_BYTES = 128 * 1024 ** 2
MEMORY_LIMIT_FLOOR_MIN_BYTES = 512 * 1024 ** 2

#: Default spill cap — bounded so a profile can never fill the user's disk.
MAX_TEMP_DIR_BYTES = 8 * 1024 ** 3

#: Sub-directory of the OS temp workspace holding DuckDB spill blocks.
SCRATCH_SUBDIR = "duckdb_scratch"

#: Settings this module is responsible for applying to every connection.
SETTING_KEYS = ("memory_limit", "temp_directory", "max_temp_directory_size")

#: DuckDB reports sizes rounded to one decimal in its chosen unit, so a read-back
#: may be marginally below what was requested. Tolerance for :func:`apply_settings`.
_READBACK_TOLERANCE = 0.01

_SIZE_RE = re.compile(
    r"^\s*(-?[0-9]*\.?[0-9]+)\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB|PiB)?\s*$", re.IGNORECASE
)
_UNITS = {
    "B": 1,
    "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
    "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4,
    "PIB": 1024 ** 5,
}


class SessionConfigError(ValueError):
    """A memory/spill ceiling DuckDB refused to accept, or a value it cannot parse."""


def parse_size(value: Union[int, float, str]) -> int:
    """Normalise a size (bytes int, or ``'512MB'`` / ``'4.0 GiB'`` literal) to bytes.

    Decimal (KB/MB/GB) and binary (KiB/MiB/GiB) units are both honoured, matching
    what DuckDB itself accepts, so an override written for DuckDB parses here the
    same way. A sign is accepted so :func:`format_size` round-trips; whether a
    negative size is *legal* is decided by :func:`validate_settings`, in one place.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    match = _SIZE_RE.match(str(value))
    if not match:
        raise SessionConfigError(f"cannot parse size {value!r} (expected e.g. 512MB or 4GiB)")
    number, unit = match.groups()
    return int(float(number) * _UNITS[(unit or "B").upper()])


def format_size(value: Union[int, float, str]) -> str:
    """Render a size for a DuckDB ``SET`` statement as an exact byte literal."""
    return f"{parse_size(value)} B"


def _ram_bytes_darwin() -> Optional[int]:
    raw = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5
    ).stdout.strip()
    return int(raw) if raw.isdigit() else None


def _ram_bytes_windows() -> Optional[int]:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(status)
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.ullTotalPhys)
    return None


@lru_cache(maxsize=1)
def total_ram_bytes() -> Optional[int]:
    """Physical RAM in bytes, or ``None`` when it cannot be determined.

    Cached: a connection is opened per source probe, and the macOS path shells
    out to ``sysctl`` — RAM cannot change inside one process.

    macOS has no ``os.sysconf('SC_PHYS_PAGES')`` (measured), so ``sysctl`` is the
    only reliable source there; Linux exposes ``/proc/meminfo``; Windows the
    ``GlobalMemoryStatusEx`` API. Never a guessed number: when all three fail the
    caller falls back to the absolute cap.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            return _ram_bytes_darwin()
        if system == "Windows":
            return _ram_bytes_windows()
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def default_thread_count() -> int:
    """DuckDB's default worker threads (= cores), used only to size the floor."""
    return max(1, os.cpu_count() or 1)


def memory_limit_floor_bytes(threads: Optional[int] = None) -> int:
    """Smallest ceiling that still lets the engine compute quantile metrics.

    ``2 × threads × QUANTILE_BUFFER_BYTES`` (1 GiB on a 4-core box), clamped into
    ``[512 MiB, cap]``. Measured justification: a full 10M-row profile under 256 MB
    finished as ``SUCCEEDED`` but with **28 per-task ERRORs** — every quantile/
    median metric — because the 128 MiB quantile buffer cannot be satisfied."""
    workers = threads if threads is not None else default_thread_count()
    derived = 2 * max(1, workers) * QUANTILE_BUFFER_BYTES
    return max(MEMORY_LIMIT_FLOOR_MIN_BYTES, min(derived, MEMORY_LIMIT_CAP_BYTES))


def default_memory_limit_bytes() -> int:
    """The ADR-021 ceiling: ``min(4 GiB, 25 % of RAM)``, floored by
    :func:`memory_limit_floor_bytes`, rounded down to whole MiB so the value we
    request is exactly what DuckDB reports back (it displays one decimal).
    Never above physical RAM: a larger pool could not be enforced anyway."""
    ram = total_ram_bytes()
    requested = MEMORY_LIMIT_CAP_BYTES if ram is None else int(ram * MEMORY_LIMIT_FRACTION)
    capped = max(memory_limit_floor_bytes(), min(requested, MEMORY_LIMIT_CAP_BYTES))
    if ram is not None:
        capped = min(capped, int(ram * 0.9))  # a pool at 100 % of RAM is not usable
    return (capped // (1024 ** 2)) * (1024 ** 2)


def default_scratch_dir() -> Path:
    """Owner-only DuckDB spill workspace inside the OS-standard temp dir.

    Lazily imports :mod:`data_profiling_engine.storage.paths` so this module
    stays importable from the connector layer without a package cycle."""
    from data_profiling_engine.storage import paths

    return paths.ensure_private_dir(paths.default_temp_root() / SCRATCH_SUBDIR)


def _env_or_none(name: str) -> Optional[str]:
    """An env override that is unset *or* empty (docker/CI write ``VAR=``)
    means "no override" — never a value handed to DuckDB as an empty literal."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def resolve_settings(
    *,
    memory_limit: Optional[Union[int, str]] = None,
    temp_directory: Optional[Union[str, "os.PathLike[str]"]] = None,
    max_temp_directory_size: Optional[Union[int, str]] = None,
) -> Dict[str, str]:
    """Effective session settings: explicit kwarg ▸ env override ▸ engine default."""
    limit = memory_limit if memory_limit is not None else _env_or_none(MEMORY_LIMIT_ENV)
    if limit is None:
        limit = default_memory_limit_bytes()
    temp = temp_directory if temp_directory is not None else _env_or_none(TEMP_DIR_ENV)
    if temp is None:
        temp = default_scratch_dir()
    max_temp = (
        max_temp_directory_size
        if max_temp_directory_size is not None
        else _env_or_none(MAX_TEMP_SIZE_ENV)
    )
    if max_temp is None:
        max_temp = MAX_TEMP_DIR_BYTES
    return {
        "memory_limit": format_size(limit),
        "temp_directory": str(Path(os.fspath(temp)).expanduser()),
        "max_temp_directory_size": format_size(max_temp),
    }


def current_settings(con: duckdb.DuckDBPyConnection) -> Dict[str, Optional[str]]:
    """What the connection actually reports for the settings we own."""
    observed: Dict[str, Optional[str]] = {}
    for key in SETTING_KEYS:
        try:
            observed[key] = con.execute(f"SELECT current_setting('{key}')").fetchone()[0]
        except duckdb.Error:
            observed[key] = None
    return observed


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


#: Smallest usable buffer-pool ceiling: below this DuckDB cannot give every
#: thread its minimum working buffers (measured: a 0/negative limit raises an
#: OutOfMemoryException at ``SET``).
MIN_MEMORY_LIMIT_BYTES = 1024 ** 2


def validate_settings(settings: Dict[str, str]) -> Dict[str, str]:
    """Reject a ceiling that would silently un-bound the engine.

    DuckDB 2.0.0 accepts values that defeat the purpose of the setting, so they
    are refused here instead of being handed to it: an absurdly large
    ``memory_limit`` is effectively no ceiling at all, a negative
    ``max_temp_directory_size`` wraps to ~16 384 PiB (measured), and a relative
    ``temp_directory`` lands spill blocks in the process CWD — the exact hazard
    :func:`default_scratch_dir` exists to remove."""
    limit = parse_size(settings["memory_limit"])
    if limit < MIN_MEMORY_LIMIT_BYTES:
        raise SessionConfigError(
            f"memory_limit {limit} B is below the {MIN_MEMORY_LIMIT_BYTES} B minimum"
        )
    ram = total_ram_bytes()
    if ram is not None and limit > ram:
        raise SessionConfigError(
            f"memory_limit {limit} B exceeds physical RAM ({ram} B); it could never be "
            "enforced, so it would silently mean 'no ceiling'"
        )
    max_temp = parse_size(settings["max_temp_directory_size"])
    if max_temp <= 0:
        raise SessionConfigError(
            f"max_temp_directory_size must be positive, got {max_temp} B "
            "(DuckDB wraps a negative value into an unbounded cap)"
        )
    if not Path(settings["temp_directory"]).is_absolute():
        raise SessionConfigError(
            f"temp_directory {settings['temp_directory']!r} is relative — it would resolve "
            "against the process CWD, possibly inside the app bundle (SECURITY §2)"
        )
    return settings


def apply_settings(
    con: duckdb.DuckDBPyConnection,
    *,
    memory_limit: Optional[Union[int, str]] = None,
    temp_directory: Optional[Union[str, "os.PathLike[str]"]] = None,
    max_temp_directory_size: Optional[Union[int, str]] = None,
) -> Dict[str, Optional[str]]:
    """Install the ceilings on ``con`` and verify DuckDB accepted them.

    Raises :class:`SessionConfigError` when a ceiling is refused by
    :func:`validate_settings`, when DuckDB rejects the ``SET`` (it raises
    ``OutOfMemoryException``/``ParserException``, both :class:`duckdb.Error`), or
    when the read-back does not match what was asked for."""
    settings = validate_settings(
        resolve_settings(
            memory_limit=memory_limit,
            temp_directory=temp_directory,
            max_temp_directory_size=max_temp_directory_size,
        )
    )
    try:
        con.execute(f"SET memory_limit={_sql_literal(settings['memory_limit'])}")
        con.execute(f"SET temp_directory={_sql_literal(settings['temp_directory'])}")
        con.execute(
            f"SET max_temp_directory_size={_sql_literal(settings['max_temp_directory_size'])}"
        )
    except duckdb.Error as exc:
        raise SessionConfigError(f"DuckDB refused the session ceilings: {exc}") from exc

    observed = current_settings(con)
    for key in ("memory_limit", "max_temp_directory_size"):
        requested = parse_size(settings[key])
        if observed[key] is None:
            raise SessionConfigError(f"{key} could not be read back from the connection")
        applied = parse_size(observed[key])
        if applied > requested:
            raise SessionConfigError(
                f"{key} is {observed[key]} ({applied} B), above the requested {requested} B"
            )
        if applied < requested * (1 - _READBACK_TOLERANCE):
            raise SessionConfigError(
                f"{key} was clamped to {observed[key]} ({applied} B), far below the "
                f"requested {requested} B"
            )
    return observed


def connect(
    *,
    memory_limit: Optional[Union[int, str]] = None,
    temp_directory: Optional[Union[str, "os.PathLike[str]"]] = None,
    max_temp_directory_size: Optional[Union[int, str]] = None,
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with the engine's ceilings applied.

    The read-only-against-sources rule (SECURITY §2, ADR-007) is unchanged: this
    only bounds how much RAM/disk a scan may take, never what it may write."""
    con = duckdb.connect()
    try:
        apply_settings(
            con,
            memory_limit=memory_limit,
            temp_directory=temp_directory,
            max_temp_directory_size=max_temp_directory_size,
        )
    except Exception:
        con.close()
        raise
    return con


__all__ = [
    "MEMORY_LIMIT_ENV",
    "TEMP_DIR_ENV",
    "MAX_TEMP_SIZE_ENV",
    "MEMORY_LIMIT_FRACTION",
    "MEMORY_LIMIT_CAP_BYTES",
    "MEMORY_LIMIT_FLOOR_MIN_BYTES",
    "QUANTILE_BUFFER_BYTES",
    "default_thread_count",
    "memory_limit_floor_bytes",
    "MAX_TEMP_DIR_BYTES",
    "SCRATCH_SUBDIR",
    "SessionConfigError",
    "parse_size",
    "format_size",
    "total_ram_bytes",
    "default_memory_limit_bytes",
    "default_scratch_dir",
    "resolve_settings",
    "validate_settings",
    "current_settings",
    "apply_settings",
    "connect",
]
