# lsp_plan（仅 jdtls-lsp）

```yaml
name: lsp_plan
what: Java 多步骤分析 **只通过 jdtls-lsp CLI**（analyze / callchain-up）；glob/grep 列待办 → run_command 或 dispatch_sub_task+run_command 并行；清单未完成不总结；大 log 只 grep/tail。
when: 全项目/多文件 Java 分析、调用链、并行子任务。
triggers:
  - 调用链
  - dispatch_sub_task
  - jdtls-lsp
  - 并行
```

## 硬性约定

- **所有** Java 语义分析（符号、定义、引用、调用链、入/出站一层等）**必须**用 **`jdtls-lsp`** 子命令完成。
- **禁止**使用 LiteClaw 内置工具 **`lsp_java_analyze`**；本技能与「内置 `src/lsp/skills/lsp_plan` + lsp_java_analyze」是不同路线，不要混用。
- 子任务若需并行，**仅**允许 `dispatch_sub_task` 且 **`type: "run_command"`**，命令体为 **一条可执行的 jdtls-lsp 命令**（或包装它的 shell），不得下发 `lsp_java_analyze`。

## 与内置 `lsp_plan` 的关系

- 仓库 **`src/lsp/skills/lsp_plan`** 为 **lsp_java_analyze** 流程。
- 本文件为 **仅 jdtls-lsp** 变体；置于 **`~/.liteclaw/skills/lsp_plan/`** 时覆盖同名技能（优先级最高）。

## 前置条件

- 已安装 [jdtls-lsp-py](https://github.com/skywalkerw/jdtls-lsp-py)（`pip install -e .` 或 `PYTHONPATH=src`），本机可执行 `jdtls-lsp` 或 `python3 -m jdtls_lsp.cli`。
- 目标为 **Maven/Gradle** 工程根；首次索引较慢。
- 不确定参数时：**先执行** `jdtls-lsp --help`、`jdtls-lsp analyze --help`、`jdtls-lsp callchain-up --help`。

## 核心原则

- **先计划再执行**：先形成**完整待办列表**，再逐项用 jdtls-lsp 执行；未完成列表前不得总结。
- **穷举**：用户要求「所有」「完整」「调用链」时，穷举纳入列表。
- **并行**：待分析项多时用 `dispatch_sub_task` + **`run_command`**（内嵌 jdtls-lsp）；同轮可多条。
- **路径**：`PROJECT` 为项目根；`--file` 用相对项目根的 `.java` 路径。

## 命令速查（jdtls-lsp）

| 目的 | 示例 |
|------|------|
| 工作区符号 | `jdtls-lsp analyze "$PROJECT" workspaceSymbol --query 'com.example.Foo'` |
| 单文件符号树 | `jdtls-lsp analyze "$PROJECT" documentSymbol --file src/.../Foo.java` |
| 引用 / 定义 / hover / 实现 / 一层入出站 | `jdtls-lsp analyze "$PROJECT" <operation> --file ... --line L --char C` |
| 向上调用链 | `jdtls-lsp callchain-up "$PROJECT" --query <kw> --format markdown --max-depth N` |
| 多起点去重 | `callchain-up` 加 `--grep-skip-interface --grep-skip-rest-entry --grep-max-entry-points 1` 等（见 `--help`） |

计划阶段可配合 **`glob`**、**`grep`** 缩小范围；需要 LSP 时 **只走上述命令**，不要用其它 LSP 工具。

## 执行流程

### 1. 计划：待办列表

- 用 glob/grep 扫范围；需要符号/结构时用 **`run_command`** 执行 `jdtls-lsp analyze ...`（如 `workspaceSymbol`、`documentSymbol`）。
- 列出待分析项（类、方法、关键字、文件路径+行号等）。

### 2. 主 Agent 直接执行

- 单点分析：一条 `jdtls-lsp analyze "$PROJECT" references|definition|...`。
- 调用链：一条 `jdtls-lsp callchain-up "$PROJECT" ...`。

### 3. 并行分派（可选）

- `dispatch_sub_task`，**`type` 必须为 `"run_command"`**；`contextSummary` 中写清**完整** `jdtls-lsp ...` 命令及期望输出格式（表格/摘要等）。
- **不要** 分派 `lsp_java_analyze` 类子任务。

### 4. 汇总

- 合并各命令输出，按用户要求呈现；禁止空泛「完成」。

## 其它约定

- **contextSummary**：须能唯一还原 jdtls-lsp 命令与输出要求。
- **日志/大文件**：`grep` 或 `run_command`（`tail`/`head`），勿 `read_file` 整日志。
- **任务数**：避免过细爆炸；优先分批与汇总。
