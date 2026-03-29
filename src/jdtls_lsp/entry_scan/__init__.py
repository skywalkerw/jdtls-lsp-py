"""静态入口扫描（无 JDTLS）：非 HTTP 行级模式 + HTTP（Spring MVC）映射。

- ``java_entry_patterns``：与 ``callchain-up`` 共用的链顶/入口正则。
- ``java_entrypoints`` / ``line_patterns.scan_java_entrypoints``：工程内入口行扫描。
- ``scan_rest_map``：REST 端点表（``rest-map.json`` 形态）。
"""

from __future__ import annotations

from jdtls_lsp.entry_scan.line_patterns import scan_java_entrypoints
from jdtls_lsp.entry_scan.rest_http import scan_rest_map

__all__ = ["scan_java_entrypoints", "scan_rest_map"]
