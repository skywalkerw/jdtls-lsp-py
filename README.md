# jdtls-lsp（Python）

`jdtls-lsp` 是一个独立可运行的 Python 包，用于启动 **JDTLS**（Eclipse JDT Language Server），通过 **LSP（stdio JSON-RPC）** 做 Java 源码分析；语义与 LiteClaw 的 `lsp_java_analyze` 对齐。

---

## 依赖

| 依赖 | 说明 |
|------|------|
| Python | **3.10+** |
| Java | **21+**；优先使用项目内 `jdtls-lsp-py/openjdk/bin/java`，其次 `./openjdk/bin/java`，否则 `PATH` / `JAVA_HOME` |
| JDTLS | 目录内需含 `config_mac` / `config_linux` / `config_win`（与系统对应）及 `plugins/org.eclipse.equinox.launcher_*.jar` |

**JDTLS 查找顺序**：`LITECLAW_JDTLS_PATH` → 包内 `jdtls-lsp-py/jdtls` → 当前目录 `./jdtls` → `~/jdtls`。

---

## 安装与运行

### 推荐：`setup.sh`（含离线 OpenJDK / JDTLS 包）

```bash
cd jdtls-lsp-py
./setup.sh
jdtls-lsp --help
# 若仅用虚拟环境：
./.venv/bin/jdtls-lsp --help
```

### 手动安装

```bash
cd jdtls-lsp-py
pip install -e .
```

### 不安装直接跑（开发）

```bash
cd jdtls-lsp-py
PYTHONPATH=src python3 -m jdtls_lsp --help
```

### Windows

- 在项目目录执行 `setup.bat`（逻辑与 `setup.sh` 一致）。
- 离线 OpenJDK 包名需含 `windows` 或 `win`。
- 虚拟环境入口：`.venv\Scripts\jdtls-lsp.exe`。
- 便携包：`export.bat`（与 `export.sh` 相同规则）。

---

## 命令总览

```text
jdtls-lsp [-v | -vv] [--log-level LEVEL] <子命令> ...
```

| 全局参数 | 说明 |
|----------|------|
| `-v` | 日志 **INFO** |
| `-vv` | 日志 **DEBUG**（含 LSP 请求/响应摘要，大 payload 会截断） |
| `--log-level` | 直接指定 `DEBUG` / `INFO` / `WARNING` / `ERROR`（覆盖 `-v`） |

**日志**默认打到 **stderr**；**分析结果**（JSON / Markdown）在 **stdout**。保存日志示例：

```bash
jdtls-lsp -v callchain /path/to/project --query foo --format json 2> jdtls-lsp.log
```

**环境变量（日志）**

| 变量 | 说明 |
|------|------|
| `JDTLS_LSP_LOG` | `debug` / `info` / `warning` / `error`（未传 `-v` 时生效） |
| `JDTLS_LSP_LOG_MAX_PAYLOAD` | 单条日志里 JSON 序列化最大字符数（默认约 12000） |

**子命令**

| 子命令 | 作用 |
|--------|------|
| `analyze` | 单次 LSP 操作（符号、定义、引用、call hierarchy 等） |
| `callchain` | 从某方法出发 **向上** 追调用链（直到 REST / abstract / 无上游 / 环 / 深度上限） |

---

## `analyze`：详细说明

```text
jdtls-lsp analyze <project> <operation> [选项]
```

- **`<project>`**：项目根目录或任意路径；会**向上**查找 Maven（`pom.xml`）/ Gradle 根作为 LSP 工作区根。
- **`--jdtls`**：JDTLS 安装目录，缺省规则见上文「JDTLS 查找顺序」。

### 各 `operation` 所需参数

| operation | 必需参数 | 说明 |
|-----------|----------|------|
| `documentSymbol` | `--file` | 相对项目根或绝对路径的 `.java` |
| `workspaceSymbol` | `--query` | 工作区符号搜索；支持 **`\|`** 或 **全角 `｜`** 拼接多个子串，结果合并去重，最多 **20** 条 |
| `definition` | `--file`、`--line`，可选 `--char` | 行号、列号均为 **1-based**（`--char` 默认 1） |
| `references` | 同上 | 同 `definition` |
| `hover` | 同上 | 同 `definition` |
| `implementation` | 同上 | 同 `definition` |
| `incomingCalls` | 同上 | 在该位置解析 call hierarchy 后取 **incoming** |
| `outgoingCalls` | 同上 | 在该位置解析 call hierarchy 后取 **outgoing** |

**输出**：完整 JSON 字符串（无长度截断）。无结果时返回以 `无结果:` 开头的提示行。

### 示例

