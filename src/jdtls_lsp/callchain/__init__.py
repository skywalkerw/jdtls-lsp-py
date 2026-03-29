"""调用链包装层：在 ``analyze``（LSP）与 ``java_grep`` 等原子能力之上做向上/向下追踪与报告格式化。

子模块：
- ``trace``：``trace_call_chain_sync`` / ``trace_outgoing_subgraph_sync`` 等
- ``format``：Markdown/JSON 摘要、``extract_trace_payload_dict`` 等
"""

from __future__ import annotations

from .format import (
    extract_trace_payload_dict,
    format_callchain_markdown,
    format_downchain_markdown,
    summarize_trace_down_json,
    summarize_trace_up_json,
)
from .trace import (
    extract_top_entry_info,
    trace_call_chain_sync,
    trace_outgoing_subgraph_sync,
)

__all__ = [
    "extract_top_entry_info",
    "extract_trace_payload_dict",
    "format_callchain_markdown",
    "format_downchain_markdown",
    "summarize_trace_down_json",
    "summarize_trace_up_json",
    "trace_call_chain_sync",
    "trace_outgoing_subgraph_sync",
]
