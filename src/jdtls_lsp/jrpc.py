"""LSP JSON-RPC over stdio (Content-Length framing)."""

from __future__ import annotations

import json
import logging
import queue
import threading
from typing import Any, Callable

from jdtls_lsp.logutil import format_payload, redact_lsp_params

_log = logging.getLogger("jdtls_lsp.jrpc")


def _write_message(stream: Any, obj: dict[str, Any]) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stream.write(header + body)
    stream.flush()


class JsonRpcConnection:
    """Minimal bidirectional JSON-RPC for LSP (client side)."""

    def __init__(
        self,
        reader,
        writer,
        on_request: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._on_request = on_request
        self._next_id = 1
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._closed = False
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        try:
            while not self._closed:
                headers: dict[str, str] = {}
                while True:
                    line = self._reader.readline()
                    if line == b"":
                        return
                    line_str = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line_str == "":
                        break
                    if ":" in line_str:
                        k, v = line_str.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                body = self._reader.read(length)
                if len(body) < length:
                    return
                try:
                    msg = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self._dispatch_incoming(msg)
        except Exception:
            pass

    def _dispatch_incoming(self, msg: dict[str, Any]) -> None:
        if "id" in msg and "method" in msg:
            m = str(msg.get("method", ""))
            p = msg.get("params")
            _log.debug("LSP <- server request %s params=%s", m, format_payload(p))
            try:
                result = self._on_request(msg)
            except Exception:
                result = None
            resp: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"]}
            if result is not None:
                resp["result"] = result
            else:
                resp["result"] = None
            with self._lock:
                if not self._closed:
                    _write_message(self._writer, resp)
            return
        if "id" in msg and ("result" in msg or "error" in msg):
            qid = msg["id"]
            if isinstance(qid, int) and qid in self._pending:
                self._pending[qid].put(msg)
            return

    def send_request(self, method: str, params: dict[str, Any] | list[Any] | None = None, timeout: float = 120.0) -> Any:
        safe = redact_lsp_params(method, params) if isinstance(params, dict) else params
        _log.debug("LSP -> request %s params=%s", method, format_payload(safe))
        with self._lock:
            if self._closed:
                raise RuntimeError("connection closed")
            req_id = self._next_id
            self._next_id += 1
            q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[req_id] = q
            payload: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
            if params is not None:
                payload["params"] = params
            _write_message(self._writer, payload)
        try:
            resp = q.get(timeout=timeout)
        finally:
            self._pending.pop(req_id, None)
        if "error" in resp:
            err = resp["error"]
            _log.warning("LSP request failed %s: %s", method, format_payload(err))
            raise RuntimeError(str(err))
        result = resp.get("result")
        _log.debug("LSP -> %s result=%s", method, format_payload(result))
        return result

    def send_notification(self, method: str, params: dict[str, Any] | list[Any] | None = None) -> None:
        safe = redact_lsp_params(method, params) if isinstance(params, dict) else params
        _log.debug("LSP -> notification %s params=%s", method, format_payload(safe))
        with self._lock:
            if self._closed:
                return
            payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            _write_message(self._writer, payload)

    def close(self) -> None:
        with self._lock:
            self._closed = True