```bash
# 单文件结构树
jdtls-lsp analyze /path/to/project documentSymbol \
  --file src/main/java/com/example/App.java

# 工作区符号（支持多关键字）
jdtls-lsp analyze /path/to/project workspaceSymbol --query 'MyService|OrderService'

# 引用（光标所在符号）
jdtls-lsp analyze /path/to/project references \
  --file src/main/java/com/example/App.java --line 10 --char 1
```

---

## `callchain`：详细说明

```text
jdtls-lsp callchain <project> [入口三选一] [选项]
```

### 入口（**必须且只能**选一种）

| 方式 | 参数 | 说明 |
|------|------|------|
| **类 + 方法** | `--class` / `-k` 与 `--method` / `-m` | 类名可为全限定名或**简单类名**；方法名**不含**参数列表 |
| **文件 + 行** | `--file` / `-f` 与 `--line` / `-l` | `.java` 路径；**行号 1-based**；可选 `--char` / `-c`（列号 1-based，默认 1） |
| **关键字** | `--query` / `-q` | 见下文「关键字解析」 |

### 关键字 `--query` 如何解析

1. **形如 `类名.方法名`**（仅一段、且**不含** `\|` / `｜`）  
   直接按「类 + 方法」解析，**不**依赖 workspace 索引。

2. **单段普通字符串**（如 `createOrder`、`monitor_data`）  
   顺序尝试：  
   - `workspace/symbol`（多子串合并时各自去搜再合并去重）  
   - 若只匹配到类/接口：按启发式选类，再取该类中**第一个方法**作为起点（`workspace_class_first_method`）  
   - 仍无可用：在工程内 **\*.java** 全文搜索（`rg` 优先，否则 Python 扫描），命中后按启发式排序，再解析 call hierarchy 起点（`java_text_grep`）

3. **多段**（用 **`\|`** 或 **全角 `｜`** 拼接，且拆分后多于一段，如 `saveMonitorData|monitor_data`）  
   - **不**走 workspace / 类首方法捷径，**只做**各子串的 **\*.java 全文搜索**，合并命中。  
   - 同一文件内若解析到**不同方法**，可产生**多个起点**；多起点在**同一 JDTLS 连接**上 **串行** 向上追踪（避免并发 `incomingCalls` 触发 JDTLS 内部错误）。  
   - 搜索词为**原样**（不做 `monitor_data` → `MonitorData` 等自动转换）。

**`java_text_grep` 多起点时的可选过滤**（仅当关键字最终走全文 grep 时生效；默认**不**过滤，与旧行为一致）：

- `--grep-skip-interface`：丢弃「源文件顶层为 `interface X` 且 `X` 与文件名一致」的命中（典型为 `FooService.java` 中的接口方法）。
- `--grep-skip-rest-entry`：丢弃起点方法本身已是 REST（`isRest`）的命中（典型为 Controller 上已是单节点链的入口）。
- `--grep-max-entry-points N`：在过滤后按**实现类优先**（如 `*ServiceImpl`、`*Impl`）再取类名排序，只保留前 **N** 条起点。

组合示例：同一方法在 Controller / `ServiceImpl` / 接口上各有一条 grep 命中时，可用  
`--grep-skip-interface --grep-skip-rest-entry --grep-max-entry-points 1`  
只保留一条从 **实现类** 出发的链。

### 其他参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--max-depth` | 20 | 向上追踪最大层数 |
| `--format` | `markdown` | `markdown`：说明 + ASCII 图 + 末尾嵌入 JSON；`json`：仅 JSON |
| `--grep-workers` | — | 兼容保留；多入口串行追踪时**不**用于并行调度。可用环境变量 `JDTLS_LSP_GREP_WORKERS`（同上） |
| `--grep-skip-interface` | 关 | 仅 `java_text_grep`：跳过 interface 源文件中的命中 |
| `--grep-skip-rest-entry` | 关 | 仅 `java_text_grep`：跳过起点已是 REST 的方法 |
| `--grep-max-entry-points` | — | 仅 `java_text_grep`：最多 N 个起点（实现类优先排序后截断） |
| `--jdtls` | 自动查找 | JDTLS 根目录 |

### 输出字段（JSON / Markdown 嵌入块）

| 字段 | 含义 |
|------|------|
| `query` | 本次查询元数据（`mode`、`keyword`、`projectRoot`、`keywordResolution`、`grepEntryFilters` 等） |
| `chainCount` | 调用链条数 |
| `chains[].chain` | 从**起点方法**到**上层调用者**的节点列表（自下而上） |
| `chains[].stopReason` | 终止原因，见下表 |
| `chains[].topEntry` | 可选；对链顶节点解析 Spring **类级 `@RequestMapping` + 方法级映射**、**JavaDoc** `restPath` / `httpMethod` 等 |
| `chains[].grepSourceFile` 等 | 多 grep 起点时标注该链对应的入口文件/行/类名/方法名 |

