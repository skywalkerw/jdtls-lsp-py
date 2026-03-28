---
name: java-analysis
description: >-
  Documents jdtls-lsp analyze subcommand and analyze_sync API: documentSymbol,
  workspaceSymbol, definition, references, hover, implementation, incomingCalls,
  outgoingCalls. Instructs agents to run jdtls-lsp analyze --help when unsure
  of CLI flags. Use when the user asks for LSP 分析, jdtls-lsp analyze, Java
  symbols, go-to-definition, find references, call hierarchy one-hop, or
  LiteClaw lsp_java_analyze equivalents without full callchain tracing.
---

# Java LSP 单步分析（jdtls-lsp `analyze`）

## 智能体指引

- **不要凭记忆编造 CLI 参数或默认值**。需要确认选项、子命令或当前版本行为时，**先在本机执行 `--help`**，再组命令或回答用户。
- 推荐命令（任选其一；未全局安装时用 `PYTHONPATH=src python3 -m jdtls_lsp.cli`）：

```bash
jdtls-lsp --help
jdtls-lsp analyze --help
# 或
PYTHONPATH=src python3 -m jdtls_lsp.cli --help
PYTHONPATH=src python3 -m jdtls_lsp.cli analyze --help
```

## 何时使用

- 需要 **单次 LSP 请求**：文件符号树、工作区符号搜索、定义/引用、悬停、实现、**一层**入站/出站调用。
- **完整向上调用链**（追到 REST、多起点合并等）请用 **`callchain-up`** 子命令或 skill `java-callchain-analysis`，勿与本文混用。

## 前置条件

- **Maven/Gradle** Java 项目根（CLI 会向上解析）。
- **jdtls-lsp-py** 已安装（`pip install -e .` 或 `PYTHONPATH=src`）。
- **JDTLS + JDK**：见仓库 `README.md` / `setup.sh`；首次索引较慢。

## CLI 形式

```bash
jdtls-lsp analyze <project> <operation> [选项]
```

| 选项 | 含义 |
|------|------|
| `--file` / `-f` | 相对项目根的 `.java` 路径（除 `workspaceSymbol` 外多数操作必填） |
| `--line` / `-l` | 行号 **1-based**（需 `--file`） |
| `--char` / `-c` | 列号 **1-based**，默认 `1`（需 `--file`） |
| `--query` / `-q` | 仅 **`workspaceSymbol`**：搜索串；可用 `\|` 或 `｜` 拼多段，结果合并去重（每操作最多约 20 条） |
| `--jdtls` | JDTLS 安装目录（默认 `./jdtls`、`LITECLAW_JDTLS_PATH`、`~/jdtls`） |

## 操作一览（`operation`）

| operation | 必填参数 | 说明 |
|-----------|----------|------|
| **documentSymbol** | `--file` | 单文件符号树（类/方法/字段等） |
| **workspaceSymbol** | `--query` | 工作区符号搜索（多段 query 见上） |
| **definition** | `--file --line`（`--char` 可选） | 跳转到定义 |
| **references** | `--file --line` | 引用列表（含声明） |
| **hover** | `--file --line` | 悬停信息（类型、Javadoc 等） |
| **implementation** | `--file --line` | 接口/抽象方法的实现 |
| **incomingCalls** | `--file --line` | **直接**调用方（一层，基于 call hierarchy） |
| **outgoingCalls** | `--file --line` | **直接**被调方（一层） |
| **typeHierarchy** | `--file --line` | 光标在**类型名**上：`subtypes` / `supertypes`（依赖 JDTLS 能力） |

`line` / `character` 在命令行层为 **1-based**；内部转为 LSP 0-based，无需手算。

**多跳向下调用子图**（BFS、`outgoingCalls`）请用子命令 **`callchain-down`**，勿与单层 `outgoingCalls` 混淆。

## 命令示例

```bash
cd /path/to/jdtls-lsp-py
PYTHONPATH=src python3 -m jdtls_lsp.cli analyze "$PROJECT" documentSymbol \
  --file src/main/java/com/example/App.java

PYTHONPATH=src python3 -m jdtls_lsp.cli analyze "$PROJECT" references \
  --file src/main/java/com/example/Foo.java --line 42 --char 1

PYTHONPATH=src python3 -m jdtls_lsp.cli analyze "$PROJECT" workspaceSymbol \
  --query 'com.example.UserService|UserService'
```

## Python API

```python
from jdtls_lsp.analyze import analyze_sync, OPERATIONS

text = analyze_sync(
    "/path/to/project",
    "references",
    file_path="src/main/java/com/example/Foo.java",
    line=10,
    character=1,
    jdtls_path=None,
)
print(text)  # JSON 字符串；无结果时返回以「无结果:」开头的提示
```

## 输出与行为说明

- 返回 **完整 JSON 字符串**（不截断）；`documentSymbol` 前会带一行 `[file: ...]` 标注。
- 无符号/无结果时返回 **`无结果: <operation>`** 等提示，而非空 JSON。
- **`incomingCalls` / `outgoingCalls`**：仅取 `prepareCallHierarchy` 的**第一个**锚点的一层调用；多候选时需移动光标或换用 `callchain-up`。

详细字段与故障排查见 **jdtls-lsp-py 仓库 `README.md`**。
