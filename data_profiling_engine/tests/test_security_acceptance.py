"""M7-T4 — security acceptance automation (SECURITY §8; TESTING §5.4).

SECURITY §8 lists five acceptance criteria and TESTING §5.4 requires each of
them to be *verified as automated tests*. This module is that automation: one
test class per §8 bullet, in bullet order, so a reviewer can tick the doc line
by line. Deep behavioural coverage already lives in the dedicated suites
(``test_bridge_facade`` / ``test_bridge_read_commands`` / ``test_reporting_html``
/ ``test_storage_*``); this file re-asserts the *acceptance claim* end-to-end
and adds the parts that had no automated check until now:

* §8.1 no bound port — static scan of every shipped source (engine Python +
  frontend TS/HTML) for listener/server primitives, a live process-tree scan
  while a real ``profile.start`` worker job runs, and a guard that the
  packaged ``--smoke`` path keeps its ``PORTS_SMOKE_*`` self-check marker
  (ADR-005; verified on the frozen build via the M7-T3 runbook §1).
* §8.2 bridge rejects unknown/invalid/out-of-scope/oversized — all four
  rejection classes through the packaged facade (spike commands OFF), each
  with the no-input-echo guarantee.
* §8.3 PII masked in default HTML/CSV export — a REAL engine run over a tiny
  email/phone fixture, artifacts read back FROM DISK (not the in-memory
  string), asserting raw values absent + masked shapes present.
* §8.4 credentials absent from every log/result sample — no credential
  surface exists in the MVP contracts; a planted lookalike secret survives
  nowhere: not in any bridge response, not in the persisted run document,
  not in any artifact byte, not in the SQLite file. (Phase-2 native-store
  handling is out of MVP scope per SECURITY §5.)
* §8.5 upgrade preserves user data; uninstall preserves it unless opted-in —
  the persistence layer survives a schema migration (rows identical +
  pre-migration backup), the automatic cleaners never touch referenced
  RESULT/EXPORT files, the data dir is refused inside the app bundle, and
  the shipped installer scripts (NSIS + macOS shell) delete no user data.

Nothing here changes engine behaviour; production-code changes are the
``main.py --smoke`` port marker only.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from data_profiling_engine.bridge import BridgeFacade, PAYLOAD_CAP_BYTES, configure_runtime
from data_profiling_engine.bridge import errors
from data_profiling_engine.bridge import facade as facade_mod
from data_profiling_engine.orchestration.rollup import profile_dataset
from data_profiling_engine.reporting import result_document
from data_profiling_engine.reporting.csv_export import emit_issue_csv_artifact
from data_profiling_engine.reporting.html_report import emit_html_report_artifact
from data_profiling_engine.schemas.models import DatasetConfig
from data_profiling_engine.storage import paths as sp
from data_profiling_engine.storage.artifact_store import (
    ArtifactStore,
    RetentionPolicy,
    retention_for,
)
from data_profiling_engine.storage.metadata_store import MetadataStore
from data_profiling_engine.storage.migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PKG = REPO_ROOT / "data_profiling_engine"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
PACKAGING = REPO_ROOT / "packaging"
FIXTURES = ENGINE_PKG / "tests" / "fixtures" / "golden"

#: Sentinel "credential" planted in rejected inputs. SECURITY §8 bullet 4 /
#: §5: a value that looks like a secret must never be echoed by the bridge nor
#: persisted by any store. (MVP file-only sources carry no real credentials.)
SECRET = "hunter2-SECACCEPT-7c41"
#: Raw PII values written into the §8.3 fixture source file. The default
#: HTML/CSV exports may not contain either, in any form.
RAW_EMAIL = "jane.doe@acme.com"
RAW_EMAIL_2 = "ceo@acme.com"
RAW_PHONE = "+62 812-3456-7890"
RAW_PII_VALUES = (RAW_EMAIL, RAW_EMAIL_2, "budi.santoso@nama.id",
                  RAW_PHONE, "+62 811-2222-3334", "+62 813-9999-0001")

RUN_ID = "run-m7t4"


def _send(facade: BridgeFacade, command: str, payload: Dict[str, Any], rid: str = "req-1"):
    return facade.bridge_send({"command": command, "request_id": rid, "payload": payload})


@pytest.fixture()
def runtime_root(tmp_path):
    """Bind the bridge context to a throwaway user-data root (never the real
    one), restore the OS default afterwards — same seam as ``test_bridge_profile``."""
    configure_runtime(tmp_path)
    yield tmp_path
    configure_runtime(None)


# ---------------------------------------------------------------------------
# §8.1 — "No port bound while app runs (automated check)."
# ---------------------------------------------------------------------------

#: Server/listener primitives that must never appear in shipped code (AGENTS §2
#: / ADR-005: "No HTTP/TCP server, ever"). The worker's multiprocessing pipes
#: are not TCP listeners: the bind/listen patterns require the socket-shaped
#: two-paren bind or a listen() call, the import pattern the server stack.
_SERVER_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(socket|socketserver|http(?:\.server)?|fastapi|flask"
    r"|uvicorn|gunicorn|aiohttp|tornado|starlette|werkzeug)\b",
    re.MULTILINE,
)
_BIND_CALL = re.compile(r"\.bind\s*\(\s*\(")          # socket.bind((host, port))
_LISTEN_CALL = re.compile(r"\.listen\s*\(")           # backlog-bearing listen()
_SHELL_LISTENER = re.compile(r"\b(?:netcat|socat)\b")

#: Frontend network primitives forbidden in the shipped app (ADR-005: all
#: host comms go through the pywebview bridge; assets load from ``file://``,
#: ADR-010).
_FETCH_CALL = re.compile(r"\bfetch\s*\(")
_XHR = re.compile(r"\bXMLHttpRequest\b")
_WS = re.compile(r"\bnew\s+WebSocket\b")
_EVENT_SOURCE = re.compile(r"\bnew\s+EventSource\b")
_URL_LITERAL = re.compile(r"[\"'`](?:https?|ws|wss)://")
_LOOPBACK = re.compile(r"\b(?:localhost|127\.0\.0\.1)\b")


def _engine_sources(*, include_tests: bool = False):
    """Every engine ``.py`` outside __pycache__; this acceptance module itself
    is excluded (it carries the forbidden patterns as *scan strings*)."""
    for path in sorted(ENGINE_PKG.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "test_security_acceptance.py":
            continue
        if not include_tests and "tests" in path.parts:
            continue
        yield path


class TestNoBoundPorts:
    def test_engine_ships_no_server_or_listener_primitives(self) -> None:
        offenders: list[str] = []
        for path in _engine_sources(include_tests=True):
            text = path.read_text(encoding="utf-8")
            for pattern in (_SERVER_IMPORT, _BIND_CALL, _LISTEN_CALL, _SHELL_LISTENER):
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern.pattern}")
        assert offenders == [], "ADR-005 violation (listener primitive): " + "; ".join(offenders)

    def test_frontend_ships_no_network_calls(self) -> None:
        files = [
            *sorted(FRONTEND_SRC.rglob("*.ts")),
            *sorted(FRONTEND_SRC.rglob("*.tsx")),
            REPO_ROOT / "frontend" / "index.html",
        ]
        offenders: list[str] = []
        for path in files:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in (_FETCH_CALL, _XHR, _WS, _EVENT_SOURCE, _URL_LITERAL, _LOOPBACK):
                if pattern.search(text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern.pattern}")
        assert offenders == [], (
            "ADR-005/ADR-010 violation (frontend network literal): " + "; ".join(offenders)
        )

    def test_host_entry_spawns_only_the_read_only_ports_probe(self) -> None:
        """The desktop host shells out nowhere except the SECURITY §8 port
        self-check (a read-only ``lsof``/``netstat`` TABLE lookup); any other
        ``subprocess`` use in ``main.py`` is an ADR-005 regression."""
        text = (ENGINE_PKG / "main.py").read_text(encoding="utf-8")
        span_start = text.index("def _run_ports_smoke")
        span_end = text.index("\ndef ", span_start + 1)
        probe = text[span_start:span_end]
        assert "subprocess.run" in probe
        assert "lsof" in probe and "netstat" in probe  # both OS table tools
        outside = text[:span_start] + text[span_end:]
        assert outside.count("subprocess") == 1, "main.py spawns outside the ports probe"
        assert "import subprocess" in outside  # the lone module-level import

    def test_packaged_smoke_carries_the_ports_self_check(self) -> None:
        """The §8.1 'while app runs' claim on the PACKAGED build is the smoke
        marker (runbook §1); a refactor must not silently drop it."""
        text = (ENGINE_PKG / "main.py").read_text(encoding="utf-8")
        assert "PORTS_SMOKE_RESULT" in text and "PORTS_SMOKE_FAILED" in text
        assert "_run_ports_smoke()" in text  # wired into the --smoke sequence

    def test_no_listening_tcp_socket_during_a_real_bridge_job(
        self, runtime_root: Path
    ) -> None:
        """Live check: while a real ``profile.start`` runs in its spawn worker
        (ADR-006), neither the host process nor the worker may hold a TCP
        socket in LISTEN state (ADR-005)."""
        psutil = pytest.importorskip("psutil")

        def _listeners() -> list[str]:
            me = psutil.Process()
            found: list[str] = []
            for proc in [me, *me.children(recursive=True)]:
                try:
                    for conn in proc.net_connections(kind="inet"):
                        if conn.status == psutil.CONN_LISTEN:
                            found.append(f"pid={proc.pid} {conn.laddr.ip}:{conn.laddr.port}")
                except psutil.Error:
                    continue  # process vanished mid-scan; the tree is re-walked
            return found

        facade = BridgeFacade()  # packaged facade: spike off
        config = json.loads((FIXTURES / "sales_deals.config.json").read_text("utf-8"))
        resp = _send(facade, "profile.start", {
            "dataset_id": "sales_deals",
            "source_path": str(FIXTURES / "sales_deals.csv"),
            "config": config,
            "allowed_roots": [str(FIXTURES)],
        })
        assert resp["status"] == "accepted", resp
        job_id = resp["job_id"]

        seen_listeners: list[str] = []
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            seen_listeners.extend(_listeners())
            status = _send(facade, "profile.get_status", {"job_id": job_id})
            if status["result"]["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.05)
        else:
            pytest.fail("job never reached a terminal state")
        seen_listeners.extend(_listeners())
        assert seen_listeners == [], (
            "SECURITY §8 bullet 1 — app bound a port while running: "
            + ", ".join(seen_listeners)
        )


# ---------------------------------------------------------------------------
# §8.2 — "Bridge rejects unknown commands, bad schemas, out-of-scope paths,
#         oversized payloads."  (packaged facade, spike commands OFF)
# ---------------------------------------------------------------------------


class TestBridgeRejection:
    """Every rejection is also a no-echo check: ``SECRET`` planted in the
    input must not appear anywhere in the typed §8 error response
    (SECURITY §3 — errors carry no request values)."""

    @pytest.fixture()
    def facade(self) -> BridgeFacade:
        return BridgeFacade()

    @pytest.mark.parametrize("command", [
        "drop_table", "eval", "os.system", "fs.unlink",
        "echo", "worker_echo_job",  # spike commands are OFF in a packaged build
    ])
    def test_unknown_commands_are_rejected(self, facade: BridgeFacade, command: str) -> None:
        resp = _send(facade, command, {"message": SECRET})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.COMMAND_UNKNOWN
        assert SECRET not in json.dumps(resp)

    @pytest.mark.parametrize("bad", [
        pytest.param({"command": "dataset.inspect", "request_id": "r",
                      "payload": {"source_path": 7}}, id="payload-wrong-type"),
        pytest.param({"command": "dataset.inspect", "request_id": "r",
                      "payload": {"source_path": "/tmp/x.csv", "sql": f"DROP {SECRET}"}},
                     id="payload-unknown-field"),
        pytest.param({"command": "dataset.inspect", "payload": {}},
                     id="envelope-missing-field"),
        pytest.param("this-is-not-json", id="envelope-non-object"),
    ])
    def test_bad_schemas_are_rejected(self, facade: BridgeFacade, bad: Any) -> None:
        resp = facade.bridge_send(bad)
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.REQUEST_INVALID
        assert SECRET not in json.dumps(resp)

    @pytest.mark.parametrize("path, roots", [
        pytest.param("/etc/passwd", ["/var/lib"], id="outside-allowed-roots"),
        pytest.param(str(REPO_ROOT / "requirements.txt"), [str(REPO_ROOT / "frontend")],
                     id="sibling-of-roots"),
    ])
    def test_out_of_scope_paths_are_rejected(
        self, facade: BridgeFacade, runtime_root: Path, path: str, roots: list[str]
    ) -> None:
        resp = _send(facade, "dataset.inspect", {"source_path": path, "allowed_roots": roots})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.SOURCE_NOT_ALLOWED

    def test_non_mvp_suffix_is_out_of_scope(self, facade: BridgeFacade, tmp_path) -> None:
        other = tmp_path / "sheet.tsv"
        other.write_text("a\tb\n1\t2\n", encoding="utf-8")
        resp = _send(facade, "dataset.inspect", {"source_path": str(other)})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.SOURCE_NOT_ALLOWED

    def test_unrecorded_artifact_ref_is_out_of_scope(
        self, facade: BridgeFacade, live_run
    ) -> None:
        # Refs resolve ONLY through recorded run_artifacts rows (SECURITY §3).
        # Bind the runtime to the live store (the run IS recorded there) so
        # the escaping ref reaches the ref-lookup, not the run-resolution
        # guard — it must be refused before any file is touched.
        configure_runtime(live_run["root"])
        try:
            resp = _send(facade, "artifact.open",
                         {"run_id": RUN_ID,
                          "ref": f"artifacts/{RUN_ID}/../../{SECRET}.txt"})
        finally:
            configure_runtime(None)
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.SOURCE_NOT_ALLOWED
        assert SECRET not in json.dumps(resp)

    def test_oversized_request_is_rejected(self, facade: BridgeFacade) -> None:
        big = SECRET + "a" * (PAYLOAD_CAP_BYTES + 4096)
        resp = _send(facade, "dataset.inspect", {"source_path": big})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.PAYLOAD_TOO_LARGE
        assert SECRET not in json.dumps(resp)

    def test_oversized_response_never_reaches_the_webview(
        self, facade: BridgeFacade, monkeypatch
    ) -> None:
        def _huge(payload: Dict[str, Any]) -> Dict[str, Any]:
            return {"blob": "x" * (PAYLOAD_CAP_BYTES + 1024), "echo": SECRET}

        monkeypatch.setitem(
            facade._commands,  # noqa: SLF001 — acceptance reaches the resolved table
            "report.get_summary",
            facade_mod.CommandSpec(name="report.get_summary",
                                   payload_model=facade_mod.ReportSummaryPayload,
                                   handler=_huge),
        )
        resp = _send(facade, "report.get_summary", {"run_id": RUN_ID})
        assert resp["status"] == "error"
        assert resp["error"]["code"] == errors.PAYLOAD_TOO_LARGE
        assert SECRET not in json.dumps(resp)


# ---------------------------------------------------------------------------
# §8.3 — "PII-like columns masked in default HTML/CSV export."
# ---------------------------------------------------------------------------

PII_CSV = """\
id,contact_email,support_phone
1,{e1},{p1}
2,{e1},{p2}
3,{e2},{p1}
4,{e3},{p2}
5,{e2},{p1}
6,,{p2}
7,{e3},{p3}
""".format(e1=RAW_EMAIL, e2=RAW_EMAIL_2, e3="budi.santoso@nama.id",
           p1=RAW_PHONE, p2="+62 811-2222-3334", p3="+62 813-9999-0001")
# Discovery's pattern sample draws DISTINCT values (ENGINE §4.1, "≥3 sample
# values to avoid single-value false hits"), so BOTH PII columns need at least
# three distinct shapes before the email/phone refinement can fire — the plain-
# truth baseline a packaged default export is actually built from.

PII_CONFIG_DOC = {
    "config_version": 1,
    "dataset": {"name": "pii_mini"},
    "profiling": {"mode": "standard"},
    "quality_checks": [
        # FAIL path: row 6 has an empty email -> issues.csv carries this rule
        {"id": "contact_not_null", "rule": "not_null", "column": "contact_email",
         "dimension": "completeness", "severity": "high"},
        # ERROR path (FAIL != ERROR): a rule on a column that does not exist
        # compiles to an ERROR entry in run.error_log — which, like every log
        # surface, may carry no raw values (SECURITY §7; checked in §8.4).
        {"id": "ghost_range", "rule": "range", "column": "ghost_column",
         "dimension": "validity", "severity": "warning", "value": [0, 1]},
    ],
}


@pytest.fixture(scope="module")
def live_run(tmp_path_factory):
    """ONE real engine run (SECURITY §4 default paths) persisted into a
    throwaway user-data root: metadata rows + the three canonical artifacts
    written to disk exactly as the bridge worker would write them."""
    root = tmp_path_factory.mktemp("m7t4-live")
    source_dir = root / "source"
    source_dir.mkdir()
    csv_path = source_dir / "pii_mini.csv"
    csv_path.write_text(PII_CSV, encoding="utf-8")

    config = DatasetConfig.model_validate(PII_CONFIG_DOC)
    artifacts = ArtifactStore(data_root=root)
    refs = {kind: artifacts.ref_path(RUN_ID, kind)
            for kind in ("profile_json", "html_report", "issue_csv")}
    bundle = profile_dataset("pii_mini", csv_path, config, RUN_ID,
                             allowed_roots=[source_dir], result_refs=refs)
    artifacts.write_document(RUN_ID, "profile_json", result_document(bundle))
    emit_html_report_artifact(bundle, artifacts)      # mask_pii default — never overridden
    emit_issue_csv_artifact(bundle, artifacts)
    meta = MetadataStore(data_root=root)
    meta.save_run(bundle, config=config, artifact_refs=refs)
    meta.close()
    return {"root": root, "bundle": bundle, "artifacts": artifacts, "refs": refs}


class TestPiiMaskedInDefaultExports:
    def test_html_artifact_on_disk_masks_email_and_phone(self, live_run) -> None:
        html = live_run["artifacts"].resolve(live_run["refs"]["html_report"]).read_text("utf-8")
        for raw in RAW_PII_VALUES:
            assert raw not in html, f"raw PII value {raw!r} reached the default HTML export"
        assert "j***@" in html     # masked shape per SECURITY §4 (j***@…)
        assert "••••" in html      # phone partial-digit mask
        assert "Privacy:" in html  # the report states masking is active

    def test_issue_csv_on_disk_names_columns_never_values(self, live_run) -> None:
        text = live_run["artifacts"].resolve(live_run["refs"]["issue_csv"]).read_text("utf-8")
        for raw in RAW_PII_VALUES:
            assert raw not in text, f"raw PII value {raw!r} reached the default CSV export"
        assert "contact_email" in text     # column NAME is not a value (tracker join)
        assert "contact_not_null" in text
        assert "FAIL" in text              # the planted violation IS reported

    def test_dataset_preview_masks_by_default(self, runtime_root: Path) -> None:
        """Same §4 default on the bridge read path feeding the UI preview."""
        src_dir = runtime_root / "src"
        src_dir.mkdir()
        src = src_dir / "pii_mini.csv"
        src.write_text(PII_CSV, encoding="utf-8")
        facade = BridgeFacade()
        resp = _send(facade, "dataset.preview",
                     {"source_path": str(src), "allowed_roots": [str(src_dir)], "limit": 10})
        assert resp["status"] == "ok", resp
        blob = json.dumps(resp)
        for raw in RAW_PII_VALUES:
            assert raw not in blob, f"raw PII value {raw!r} reached the default preview"
        assert "j***@" in blob


# ---------------------------------------------------------------------------
# §8.4 — "Credentials absent from every log/result sample; stored only in
#         native store (Phase 2)."
# ---------------------------------------------------------------------------

_CREDENTIAL_NAME = re.compile(
    r"passw|secret|token|credential|api[_-]?key|private[_-]?key", re.I
)


def _walk_names(node: Any):
    """Every property/key name in a JSON document (schema or run document)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_names(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_names(value)


class TestCredentialsAbsent:
    def test_mvp_contracts_have_no_credential_surface(self) -> None:
        """MVP file-only sources carry no credentials (SECURITY §5 defines the
        contract for Phase 2 only): the dataset-config schema and pydantic
        model have no field that could hold a secret."""
        doc = json.loads(
            (ENGINE_PKG / "schemas" / "dataset_config.schema.json").read_text("utf-8")
        )
        schema_offenders = [n for n in _walk_names(doc) if _CREDENTIAL_NAME.search(n)]
        assert schema_offenders == [], f"credential-shaped schema field(s): {schema_offenders}"
        model_offenders = [f for f in DatasetConfig.model_fields if _CREDENTIAL_NAME.search(f)]
        assert model_offenders == [], f"credential-shaped model field(s): {model_offenders}"

    def test_no_plaintext_credential_store_in_shipped_code(self) -> None:
        """Phase 2 uses the OS-native store only (SECURITY §5). No MVP module
        may pull a keyring/secret-storage library — the dependency edge itself
        is the guard against an ad-hoc plaintext path appearing first."""
        forbidden = re.compile(
            r"^\s*(?:import|from)\s+(keyring|secretstorage|watchtower|wincred)\b",
            re.MULTILINE | re.IGNORECASE,
        )
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _engine_sources()
            if forbidden.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], f"credential-store dependency in MVP: {offenders}"

    def test_credential_shaped_config_is_rejected_before_dispatch(
        self, runtime_root: Path
    ) -> None:
        with pytest.raises(Exception):
            DatasetConfig.model_validate({**PII_CONFIG_DOC, "credentials": {"password": SECRET}})
        facade = BridgeFacade()
        resp = _send(facade, "profile.start", {
            "dataset_id": "pii_mini",
            "source_path": "/tmp/x.csv",
            "config": {**PII_CONFIG_DOC, "password": SECRET},
            "allowed_roots": ["/tmp"],
        })
        assert resp["status"] == "error"
        assert SECRET not in json.dumps(resp)  # never echoed, never dispatched

    def test_planted_secret_survives_nowhere(self, runtime_root: Path, live_run) -> None:
        """After a battery of rejected/failed bridge calls carrying ``SECRET``,
        neither the running context's tree nor the persisted run (document,
        error_log, every artifact byte, every SQLite byte) contains it —
        logs, results and stores stay credential-free (SECURITY §5/§7)."""
        facade = BridgeFacade()
        _send(facade, "dataset.inspect", {"source_path": SECRET, "allowed_roots": ["/nope"]})
        _send(facade, "profile.get_status", {"job_id": SECRET})
        _send(facade, "findings.get", {"run_id": SECRET})
        _send(facade, "artifact.open", {"run_id": "r", "ref": f"artifacts/r/{SECRET}"})
        _send(facade, "report.get_summary", {"run_id": SECRET})

        doc = json.dumps(result_document(live_run["bundle"]), sort_keys=True)
        assert SECRET not in doc
        # The run's error_log exists (planted ERROR rule) and carries no raw
        # PII value either — SECURITY §7 scrub guarantee, at acceptance level.
        run_json = json.loads(doc)
        error_log = run_json["run"].get("error_log", [])
        assert error_log, "planted ERROR rule produced no error_log entry"
        for entry in error_log:
            for raw in (RAW_EMAIL, RAW_PHONE):
                assert raw not in json.dumps(entry)

        planted = SECRET.encode("utf-8")
        for tree in (runtime_root, live_run["root"]):
            for path in tree.rglob("*"):
                if path.is_file():
                    assert planted not in path.read_bytes(), (
                        f"planted secret persisted into {path.relative_to(tree)}"
                    )