**`stopReason` 常见值**

| 值 | 含义 |
|----|------|
| `rest_endpoint` | 检测到 Controller 上的 REST 映射 |
| `abstract_class` | 到达 abstract 类 |
| `no_incoming` | LSP `incomingCalls` 为空（无上层 Java 调用方） |
| `cycle` | 环 |
| `max_depth` | 达到 `--max-depth` |
| `jdtls_error` | JDTLS 对 `incomingCalls` 返回内部错误（实现侧会对锚点做**刷新与列偏移重试**；仍失败则记录 `jdtlsError`） |

### 示例

```bash
# 类 + 方法
jdtls-lsp callchain /path/to/project \
  --class com.example.service.OrderService --method createOrder --max-depth 20

# 文件 + 行号（1-based）
jdtls-lsp callchain /path/to/project \
  --file src/main/java/com/example/Foo.java --line 42

# 单关键字
jdtls-lsp callchain /path/to/project --query createOrder
jdtls-lsp callchain /path/to/project --query OrderServiceImpl.createOrder

# 多关键字（仅全文 grep 合并，适合同时搜表名与实体名）
jdtls-lsp callchain /path/to/project --query 'saveMonitorData|monitor_data' --format json

# 全文 grep 命中多处（Controller / Impl / 接口）时只保留一条实现类起点链
jdtls-lsp callchain /path/to/project --query saveMonitorData \
  --grep-skip-interface --grep-skip-rest-entry --grep-max-entry-points 1

# 仅 JSON、便于脚本解析
jdtls-lsp callchain /path/to/project --class Foo --method bar --format json
```

---

## 作为库调用

### `analyze_sync`

```python
from jdtls_lsp.analyze import analyze_sync

out = analyze_sync(
    "/path/to/project",
    "references",
    file_path="src/main/java/App.java",
    line=1,
    character=1,
    jdtls_path=None,  # 可选 Path
)
print(out)
```

### `trace_call_chain_sync`

```python
from pathlib import Path
from jdtls_lsp.callchain import trace_call_chain_sync

text = trace_call_chain_sync(
    "/path/to/project",
    symbol_query="monitor_data|MonitorData",
    jdtls_path=Path("/path/to/jdtls"),
    max_depth=50,
    output_format="json",  # 或 "markdown"
    grep_parallel_workers=None,  # 兼容参数；多入口串行时不影响调度
    grep_skip_interface=False,
    grep_skip_rest=False,
    grep_max_entry_points=None,  # 例如 1 与 CLI --grep-max-entry-points 1 一致
)
print(text)
```

### 日志（库内）

```python
from jdtls_lsp.logutil import setup_logging

setup_logging("INFO")  # 或 "DEBUG"，或环境变量 JDTLS_LSP_LOG
```

---

## 常见报错排查

### 需要 Python 3.10+ / 未找到 Python

```bash
python3 --version
cd jdtls-lsp-py && python3 -m pip install -e .
```

### 需要 Java 21+ / 未找到 Java

```bash
java -version
# 配置 JAVA_HOME 与 PATH
```

### `JDTLS not found under ...`

检查 `LITECLAW_JDTLS_PATH`、包内 `jdtls-lsp-py/jdtls`、当前目录 `./jdtls`、`~/jdtls` 是否存在且结构正确。

### `workspaceSymbol` 无结果

JDTLS 冷启动时索引未就绪较常见；实现中已对工作区符号 **延迟重试**（约 8 秒）。可改用更具体的包名前缀，或先执行 `documentSymbol` 确认文件可解析。

### `ModuleNotFoundError: No module named 'jdtls_lsp'`

未安装时使用：

```bash
cd jdtls-lsp-py
PYTHONPATH=src python3 -m jdtls_lsp analyze --help
```

### `callchain` 出现 `jdtls_error`

多为 **JDTLS 对某次 `incomingCalls` 的内部 bug**（日志里常见 NPE）。当前实现已做锚点重试；若仍失败，可换入口（`--class`/`--file`）或缩小 `--query` 范围。

---

## 离线包与 `setup.sh` 细节

`setup.sh` 会检查 Python/Java、尝试离线安装 OpenJDK、从 `offline-packages/` 解压 JDTLS 等；无离线包时输出中文指引。若解压后目录结构异常，请把正确 JDTLS 根目录放到 `jdtls-lsp-py/jdtls` 后重试。
