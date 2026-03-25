# lsp_plan

```yaml
name: lsp_plan
description: >
  多 Agent 下 Java 项目分析流程：先形成完整待办列表，再并行分析并汇总。
  本版本面向 jdtls-lsp（Python）：优先使用 jdtls-lsp analyze / jdtls_lsp.analyze_sync。
```

## 核心原则

- **先计划再分派**：先形成完整待办列表，再执行子任务或并行分析。
- **穷举用户要求**：用户要求“所有”“完整”“调用链”时，必须覆盖所有符合条件项。
- **并行优先**：待分析项较多（>=3）时并行执行，减少整体耗时。
- **路径规范**：`filePath` 使用相对于 `projectPath` 的路径，如 `src/main/java/com/xxx/File.java`。

## 执行流程

### 1) 计划阶段：生成完整待办

- 用 `glob`、`rg`（或等效搜索）梳理项目结构。
- 用 `jdtls-lsp analyze <project> workspaceSymbol --query ...` 快速定位包/类。
- 必要时补充代码文本检索（TODO/FIXME、关键注解、接口实现点）。

### 2) 分析阶段：按项执行

- 每个待办项调用对应 operation（见 `lsp_java_doc`）。
- 推荐从 `documentSymbol` 起步，再进入 `definition/references/incomingCalls/outgoingCalls`。
- 若支持多任务工具，按文件/符号粒度并行下发。

### 3) 深入分析（按需）

- 调用链分析优先组合：
  - `incomingCalls`（谁调我）
  - `outgoingCalls`（我调谁）
  - `implementation`（接口到实现）
  - `definition`（跳转定义补链）

### 4) 汇总输出

- 等全部任务完成后统一汇总。
- 按用户要求输出表格/清单/调用链，避免仅给“完成”。

## 重要约定

- 默认排除测试类（`*Test.java`），除非用户明确要求包含。
- 大日志/大文件优先检索（`rg`/命令行过滤），避免整文件通读。
- 优先使用项目内环境：
  - Java：`./openjdk/bin/java`（若存在）
  - JDTLS：`./jdtls`（若存在）
- 常用执行方式：
  - `jdtls-lsp analyze ...`（全局安装成功时）
  - `./.venv/bin/jdtls-lsp analyze ...`（Unix 上 setup 回退 venv 时）
  - `.venv\Scripts\jdtls-lsp.exe analyze ...`（Windows 上 venv 回退时）
  - `PYTHONPATH=src python3 -m jdtls_lsp analyze ...`（不安装时；Windows 可用 `py -3 -m jdtls_lsp ...`）
