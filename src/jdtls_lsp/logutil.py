"""Logging helpers for jdtls_lsp: levels, payload formatting, sensitive truncation."""

from __future__ import annotations

import json
import logging
import os
import sys
from copy import deepcopy
from typing import Any

_LOG = logging.getLogger("jdtls_lsp")


def get_logger(name: str | None = None) -> logging.Logger:
    """Return package logger or child e.g. jdtls_lsp.jrpc."""
    if name:
        return logging.getLogger(f"jdtls_lsp.{name}")
    return _LOG


def max_payload_chars() -> int:
    try:
        return max(400, int(os.environ.get("JDTLS_LSP_LOG_MAX_PAYLOAD", "12000")))
    except ValueError:
        return 12000


def format_payload(obj: Any, max_chars: int | None = None) -> str:
    """JSON-serialize for logs; truncate long strings."""
    mc = max_chars if max_chars is not None else max_payload_chars()
    try:
        if isinstance(obj, str):
            s = obj
        else:
            s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = repr(obj)
    if len(s) <= mc:
        return s
    return s[: mc - 3] + "..."


def format_lsp_response(method: str, result: Any) -> str:
    """
    Format LSP method results for DEBUG logs without ``json.dumps`` of multi‑MB payloads.

    Full ``documentSymbol`` / large ``workspace/symbol`` lists would otherwise allocate
    huge strings and block stderr (pipe backpressure), making the CLI look hung under ``-vv``.
    """
    if result is None:
        return "null"
    if method == "textDocument/documentSymbol":
        lst = result if isinstance(result, list) else ([result] if isinstance(result, dict) else [])
        if not lst:
            return format_payload(result)
        n = len(lst)
        names: list[str] = []
        for x in lst[:16]:
            if isinstance(x, dict):
                names.append(str(x.get("name", "?")))
            else:
                names.append("?")
        more = f" …(+{n - len(names)} more)" if n > len(names) else ""
        return f"({n} symbols; names: {names}{more})"
    if method == "initialize" and isinstance(result, dict):
        caps = result.get("capabilities")
        if isinstance(caps, dict):
            ks = sorted(caps.keys())
            head = ks[:24]
            extra = f" …(+{len(ks) - len(head)} keys)" if len(ks) > len(head) else ""
            return f"{{capabilities: {len(ks)} keys [{', '.join(head)}{extra}]}}"
        return format_payload(result, max_chars=4000)
    if method == "workspace/symbol" and isinstance(result, list) and len(result) > 12:
        snips: list[str] = []
        for x in result[:12]:
            if isinstance(x, dict):
                snips.append(str(x.get("name", "?")))
            else:
                snips.append("?")
        return f"({len(result)} symbols; sample: {snips} …)"
    return format_payload(result)


def _redact_text_document(obj: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(obj)
    td = out.get("textDocument")
    if isinstance(td, dict) and "text" in td:
        text = td.get("text", "")
        if isinstance(text, str) and len(text) > 0:
            td = dict(td)
            td["text"] = f"<omitted {len(text)} chars>"
            out["textDocument"] = td
    return out


def redact_lsp_params(method: str, params: Any) -> Any:
    """Shorten huge document bodies in params for logging."""
    if params is None:
        return None
    if method == "textDocument/didOpen" and isinstance(params, dict):
        return _redact_text_document(params)
    if method == "textDocument/didChange" and isinstance(params, dict):
        out = deepcopy(params)
        changes = out.get("contentChanges")
        if isinstance(changes, list):
            out["contentChanges"] = [
                {"text": f"<omitted {len(c.get('text', ''))} chars>"}
                if isinstance(c, dict) and isinstance(c.get("text"), str) and len(c.get("text", "")) > 200
                else c
                for c in changes
            ]
        return out
    return params


def parse_log_level(name: str | None) -> int:
    if not name or not str(name).strip():
        return logging.WARNING
    n = str(name).strip().upper()
    return getattr(logging, n, logging.WARNING)


def setup_logging(
    level: int | str | None = None,
    *,
    stream: Any = None,
) -> None:
    """
    Configure the jdtls_lsp logger (stderr, single handler).
    If level is None, use env JDTLS_LSP_LOG: debug|info|warning|error (default warning).
    """
    if level is None:
        raw = (os.environ.get("JDTLS_LSP_LOG") or "").strip().lower()
        level = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(raw, logging.WARNING)
    elif isinstance(level, str):
        level = parse_log_level(level)

    log = logging.getLogger("jdtls_lsp")
    log.setLevel(level)
    log.propagate = False
    if not log.handlers:

        class _FlushStreamHandler(logging.StreamHandler):
            def emit(self, record: logging.LogRecord) -> None:
                super().emit(record)
                self.flush()

        h = _FlushStreamHandler(stream or sys.stderr)
        h.setLevel(level)
        h.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        log.addHandler(h)
    else:
        for h in log.handlers:
            h.setLevel(level)