# ---------------------------------------------------------------------------
# §8.5 — "Upgrade preserves user data; uninstall preserves user data unless
#         opted-in."
# ---------------------------------------------------------------------------


class TestUserDataSurvives:
    def test_schema_upgrade_preserves_rows_and_writes_a_backup(self, live_run, tmp_path) -> None:
        """The §6.5 upgrade leg, automated: a *future* forward migration on a
        copy of the real populated store leaves every read-back identical and
        first copies the pre-migration database to the backups dir."""
        root = tmp_path / "upgrade-root"
        shutil.copytree(live_run["root"], root)

        before = MetadataStore(data_root=root)
        run_before = before.load_profiling_run(RUN_ID)
        assert run_before is not None
        checks_before = len(before.load_check_results(RUN_ID))
        findings_before = len(before.load_findings(RUN_ID))
        before.close()

        probe = Migration(3, "accept_probe_v3", "CREATE TABLE dpe_accept_probe (x TEXT);")
        after = MetadataStore(data_root=root, migrations=(*MIGRATIONS, probe))
        try:
            run_after = after.load_profiling_run(RUN_ID)
            assert run_after is not None
            assert run_after.model_dump(mode="json") == run_before.model_dump(mode="json")
            assert len(after.load_check_results(RUN_ID)) == checks_before
            assert len(after.load_findings(RUN_ID)) == findings_before
            assert after.migration_report.backup_path is not None, \
                "pre-migration SQLite backup missing (SECURITY §6.5)"
            assert after.migration_report.backup_path.is_file()
        finally:
            after.close()

    def test_automatic_cleaners_never_touch_referenced_or_exported_files(
        self, live_run, tmp_path
    ) -> None:
        """§2 lifecycle on a copy of the live store: the sweep/clear paths
        remove ONLY crash orphans, staging, and cache — referenced RESULT
        files and user exports survive every automatic cleaner."""
        root = tmp_path / "sweep-root"
        shutil.copytree(live_run["root"], root)
        store = ArtifactStore(
            data_root=root,
            staging_root=tmp_path / "staging",
            cache_root=tmp_path / "cache",
        )
        meta = MetadataStore(data_root=root)

        # an unreferenced file under the run layout = a crashed run's leftover
        orphan_ref = store.write_text(RUN_ID, "debug_scratch", "leftover",
                                      filename="orphan.txt")
        # a user export outside the per-run layout: out of sweep scope by
        # construction (SECURITY §2 "exports removed only by explicit action")
        export_dir = root / "exports"
        export_dir.mkdir()
        export_file = export_dir / "report.html"
        export_file.write_text("<html>user export</html>", encoding="utf-8")

        removed = store.sweep_orphans(meta)
        assert removed == [orphan_ref]
        for kind in ("profile_json", "html_report", "issue_csv"):
            assert store.resolve(live_run["refs"][kind]).is_file()
        assert export_file.is_file()
        assert store.clear_staging() == 0 and store.clear_cache() == 0
        for kind in ("profile_json", "html_report", "issue_csv"):
            assert store.resolve(live_run["refs"][kind]).is_file(), \
                "an automatic cleaner removed a referenced result file"
        # lifecycle policy itself: every MVP run-artifact class is RESULT or
        # EXPORT — survive-upgrade, explicit-delete-only (SECURITY §2 table).
        policies = {kind: retention_for(kind) for kind in
                    ("profile_json", "html_report", "issue_csv",
                     "run_export", "report_export")}
        assert all(p in (RetentionPolicy.RESULT, RetentionPolicy.EXPORT)
                   for p in policies.values()), policies
        meta.close()

    def test_user_data_refused_inside_the_app_bundle(self, tmp_path, monkeypatch) -> None:
        """§2 separation, the automated half of the upgrade guarantee: with a
        simulated frozen bundle, any data/cache root inside it is refused —
        an upgrade (which replaces the bundle) can never destroy user data."""
        bundle = tmp_path / "DataProfilerEngine.app" / "Contents" / "Resources"
        bundle.mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(bundle.resolve()), raising=False)
        with pytest.raises(sp.DirectoryPolicyError):
            sp.resolve_data_root(bundle / "data")
        with pytest.raises(sp.DirectoryPolicyError):
            sp.ensure_outside_app_bundle(bundle / "Cache", label="cache")
        # a normal user-data root still resolves fine
        assert sp.resolve_data_root(tmp_path / "user-data") == (tmp_path / "user-data").resolve()

    def test_windows_uninstall_deletes_no_user_data(self) -> None:
        """The NSIS uninstall section may remove only app files, shortcuts and
        its own registry key — no ``$APPDATA``/``$TEMP`` deletion and no
        ``$LOCALAPPDATA`` path expressed outside ``$INSTDIR`` (SECURITY §6.5;
        removal of results is an explicit opt-in the installer never takes)."""
        text = (PACKAGING / "windows" / "profiler.nsi").read_text(encoding="utf-8")
        match = re.search(r'Section\s+"Uninstall".*?SectionEnd', text, re.S)
        assert match, "uninstall section missing"
        block = match.group(0)
        deleters = [line.strip() for line in block.splitlines()
                    if re.match(r"\s*(Delete|RMDir)\b", line)]
        assert deleters, "uninstall section performs no cleanup at all (stale template?)"
        for line in deleters:
            upper = line.upper()
            assert "$APPDATA" not in upper, line
            assert "$TEMP" not in upper, line
            assert "$LOCALAPPDATA" not in upper or "$INSTDIR" in upper, line

    def test_macos_scripts_never_remove_user_data(self) -> None:
        """The macOS build/notarize pipeline must contain no recursive delete
        aimed at the user-data or cache roots (SECURITY §2/§6.5)."""
        dangerous = re.compile(
            r"rm\s+-[rRf]{1,2}\s+[^\n]*(Application Support|Library/Caches|DPE_DATA_DIR)",
            re.IGNORECASE,
        )
        scripts = sorted((PACKAGING / "macos").glob("*.sh"))
        assert scripts, "macOS packaging scripts missing"
        for script in scripts:
            text = script.read_text(encoding="utf-8")
            assert not dangerous.search(text), f"{script.relative_to(REPO_ROOT)} deletes user data"

    def test_metadata_migrations_are_forward_only_and_backed_up(self, tmp_path) -> None:
        """Upgrade safety rails (§6.5): a versioned db reopens cleanly through
        an extended chain with rows preserved + backup written; an (older)
        truncated chain — the rollback case — is refused rather than silently
        corrupting data."""
        db = tmp_path / "metadata.sqlite3"
        with MetadataStore(db) as store:
            store.register_dataset(dataset_id="ds_keep", name="keep",
                                   connector="csv", source_ref="/a.csv")
        probe = Migration(3, "accept_probe_v3", "CREATE TABLE dpe_v3 (x TEXT);")
        with MetadataStore(db, migrations=(*MIGRATIONS, probe)) as store:
            con = sqlite3.connect(db)
            try:
                row = con.execute(
                    "SELECT name FROM datasets WHERE dataset_id = ?", ("ds_keep",)
                ).fetchone()
            finally:
                con.close()
            assert row is not None and row[0] == "keep"
            assert store.migration_report.backup_path is not None
        # downgrade refusal: reopening a v3 db with the shipped (v<=2) chain
        with pytest.raises(MigrationError):
            MetadataStore(db, migrations=MIGRATIONS)
