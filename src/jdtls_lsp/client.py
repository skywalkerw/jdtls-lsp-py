"""LSP client: initialize, didOpen, LSP requests (aligned with LiteClaw client.ts + index.ts)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from jdtls_lsp.jdtls import find_project_root, spawn_jdtls
from jdtls_lsp.jrpc import JsonRpcConnection
from jdtls_lsp.logutil import format_payload

_log = logging.getLogger("jdtls_lsp.client")

INIT_TIMEOUT_S = 120.0


def _path_to_uri(path: str | Path) -> str:
    return Path(path).resolve().as_uri()


class LSPClient:
    def __init__(
        self,
        project_root: str,
        proc: subprocess.Popen[Any],
        conn: JsonRpcConnection,
        data_dir: Path,
    ) -> None:
        self.root = str(Path(project_root).resolve())
        self._proc = proc
        self._conn = conn
        self._data_dir = data_dir
        self._files: dict[str, int] = {}
        self._lock = threading.Lock()

    def _on_server_request(self, msg: dict[str, Any]) -> Any:
        method = msg.get("method")
        if method == "window/workDoneProgress/create":
            return None
        if method == "workspace/configuration":
            return [{}]
        if method in ("client/registerCapability", "client/unregisterCapability"):
            return {}
        if method == "workspace/workspaceFolders":
            return [{"name": "workspace", "uri": _path_to_uri(self.root)}]
        return None

    def open_file(self, path: str) -> None:
        """textDocument/didOpen or didChange (aligned with LiteClaw)."""
        file_path = path
        if not Path(file_path).is_absolute():
            file_path = str(Path(self.root) / path)
        p = Path(file_path).resolve()
        ext = p.suffix.lower()
        language_id = "java" if ext == ".java" else "plaintext"
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return
        uri = p.as_uri()
        with self._lock:
            version = self._files.get(str(p))
            if version is not None:
                nxt = version + 1
                self._files[str(p)] = nxt
                self._conn.send_notification(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": uri, "version": nxt},
                        "contentChanges": [{"text": text}],
                    },
                )
                return
            self._conn.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": 0,
                        "text": text,
                    }
                },
            )
            self._files[str(p)] = 0

    def request(self, method: str, params: dict[str, Any], *, timeout: float | None = None) -> Any:
        return self._conn.send_request(method, params, timeout=120.0 if timeout is None else timeout)

    def shutdown(self) -> None:
        try:
            self._conn.send_request("shutdown", None, timeout=30.0)
        except KeyboardInterrupt:
            _log.warning("LSP shutdown: KeyboardInterrupt during shutdown request; terminating JVM")
        except Exception:
            pass
        try:
            self._conn.send_notification("exit", {})
        except Exception:
            pass
        self._conn.close()
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        try:
            shutil.rmtree(self._data_dir, ignore_errors=True)
        except Exception:
            pass


def create_client(project_path: str, jdtls_path: Path | None = None) -> LSPClient:
    root = str(find_project_root(project_path))
    _log.info("LSP create_client project_path=%s resolved_root=%s jdtls_path=%s", project_path, root, jdtls_path)
    proc, data_dir, _ = spawn_jdtls(root, jdtls_path=jdtls_path)
    assert proc.stdout and proc.stdin

    holder: list[LSPClient | None] = [None]

    def on_server_request(msg: dict[str, Any]) -> Any:
        c = holder[0]
        if c is not None:
            return c._on_server_request(msg)
        method = msg.get("method")
        if method == "window/workDoneProgress/create":
            return None
        if method == "workspace/configuration":
            return [{}]
        if method in ("client/registerCapability", "client/unregisterCapability"):
            return {}
        if method == "workspace/workspaceFolders":
            return [{"name": "workspace", "uri": _path_to_uri(root)}]
        return None

    conn = JsonRpcConnection(proc.stdout, proc.stdin, on_server_request)
    client = LSPClient(root, proc, conn, data_dir)
    holder[0] = client

    pid = proc.pid
    init_params = {
        "rootUri": _path_to_uri(root),
        "processId": pid,
        "workspaceFolders": [{"name": "workspace", "uri": _path_to_uri(root)}],
        "capabilities": {
            "window": {"workDoneProgress": True},
            "workspace": {"configuration": True, "didChangeWatchedFiles": {"dynamicRegistration": True}},
            "textDocument": {
                "synchronization": {"didOpen": True, "didChange": True},
                "publishDiagnostics": {"versionSupport": True},
            },
        },
    }
    _log.debug("LSP initialize params=%s", format_payload(init_params))
    conn.send_request("initialize", init_params, timeout=INIT_TIMEOUT_S)
    conn.send_notification("initialized", {})
    _log.info("LSP initialized, client root=%s", root)
    return client
