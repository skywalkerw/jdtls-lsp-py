"""Standalone JDTLS LSP client (aligned with LiteClaw lsp module)."""

from jdtls_lsp.callchain import extract_top_entry_info, format_callchain_markdown
from jdtls_lsp.java_grep import java_grep_report
from jdtls_lsp.logutil import setup_logging

__version__ = "0.1.1"

__all__ = [
    "__version__",
    "setup_logging",
    "format_callchain_markdown",
    "extract_top_entry_info",
    "java_grep_report",
]
