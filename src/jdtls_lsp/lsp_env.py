"""Shared LSP-related defaults from environment (timeouts, etc.)."""

from __future__ import annotations

import os


def document_symbol_timeout_s() -> float:
    """Seconds to wait for ``textDocument/documentSymbol`` (``analyze`` 子命令)."""
    raw = (os.environ.get("JDTLS_LSP_DOCUMENT_SYMBOL_TIMEOUT") or "").strip()
    if not raw:
        return 600.0
    try:
        return max(30.0, float(raw))
    except ValueError:
        return 600.0
