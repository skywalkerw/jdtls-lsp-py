"""逆向设计导出（`需求.md` step1–step8）。

step1：模块扫描 + 按包符号；step2：REST 映射（``entry_scan.scan_rest_map``，静态入口 HTTP 分支）；step3：表清单；step4/5：向下/向上调用链；
step6：业务摘要（``jdtls_lsp.business_summary``，与 reverse_design 平级；CLI ``--business-summary``）；step7：单点深挖由 analyze/callchain 与 IDE 完成；
step8：bundle 写 ``index.md`` 与摘要。
"""

from jdtls_lsp.business_summary import (
    annotate_downchain_business,
    format_business_md,
    merge_key_methods_from_downchain_files,
)
from jdtls_lsp.reverse_design.bundle import run_design_bundle
from jdtls_lsp.reverse_design.rest_callchains_down import infer_service_impl_fqcn, run_rest_callchains_down
from jdtls_lsp.entry_scan import scan_rest_map
from jdtls_lsp.reverse_design.batch_symbols_by_package import batch_symbols_by_package
from jdtls_lsp.reverse_design.scan_modules import scan_modules
from jdtls_lsp.reverse_design.table_callchains_up import (
    resolve_service_anchor_for_table,
    run_table_callchains_up,
)
from jdtls_lsp.reverse_design.table_manifest import build_table_manifest

__all__ = [
    "annotate_downchain_business",
    "batch_symbols_by_package",
    "build_table_manifest",
    "format_business_md",
    "infer_service_impl_fqcn",
    "merge_key_methods_from_downchain_files",
    "resolve_service_anchor_for_table",
    "run_design_bundle",
    "run_rest_callchains_down",
    "run_table_callchains_up",
    "scan_modules",
    "scan_rest_map",
]
