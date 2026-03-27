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
        h = logging.StreamHandler(stream or sys.stderr)
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
