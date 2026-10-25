"""M7-T6 (ADR-023 Option A) — the packaged §8.1 no-port self-check.

``main._run_ports_smoke`` runs inside the frozen app, where pywebview starts its
internal bottle HTTP server to serve the ``file://`` SPA. Option A keeps the
"no port bound" guarantee meaningful while permitting exactly that one loopback
static-file listener: the offenders are computed by pure helpers (no subprocess,
no socket) so they are unit-testable here, and a guard test pins the dependency
behaviour the whole exception rests on — pywebview binds loopback and its HTTP
``/js_api`` route stays inert (js_api rides the native message handler, so no
data or bridge traffic ever crosses the socket).
"""
from __future__ import annotations

import types

import pytest

from data_profiling_engine import main
from data_profiling_engine.main import (
    _LOOPBACK_HOSTS,
    _parse_listener_lines,
    _pywebview_loopback_endpoint,
    _select_port_offenders,
    _split_host_port,
)

# A representative pywebview static-file port (loopback) and a look-alike rogue.
PYWEBVIEW_PORT = 44212
APP_PID = 2376


# ---------------------------------------------------------------------------
# 1. token parsing
# ---------------------------------------------------------------------------


def test_split_host_port_ipv4_ipv6_and_rejects() -> None:
    assert _split_host_port("127.0.0.1:44212") == ("127.0.0.1", 44212)
    assert _split_host_port("[::1]:8080") == ("[::1]", 8080)
    assert _split_host_port("*:80") == ("*", 80)
    # no numeric port / no separator is not a listener we can classify
    assert _split_host_port("*:http") == (None, None)
    assert _split_host_port("nosColon") == (None, None)


# ---------------------------------------------------------------------------
# 2. TCP-table line parsing — macOS lsof and Windows netstat shapes
# ---------------------------------------------------------------------------


def test_parse_mac_lsof_rows() -> None:
    lines = [
        "COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME",
        "DataProfi %d jensu  4u  IPv4 0xabcd    0t0     TCP  127.0.0.1:%d (LISTEN)"
        % (APP_PID, PYWEBVIEW_PORT),
        "Python    %d jensu  7u  IPv4 0xef01    0t0     TCP  127.0.0.1:5555 (LISTEN)"
        % APP_PID,
        "evil      9999 root  3u  IPv4 0x1234   0t0     TCP  *:9999 (LISTEN)",
    ]
    assert _parse_listener_lines(lines, win=False) == [
        (APP_PID, "127.0.0.1", PYWEBVIEW_PORT),
        (APP_PID, "127.0.0.1", 5555),
        (9999, "*", 9999),
    ]


def test_parse_win_netstat_rows() -> None:
    lines = [
        "Active Internet connections (only servers)",
        "Proto Local Address      Foreign Address    State       PID",
        "TCP    0.0.0.0:135        0.0.0.0:0          LISTENING   1000",
        "TCP    127.0.0.1:%d   0.0.0.0:0      LISTENING   %d" % (PYWEBVIEW_PORT, APP_PID),
        "UDP    0.0.0.0:5353       *:*                            1000",
    ]
    # UDP + non-LISTENING rows and the header are skipped.
    assert _parse_listener_lines(lines, win=True) == [
        (1000, "0.0.0.0", 135),
        (APP_PID, "127.0.0.1", PYWEBVIEW_PORT),
    ]


# ---------------------------------------------------------------------------
# 3. offender selection — only the exact pywebview loopback tuple is permitted
# ---------------------------------------------------------------------------


def _app_pids() -> set[str]:
    return {str(APP_PID)}


def test_allowed_pywebview_socket_is_permitted_but_a_second_one_is_not() -> None:
    listeners = [
        (APP_PID, "127.0.0.1", PYWEBVIEW_PORT),
        (APP_PID, "127.0.0.1", 5555),   # an extra loopback listener we did NOT expect
        (9999, "*", 9999),              # another process — out of scope
    ]
    offenders = _select_port_offenders(
        listeners, app_pids=_app_pids(), allowed=("127.0.0.1", PYWEBVIEW_PORT)
    )
    assert offenders == [(APP_PID, "127.0.0.1", 5555)]


def test_no_allow_list_means_our_own_sockets_all_offend() -> None:
    # No pywebview server running (allowed=None): the very same socket is a
    # genuine §8.1 violation, so the check keeps its teeth against a real bug.
    listeners = [(APP_PID, "127.0.0.1", PYWEBVIEW_PORT)]
    offenders = _select_port_offenders(listeners, app_pids=_app_pids(), allowed=None)
    assert offenders == listeners


def test_non_loopback_is_never_permitted_even_if_the_tuple_matches() -> None:
    # Defence in depth: a matching (host, port) that is not loopback is still an
    # offender — Option A permits only a *loopback* transport.
    listeners = [(APP_PID, "0.0.0.0", PYWEBVIEW_PORT)]
    offenders = _select_port_offenders(
        listeners, app_pids=_app_pids(), allowed=("0.0.0.0", PYWEBVIEW_PORT)
    )
    assert offenders == listeners


# ---------------------------------------------------------------------------
# 4. the pywebview endpoint is derived from the live server, loopback-gated
# ---------------------------------------------------------------------------


def test_pywebview_endpoint_none_without_a_server(monkeypatch) -> None:
    import webview.http as wvhttp

    monkeypatch.setattr(wvhttp, "global_server", None, raising=False)
    assert _pywebview_loopback_endpoint() is None


def test_pywebview_endpoint_reads_loopback_and_refers_non_loopback(monkeypatch) -> None:
    import webview.http as wvhttp

    monkeypatch.setattr(
        wvhttp, "global_server",
        types.SimpleNamespace(address=f"http://127.0.0.1:{PYWEBVIEW_PORT}/"),
        raising=False,
    )
    assert _pywebview_loopback_endpoint() == ("127.0.0.1", PYWEBVIEW_PORT)

    # A non-loopback bind is refused (→ treated as an offender, §8.1 fails).
    monkeypatch.setattr(
        wvhttp, "global_server",
        types.SimpleNamespace(address=f"http://0.0.0.0:{PYWEBVIEW_PORT}/"),
        raising=False,
    )
    assert _pywebview_loopback_endpoint() is None


# ---------------------------------------------------------------------------
# 5. guard: the dependency behaviour the whole exception rests on
# ---------------------------------------------------------------------------


def test_pywebview_binds_loopback_and_http_js_api_is_inert() -> None:
    """The Option-A exception is only safe while (a) the adapter defaults to a
    loopback bind and (b) the HTTP ``/js_api`` route can never dispatch (its
    ``js_callback`` table is empty by default). If a pywebview/bottle upgrade
    flips either, this fails and ADR-023 must move to Option B."""
    import webview.http as wvhttp

    # (a) the threaded WSGI adapter pywebview runs binds 127.0.0.1 by default.
    assert wvhttp.ThreadedAdapter().host == "127.0.0.1"
    # (b) a fresh server has no js_api callbacks registered → the POST route is
    #     inert, so no callable facade (and no data) crosses the socket.
    assert wvhttp.BottleServer().js_callback == {}


def test_loopback_host_set_covers_v4_and_v6_forms() -> None:
    for host in ("127.0.0.1", "::1", "localhost", "[::1]"):
        assert host in _LOOPBACK_HOSTS
    assert "0.0.0.0" not in _LOOPBACK_HOSTS
    assert "*" not in _LOOPBACK_HOSTS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
