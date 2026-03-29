---
name: java-callchain-analysis
description: >-
  Analyzes Java call chains upward from a method using jdtls-lsp (JDTLS/LSP
  callHierarchy/incomingCalls). Instructs agents to run jdtls-lsp callchain-up
  --help when unsure of CLI flags. Use when the user asks for 调用链, call chain,
  incoming calls, who calls X, API entry for a method, Spring REST trace from
  service to controller, or keyword-based Java call tracing with jdtls-lsp-py.
---

# Java 调用链分析（jdtls-lsp）

## 智能体指引

- **不要凭记忆编造 CLI 参数或默认值**。需要确认选项、入口互斥或 grep 过滤开关时，**先在本机执行 `--help`**，再组命令或回答用户。
- 推荐命令（任选其一；未全局安装时与 **README** 一致：`PYTHONPATH=src python3 -m jdtls_lsp`；`python3 -m jdtls_lsp.cli` 等价）：

```bash
jdtls-lsp --help
jdtls-lsp callchain-up --help
jdtls-lsp callchain-down --help
# 或
PYTHONPATH=src python3 -m jdtls_lsp --help
PYTHONPATH=src python3 -m jdtls_lsp callchain-up --help
```

## 何时使用

- 从**方法/关键字**出发，**向上**追到 Controller、REST、无上游或环。
- 需要比纯文本 grep **更语义化**的调用关系（LSP `callHierarchy/incomingCalls`）。
- 从某方法出发 **向下** 展开多层被调子图（`outgoingCalls` BFS、边表）请用 **`callchain-down`**（入口与 `callchain-up` 相同；**不支持**多文件 grep 多起点）。

## 前置条件

- 工程为 **Maven/Gradle** Java 项目（CLI 会向上解析项目根）。
- 已安装 **jdtls-lsp-py**（`pip install -e /path/to/jdtls-lsp-py` 或 `PYTHONPATH=src`）。
- **JDTLS** 与 **JDK** 按该项目的 README/`setup.sh` 配置；首次运行会较慢（索引）。

## 推荐流程

1. **确定项目根目录** `PROJECT`（含 `pom.xml` 或 Gradle 根的目录）。
2. **选一种入口**（互斥）：
   - **类 + 方法**：`--class Foo --method bar`（方法名不含参数列表）。
   - **文件 + 行**：`--file path/to/Foo.java --line N`（行号 1-based）。
   - **关键字**：`--query needle`（先 workspace/symbol，再回退到 `*.java` 全文 grep → `java_text_grep`）。
3. **运行**（示例）：

```bash
cd /path/to/jdtls-lsp-py
PYTHONPATH=src python3 -m jdtls_lsp callchain-up "$PROJECT" \
  --query saveMonitorData --format markdown --max-depth 100
```

4. **解读输出**：
   - **`chains[].chain`**：自下而上，起点 → 直接调用方 → …
   - **`stopReason`**：`rest_endpoint`（追到 REST）、`message_listener`（Kafka/Rabbit 等消费者）、`scheduled_task`（`@Scheduled` / Quartz / XXL-JOB 等）、`async_method`（Spring **`@Async`**）、`no_incoming`、`cycle`、`max_depth`、`abstract_class`、`jdtls_error` 等。
   - **`topEntry`**：链顶 Spring 映射时含 `httpMethod`、`restPath`（如 `POST /api/...`）。
5. **多 grep 起点去重**（同一方法命中 Controller / `*Impl` / 接口等多条链时）：加过滤只保留实现类一条，例如：

```bash
PYTHONPATH=src python3 -m jdtls_lsp callchain-up "$PROJECT" \
  --query saveMonitorData \
  --grep-skip-interface --grep-skip-rest-entry --grep-max-entry-points 1 \
  --format markdown
```

## 参数速查

| 场景 | 参数 |
|------|------|
| 仅 JSON | `--format json` |
| 追踪深度 | `--max-depth N`（默认 20） |
| 跳过接口文件起点 | `--grep-skip-interface`（仅 `java_text_grep`） |
| 跳过已是 REST 的起点 | `--grep-skip-rest-entry` |
| 最多 N 个 grep 起点 | `--grep-max-entry-points N`（实现类优先排序后截断） |
| 指定 JDTLS 根 | `--jdtls /path/to/jdtls` |

多关键字全文搜索（不走 workspace 捷径）：查询串用 `|` 或 `｜` 连接，如 `--query 'foo|bar'`。

## 作为库

与 **README「作为库调用」** 一致：实现在 **`jdtls_lsp.callchain`**（`trace_call_chain_sync` / `trace_outgoing_subgraph_sync` 与 `callchain.format` 的 Markdown/摘要）。

```python
from jdtls_lsp.callchain import trace_call_chain_sync

text = trace_call_chain_sync(
    "/path/to/project",
    symbol_query="methodName",
    output_format="json",  # 或 "markdown"；bundle step4/5 默认 markdown
    grep_skip_interface=True,
    grep_skip_rest=True,
    grep_max_entry_points=1,
)
```

## 向用户交付时的结构建议

- **查询条件**：项目根、入口方式、关键字/类方法/文件行。
- **结论**：每条链一句话（从谁 → 到 REST 或终止原因）。
- **原始依据**：附 `--format markdown` 中的图示或嵌入 JSON 片段，便于复核。

## 限制说明

- **并发**：多 grep 起点在同一 JDTLS 连接上**串行**追踪；不要用多进程同时对同一工程开多个 `incomingCalls` 压同一 JDTLS。
- **过滤后无起点**：会报错；放宽 `--grep-*` 或去掉 `--grep-max-entry-points`。
- **非 Java** 或无法解析的锚点：需换入口（`--file`/`--line` 或更具体的关键字）。

详细行为与字段说明以 **jdtls-lsp-py 仓库内的 README** 为准。
