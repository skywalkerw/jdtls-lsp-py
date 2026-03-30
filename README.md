# jdtls-lsp（Python）

`jdtls-lsp` 是一个独立可运行的 Python 包，用于启动 **JDTLS**（Eclipse JDT Language Server），通过 **LSP（stdio JSON-RPC）** 做 Java 源码分析；语义与 LiteClaw 的 `lsp_java_analyze` 对齐。

---

## 依赖


| 依赖     | 说明                                                                                                      |
| ------ | ------------------------------------------------------------------------------------------------------- |
| Python | **3.10+**                                                                                               |
| Java   | **21+**；优先使用项目内 `jdtls-lsp-py/openjdk/bin/java`，其次 `./openjdk/bin/java`，否则 `PATH` / `JAVA_HOME`         |
| JDTLS  | 目录内需含 `config_mac` / `config_linux` / `config_win`（与系统对应）及 `plugins/org.eclipse.equinox.launcher_*.jar` |


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
- 便携包：`export.bat`（与 `export.sh` 相同规则；在上级目录生成 `*-portable-full.tar.gz` / `*-portable-mini.tar.gz`）。

---

## 命令总览

```text
jdtls-lsp [-v | -vv] [--log-level LEVEL] <子命令> ...
```


| 全局参数          | 说明                                                   |
| ------------- | ---------------------------------------------------- |
| `-v`          | 日志 **INFO**                                          |
| `-vv`         | 日志 **DEBUG**（含 LSP 请求/响应摘要，大 payload 会截断）            |
| `--log-level` | 直接指定 `DEBUG` / `INFO` / `WARNING` / `ERROR`（覆盖 `-v`） |


**日志**默认打到 **stderr**；**分析结果**（JSON / Markdown）在 **stdout**。保存日志示例：

```bash
jdtls-lsp -v callchain-up /path/to/project --query foo --format json 2> jdtls-lsp.log
```

**环境变量（日志）**


| 变量                                  | 说明                                                                                                               |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `JDTLS_LSP_LOG`                     | `debug` / `info` / `warning` / `error`（未传 `-v` 时生效）                                                              |
| `JDTLS_LSP_LOG_MAX_PAYLOAD`         | 单条日志里 JSON 序列化最大字符数（默认约 12000）                                                                                   |
| `JDTLS_LSP_DOCUMENT_SYMBOL_TIMEOUT` | `**analyze documentSymbol`** 单次 `textDocument/documentSymbol` 等待秒数（默认 **600**；**设计导出中的 `symbols` 为轻量扫描，不经 LSP**） |


**子命令**


| 子命令              | 作用                                                                                                                                              |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `analyze`        | 单次 LSP 操作（符号、定义、引用、call hierarchy、**类型层次** 等）                                                                                                   |
| `callchain-up`   | 从某方法出发 **向上** 追调用链（直到 REST / 消息监听 / 定时 / `@Async` / abstract / 无上游 / 环 / 深度上限）                                                                  |
| `callchain-down` | 从某方法出发 **向下** BFS 展开 `outgoingCalls` 子图（边表 + 节点上限）                                                                                              |
| `entrypoints`    | **静态入口扫描**之一：`main`、Spring 启动、Servlet、消息监听、定时、`@Async` 等（无需 JDTLS）；**REST 映射**见 `reverse-design rest-map`（同属静态入口，见下文专节）                         |
| `java-grep`      | 在 `*.java` 内**全文**搜索关键字（`rg` 优先，否则 Python；无需 JDTLS）                                                                                             |
| `reverse-design` | 逆向设计编排：`scan` / `db-tables` / `symbols` / `bundle`；`**rest-map`（step2）属静态入口扫描**，与 `entrypoints` 并列（见下文 **[静态入口扫描（无需 JDTLS）](#静态入口扫描无需-jdtls)**） |


**静态入口扫描（无需 JDTLS）**（详见专节）：`**entrypoints`**（`main`/Spring/Servlet/消息/定时等）与 `**reverse-design rest-map**`（HTTP 映射 → `rest-map.json`）同属从源码静态发现外向边界，**均不启 JDTLS**；常与 `**callchain-up` / `callchain-down`** 联用。

### 调用链与静态分析边界（易「断链」）

`callchain-up` / `callchain-down` 依赖 JDTLS 的 `**callHierarchy` + `incomingCalls` / `outgoingCalls`**，边来自 编译期/IDE 调用图，不是运行时。以下情形常见 链不完整 或 `**no_incoming`**（静态图里根本没有那条边）：


| 情形                                                                  | 为何断链                                                |
| ------------------------------------------------------------------- | --------------------------------------------------- |
| **反射**（`Method.invoke`、`Constructor.newInstance`、`Class.forName` 等） | 静态上多停在 `invoke`/`newInstance`；**无法**可靠解析到反射实际指向的方法。 |
| **动态代理**（JDK `Proxy`、CGLIB、拦截器等）                                    | 边多在代理/接口层，**具体实现类** 与手写调用方之间常缺边。                    |
| **工厂 / 容器**（`BeanFactory.getBean`、SPI、`ServiceLoader`、注册表按名取实现等）    | 若依赖 **运行时** 才绑定的类型，静态图往往只能追到接口或工厂方法。                |


**实务**：REST/直接调用仍较可靠；遇反射/代理路径可配合 `analyze references`、`java-grep` 或运行时工具补全。本工具**未**对反射/代理做单独修补，行为与 JDT 一致。

---

## `analyze`：详细说明

```text
jdtls-lsp analyze <project> <operation> [选项]
```

- `**<project>**`：项目根目录或任意路径；会**向上**查找 Maven（`pom.xml`）/ Gradle 根作为 LSP 工作区根。
- `**--jdtls`**：JDTLS 安装目录，缺省规则见上文「JDTLS 查找顺序」。

### 各 `operation` 所需参数


| operation         | 必需参数                          | 说明                                                                              |
| ----------------- | ----------------------------- | ------------------------------------------------------------------------------- |
| `documentSymbol`  | `--file`                      | 相对项目根或绝对路径的 `.java`                                                             |
| `workspaceSymbol` | `--query`                     | 工作区符号搜索；支持 `**                                                                  |
| `definition`      | `--file`、`--line`，可选 `--char` | 行号、列号均为 **1-based**（`--char` 默认 1）                                              |
| `references`      | 同上                            | 同 `definition`                                                                  |
| `hover`           | 同上                            | 同 `definition`                                                                  |
| `implementation`  | 同上                            | 同 `definition`                                                                  |
| `incomingCalls`   | 同上                            | 在该位置解析 call hierarchy 后取 **incoming**                                           |
| `outgoingCalls`   | 同上                            | 在该位置解析 call hierarchy 后取 **outgoing**                                           |
| `typeHierarchy`   | 同上                            | 光标须在**类型名**上：`prepareTypeHierarchy` + `subtypes` / `supertypes`（需 JDTLS/LSP 支持） |


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

## `callchain-up`：详细说明

```text
jdtls-lsp callchain-up <project> [入口三选一] [选项]
```

### 入口（**必须且只能**选一种）


| 方式         | 参数                                   | 说明                                                            |
| ---------- | ------------------------------------ | ------------------------------------------------------------- |
| **类 + 方法** | `--class` / `-k` 与 `--method` / `-m` | 类名可为全限定名或**简单类名**；方法名**不含**参数列表                               |
| **文件 + 行** | `--file` / `-f` 与 `--line` / `-l`    | `.java` 路径；**行号 1-based**；可选 `--char` / `-c`（列号 1-based，默认 1） |
| **关键字**    | `--query` / `-q`                     | 见下文「关键字解析」                                                    |


### 关键字 `--query` 如何解析

1. **形如 `类名.方法名`**（仅一段、且**不含** `\|` / `｜`）
  直接按「类 + 方法」解析，**不**依赖 workspace 索引；但当点两侧都是全大写 SQL 标识符（如 `SCHEMA.TABLENAME`）时，会按普通关键字处理，不做类/方法拆分。
  若要 100% 禁止拆分，请在点上使用转义：`schema\.tablename`（建议用单引号包住 `--query`，避免 shell 吃掉反斜杠）。
2. **单段普通字符串**（如 `createOrder`、`monitor_data`）
  顺序尝试：  
  - `workspace/symbol`（多子串合并时各自去搜再合并去重）  
  - 若只匹配到类/接口：按启发式选类，再取该类中**第一个方法**作为起点（`workspace_class_first_method`）  
  - 仍无可用：在工程内 **.java** 全文搜索（`rg` 优先，否则 Python 扫描），命中后按启发式排序，再解析 call hierarchy 起点（`java_text_grep`）
3. **多段**（用 `**\|`** 或 **全角 `｜`** 拼接，且拆分后多于一段，如 `saveMonitorData|monitor_data`）
  - **不**走 workspace / 类首方法捷径，**只做**各子串的 **.java 全文搜索**，合并命中。  
  - 同一文件内若解析到**不同方法**，可产生**多个起点**；多起点在**同一 JDTLS 连接**上 **串行** 向上追踪（避免并发 `incomingCalls` 触发 JDTLS 内部错误）。  
  - 搜索词为**原样**（不做 `monitor_data` → `MonitorData` 等自动转换）。

`**java_text_grep` 多起点时的可选过滤**（仅当关键字最终走全文 grep 时生效；默认**不**过滤，与旧行为一致）：

- `--grep-skip-interface`：丢弃「源文件顶层为 `interface X` 且 `X` 与文件名一致」的命中（典型为 `FooService.java` 中的接口方法）。
- `--grep-skip-rest-entry`：丢弃起点方法本身已是 REST（`isRest`）的命中（典型为 Controller 上已是单节点链的入口）。
- `--grep-max-entry-points N`：在过滤后按**实现类优先**（如 `*ServiceImpl`、`*Impl`）再取类名排序，只保留前 **N** 条起点。

组合示例：同一方法在 Controller / `ServiceImpl` / 接口上各有一条 grep 命中时，可用  
`--grep-skip-interface --grep-skip-rest-entry --grep-max-entry-points 1`  
只保留一条从 **实现类** 出发的链。

### 其他参数


| 参数                        | 默认         | 说明                                                                                                                                                    |
| ------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--max-depth`             | 20         | 向上追踪最大层数                                                                                                                                              |
| `--format`                | `markdown` | `markdown`：**查询** → **说明** → **统计** → **重点（边界入口）** → **各链（展开）** + 末尾 JSON；`json`：仅 JSON |
| `--grep-workers`          | —          | 兼容保留；多入口串行追踪时**不**用于并行调度。可用环境变量 `JDTLS_LSP_GREP_WORKERS`（同上）                                                                                          |
| `--grep-skip-interface`   | 关          | 仅 `java_text_grep`：跳过 interface 源文件中的命中                                                                                                               |
| `--grep-skip-rest-entry`  | 关          | 仅 `java_text_grep`：跳过起点已是 REST 的方法                                                                                                                    |
| `--grep-max-entry-points` | —          | 仅 `java_text_grep`：最多 N 个起点（实现类优先排序后截断）                                                                                                               |
| `--jdtls`                 | 自动查找       | JDTLS 根目录                                                                                                                                             |


### 输出字段（JSON / Markdown 嵌入块）


| 字段                          | 含义                                                                                                                                                                                 |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `query`                     | 本次查询元数据（`mode`、`keyword`、`projectRoot`、`keywordResolution`、`grepEntryFilters` 等）                                                                                                   |
| `chainCount`                | 调用链条数                                                                                                                                                                              |
| `chains[].chain`            | 从**起点方法**到**上层调用者**的节点列表（自下而上）                                                                                                                                                     |
| `chains[].stopReason`       | 终止原因，见下表                                                                                                                                                                           |
| `chains[].topEntry`         | 可选；`rest_endpoint` / `no_incoming` 时多为 Spring **REST + JavaDoc**；`message_listener` / `scheduled_task` / `async_method` 时为 `listenerMarkers` / `scheduledMarkers` / `asyncMarkers` |
| `chains[].grepSourceFile` 等 | 多 grep 起点时标注该链对应的入口文件/行/类名/方法名                                                                                                                                                     |


链上节点（`chains[].chain[]`）在能解析到源文件时还可含：`isRest`、`isMessageListener`、`isScheduledTask`、`isAsyncMethod`、`listenerMarkers`、`scheduledMarkers`、`asyncMarkers`（启发式，见 `jdtls_lsp.entry_scan.java_entry_patterns`）。链顶**同时**命中多类注解时终止码优先级：**REST > 消息监听 > 定时 > `@Async` > abstract**。

`**stopReason` 常见值**


| 值                  | 含义                                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------------------- |
| `rest_endpoint`    | 检测到 Controller 上的 REST 映射                                                                           |
| `message_listener` | 链顶方法上方窗口内匹配到 **消息消费者** 注解（如 `@KafkaListener`、`@RabbitListener` 等，见节点 `listenerMarkers`）             |
| `scheduled_task`   | 链顶匹配 **定时任务**（如 `@Scheduled`、`Quartz` `execute(JobExecutionContext`、`@XxlJob`，见 `scheduledMarkers`） |
| `async_method`     | 链顶方法上方窗口内匹配 **Spring `@Async`**（见 `asyncMarkers`；类级 `@Async` 若超出默认窗口可能未命中）                          |
| `abstract_class`   | 到达 abstract 类                                                                                       |
| `no_incoming`      | LSP `incomingCalls` 为空（无上层 Java 调用方）                                                                |
| `cycle`            | 环                                                                                                   |
| `max_depth`        | 达到 `--max-depth`                                                                                    |
| `jdtls_error`      | JDTLS 对 `incomingCalls` 返回内部错误（实现侧会对锚点做**刷新与列偏移重试**；仍失败则记录 `jdtlsError`）                            |


### 示例

```bash
# 类 + 方法
jdtls-lsp callchain-up /path/to/project \
  --class com.example.service.OrderService --method createOrder --max-depth 20

# 文件 + 行号（1-based）
jdtls-lsp callchain-up /path/to/project \
  --file src/main/java/com/example/Foo.java --line 42

# 单关键字
jdtls-lsp callchain-up /path/to/project --query createOrder
jdtls-lsp callchain-up /path/to/project --query OrderServiceImpl.createOrder

# 多关键字（仅全文 grep 合并，适合同时搜表名与实体名）
jdtls-lsp callchain-up /path/to/project --query 'saveMonitorData|monitor_data' --format json

# 全文 grep 命中多处（Controller / Impl / 接口）时只保留一条实现类起点链
jdtls-lsp callchain-up /path/to/project --query saveMonitorData \
  --grep-skip-interface --grep-skip-rest-entry --grep-max-entry-points 1

# 仅 JSON、便于脚本解析
jdtls-lsp callchain-up /path/to/project --class Foo --method bar --format json
```

---

## `callchain-down`：详细说明

从某方法出发，沿 `**callHierarchy/outgoingCalls**` 做 **BFS**，展开**下游**有向子图（节点 + 边）。与 `callchain-up` 共用「类+方法 / 文件+行 / 关键字」入口，但**仅支持单起点**；**不**沿 `incomingCalls` 向上走。

对在 **接口** 或 **abstract** 方法**声明**上的节点，`outgoingCalls` 往往为空（无方法体）。此时会自动调用 `**textDocument/implementation**` 找到实现类中的对应方法，再 `prepareCallHierarchy` 后继续向下扩展；合成边带 `syntheticImplementation: true`，`stats.implementationFallbackEdges` 为本次兜底边条数。

```text
jdtls-lsp callchain-down <project> [入口三选一] [选项]
```

### 入口（**必须且只能**选一种）


| 方式         | 参数                                   | 说明                                                            |
| ---------- | ------------------------------------ | ------------------------------------------------------------- |
| **类 + 方法** | `--class` / `-k` 与 `--method` / `-m` | 与 `callchain-up` 相同：全限定名或简单类名；方法名**不含**参数列表                   |
| **文件 + 行** | `--file` / `-f` 与 `--line` / `-l`    | `.java` 路径；**行号 1-based**；可选 `--char` / `-c`（列号 1-based，默认 1） |
| **关键字**    | `--query` / `-q`                     | 解析规则见下（与 `callchain-up` 有关键差异）                                |


### 关键字 `--query` 与 `callchain-up` 的差异

1. **单段 `类名.方法名`**（不含 `\|` / `｜`）
  与 `callchain-up` 相同：直接按类+方法解析，**不**依赖 workspace 索引；但当点两侧都是全大写 SQL 标识符（如 `SCHEMA.TABLENAME`）时，会按普通关键字处理，不做类/方法拆分。
  若要 100% 禁止拆分，请在点上使用转义：`schema\.tablename`（建议用单引号包住 `--query`，避免 shell 吃掉反斜杠）。
2. **其余单段 / 多段**
  解析流程与 `callchain-up` 中「关键字解析」一致（`workspace/symbol`、类首方法、`java_text_grep` 等）。  
   **但若** 关键字最终对应 `**java_text_grep` 且跨多文件产生多起点**（内部标记 `javaGrepMultiFile`），`callchain-down` **会报错退出**，并提示改用 `--class`/`--method`、`--file`/`--line` 或更精确的单文件关键字。  
   **多段** `saveMonitorData|monitor_data` 若合并命中后仍是**单起点**，则可用；否则会触发上述限制。
3. `**java_text_grep` 过滤**（仅当关键字走全文 grep 时生效；与 `callchain-up` 相同）
  `--grep-skip-interface`、`--grep-skip-rest-entry`、`--grep-max-entry-points N` 可用于**收敛到单一起点**（例如 `--grep-max-entry-points 1`）。

### 其他参数


| 参数                        | 默认         | 说明                                                                                   |
| ------------------------- | ---------- | ------------------------------------------------------------------------------------ |
| `--max-depth`             | 8          | 自起点向下 BFS 最大层数（起点深度为 0；仅当深度 `< max-depth` 时才继续扩展子节点）                                 |
| `--max-nodes`             | 500        | 子图中最多收录的**不同**方法节点数（达到后不再加入新节点）                                                      |
| `--max-branches`          | 32         | **每一层** 对 `outgoingCalls` 排序后**最多**保留的出边条数（超出部分丢弃，并可能将 `stopReason` 记为 `branch_cap`） |
| `--format`                | `markdown` | `markdown`：概要 + 下游叶节点分类说明 + 嵌入 JSON；`json`：仅 JSON                                    |
| `--grep-skip-interface`   | 关          | 仅关键字 `java_text_grep`：跳过 interface 源文件命中                                             |
| `--grep-skip-rest-entry`  | 关          | 仅关键字 `java_text_grep`：跳过起点已是 REST 的方法                                                |
| `--grep-max-entry-points` | —          | 仅关键字 `java_text_grep`：最多 N 个起点（实现类优先排序后截断）                                           |
| `--jdtls`                 | 自动查找       | JDTLS 根目录                                                                            |


### 输出字段（JSON / Markdown 嵌入块）


| 字段            | 含义                                                                                                                                        |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `query`       | 查询元数据（`mode`、`keyword`、`projectRoot`、`className`/`methodName` 等，与入口方式对应）                                                                  |
| `direction`   | 固定为 `down`                                                                                                                                |
| `traversal`   | 固定为 `bfs`                                                                                                                                 |
| `nodes`       | 字典：**键**为内部稳定键 `file`:`line`:`character`:`method`，**值**为节点（`class`、`method`、`file`、`line`、`character`、`uri`、`isRest`、`isAbstractClass` 等） |
| `startKey`    | 起点节点键（与 `nodes` 中键一致）；Markdown 中 **ASCII 树** 自该键展开                                                                 |
| `edges`       | 数组：`{ "from": 键, "to": 键, "fromRanges": … }`，表示一条静态调用边；经实现类兜底时可有 `syntheticImplementation`                                                                                    |
| `stats`       | `nodeCount`、`edgeCount`、`expandedCount`、`implementationFallbackEdges`（接口/abstract 上经 `implementation` 兜底的边数），以及本次使用的 `maxDepth` / `maxNodes` / `maxBranches` 配置快照                                              |
| `stopReason`  | 终止原因，见下表                                                                                                                                  |
| `jdtlsErrors` | 某次 `outgoingCalls` 失败时的锚点键与错误摘要（不阻断已收集子图输出）                                                                                               |


`**stopReason`（向下）常见值**


| 值            | 含义                                                                               |
| ------------ | -------------------------------------------------------------------------------- |
| `complete`   | 队列耗尽；未出现 `branch_cap` / `max_nodes`（收尾时若节点数已达 `--max-nodes`，结果中也可能为 `max_nodes`） |
| `max_nodes`  | 收录节点数达到 `--max-nodes`                                                            |
| `branch_cap` | 至少有一层 `outgoingCalls` 条数超过 `--max-branches`，该层只保留前 N 条                           |


### Markdown 与 JSON 的差异

- `**--format markdown`（默认）**：**查询** → **说明** → **统计** → **重点（下游终点）** → **ASCII 树（展开）** →（可选）**JavaBean 合并**、**关键业务候选（step6）** → **边（前 200）**；简单 get/set/is 叶节点在正文里常按文件汇总。**嵌入块中的 JSON** 与 `json` 模式同源、节点完整。
- `**--format json`**：`nodes` / `edges` **完整**，无删减。

### 示例

```bash
# 类 + 方法（默认 Markdown，含嵌入 JSON）
jdtls-lsp callchain-down /path/to/project \
  --class com.example.service.OrderService --method createOrder

# 仅 JSON，便于脚本消费
jdtls-lsp callchain-down /path/to/project \
  --class com.example.service.OrderService --method createOrder --format json

# 文件 + 行号（1-based），收紧深度与节点上限
jdtls-lsp callchain-down /path/to/project \
  --file src/main/java/com/example/Foo.java --line 42 --max-depth 5 --max-nodes 200

# 单关键字（与 callchain-up 相同解析；需单起点）
jdtls-lsp callchain-down /path/to/project --query exportMonitorDataToExcel --max-depth 10

# 单段 类.方法
jdtls-lsp callchain-down /path/to/project --query ExportServiceImpl.exportMonitorDataToExcel

# 全文 grep 命中多处时，只保留一条实现类起点（避免多起点报错）
jdtls-lsp callchain-down /path/to/project --query saveMonitorData \
  --grep-skip-interface --grep-skip-rest-entry --grep-max-entry-points 1

# 每层允许更多出边（默认 32）
jdtls-lsp callchain-down /path/to/project --class Foo --method bar --max-branches 48
```

---

## 静态入口扫描（无需 JDTLS）

`**entrypoints**` 负责非 HTTP 类入口；**REST 映射**见 `**reverse-design rest-map`**（[下一小节](#rest-映射reverse-design-rest-map)）。

### `entrypoints`（非 HTTP 静态入口）

不启动 JDTLS，仅在工程内扫描 `*.java`（跳过 `target`/`build` 等目录），按**行**做启发式匹配（同一行可产生多条不同 `kind` 记录）。


| `kind`                          | 匹配含义                                                               |
| ------------------------------- | ------------------------------------------------------------------ |
| `main`                          | `public static void main(String[] …)` 或 `String... args`           |
| `spring_boot_application`       | `@SpringBootApplication`                                           |
| `spring_application_run`        | `SpringApplication.run(`（常见启动调用）                                   |
| `web_application_initializer`   | 行内含 `WebApplicationInitializer`                                    |
| `web_servlet`                   | `@WebServlet(`                                                     |
| `web_filter`                    | `@WebFilter(`                                                      |
| `web_listener`                  | `@WebListener`                                                     |
| `http_servlet`                  | `extends` … `HttpServlet`（含 `javax` / `jakarta` 或短类名）              |
| `servlet`                       | `implements` … `javax.servlet.Servlet` / `jakarta.servlet.Servlet` |
| `servlet_container_initializer` | `implements` … `ServletContainerInitializer`                       |
| `kafka_listener`                | `@KafkaListener`                                                   |
| `rabbit_listener`               | `@RabbitListener`                                                  |
| `jms_listener`                  | `@JmsListener`                                                     |
| `rocketmq_message_listener`     | `@RocketMQMessageListener`                                         |
| `sqs_listener`                  | `@SqsListener`（如 Spring Cloud AWS）                                 |
| `stream_listener`               | `@StreamListener`                                                  |
| `pulsar_listener`               | `@PulsarListener`                                                  |
| `incoming_channel`              | `@Incoming(`（MicroProfile / SmallRye 等）                            |
| `service_activator`             | `@ServiceActivator`（Spring Integration）                            |
| `scheduled`                     | `@Scheduled`                                                       |
| `schedules`                     | `@Schedules`                                                       |
| `quartz_job_execute`            | `void execute(JobExecutionContext…`（Quartz `Job`）                  |
| `xxl_job`                       | `@XxlJob`                                                          |
| `spring_async`                  | `@Async`（Spring）                                                   |


```bash
jdtls-lsp entrypoints /path/to/project
# 限制扫描文件数（默认 30000）
jdtls-lsp entrypoints /path/to/project --max-files 5000
```

输出 JSON：`projectRoot`、`entryCount`、`entries[]`（`kind`、`file`、`line`、`preview`）。

### REST 映射（`reverse-design rest-map`）

**静态入口扫描**的 HTTP 分支：启发式扫描 Spring MVC 映射（**非**完整 AST，与运行时路由可能不一致），输出 `**rest-map.json`**（端点列表、Controller 方法锚点）。**step2** 与设计导出 `**reverse-design bundle`** 中的 `**--skip-rest-map**` / `**graphs/rest-map.mmd**` 见下文 `**[reverse-design](#reverse-design逆向设计导出step1step8)**`。

```bash
jdtls-lsp reverse-design rest-map /path/to/project --max-files 8000
```

---

## `java-grep`：Java 全文搜索（无需 JDTLS）

与 `callchain-up` 中 `**java_text_grep**` 使用相同的 **needle 规则**（`\|` / `｜` 拆多段、合并命中）与 **跳过 `target`/`build` 等目录** 的策略；仅做文本搜索，**不**解析符号、**不**调 LSP。


| 参数               | 默认     | 说明                                                                                |
| ---------------- | ------ | --------------------------------------------------------------------------------- |
| `--query` / `-q` | （必填）   | 关键字；支持 `**                                                                        |
| `--max-hits`     | `200`  | 返回条数上限                                                                            |
| `--no-sort`      | 关      | 不按 `score_grep_hit` 启发式排序                                                         |
| `--format`       | `json` | `json`：`needles`、`hits[]`（`file`、`line`、`text`、`score`）；`text`：每行 `path:line:行内容` |


```bash
jdtls-lsp java-grep /path/to/project -q saveMonitorData --format json
jdtls-lsp java-grep /path/to/project -q 'foo|bar' --format text --max-hits 50
```

---

## `reverse-design`：逆向设计导出（step1–step8）

与仓库根目录 `**需求.md**`、`docs/REVERSE_ENGINEERING_DESIGN.md` **§0** 对齐：先建立 **step1–3** 扫描产物，再可选 **step4–6** 调用链与业务摘要，**step7** 用 `analyze` / 单点 callchain / IDE 补全，**step8** 由 `bundle` 写 `index.md` 与 stdout 摘要。

**step2（`rest-map`）**在分类上归入 **[静态入口扫描（无需 JDTLS）](#静态入口扫描无需-jdtls)**（与 `**entrypoints`** 并列，均不启 JDTLS）；此处仍作为 `reverse-design` 子命令，便于与 `**bundle**` 产物目录一致。

**八步一览**


| step  | 目标                | 主要手段 / 产物                                                                                                                                   |
| ----- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| step1 | 工程概要              | `scan` → `modules.json`；`symbols` → `symbols-by-package.json`                                                                               |
| step2 | REST 清单（**静态入口**） | `rest-map` → `rest-map.json`                                                                                                                |
| step3 | 数据库表清单            | `db-tables` / bundle → `tables-manifest.json`                                                                                               |
| step4 | 每入口向下调用链          | bundle `--entrypoint-callchain-down` → `data/callchain-down-entrypoints/<safe_entrypoint_file>/callchain-down-entrypoints-*.md`（文末含完整 JSON） |
| step5 | 每表向上调用链           | bundle `--table-callchain-up` → `data/callchain-up-table/<物理表>/callchain-up-table-*.md`；`--queries` 为 **关键字向上（step5′）**                     |
| step6 | 链上关键业务            | 向下链 JSON 内标权；`--business-summary` → `business.md`                                                                                           |
| step7 | 补全实现细节            | **非 bundle 全自动**：`analyze`、`callchain-up` / `callchain-down`、IDE                                                                            |
| step8 | 汇总输出              | bundle 收尾：`index.md` + stdout JSON                                                                                                          |


子命令：

```text
jdtls-lsp reverse-design { scan | rest-map | db-tables | symbols | bundle } ...
```


| 子命令                                  | step     | 依赖 JDTLS | 说明                                                                                                                                                                                                                                |
| ------------------------------------ | -------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `reverse-design scan <project>`      | step1    | 否        | 解析根目录 `pom.xml`（`modules` / `artifactId`）与 `settings.gradle*`（`include`），输出 JSON                                                                                                                                                  |
| `reverse-design rest-map <project>`  | step2    | 否        | **静态入口扫描**（HTTP）：启发式 `@GetMapping` / `@RequestMapping` 等（**非** AST；与运行时路由可能不完全一致）                                                                                                                                                 |
| `reverse-design db-tables <project>` | step3    | **否**    | `**tables-manifest` JSON**：`@Table`、含 SQL 语义的 **字符串字面量**（`FROM`/`JOIN`/`INTO`/`UPDATE`）、MyBatis `**table=`** 与 XML 内字面量 SQL；`**--tables-file**` / `**--tables**` 提供**规范表名**（`canonicalTables`、`unresolvedTables`、`extractedOnly`） |
| `reverse-design symbols <project>`   | step1 补充 | **否**    | **轻量扫描**（注释/字符串感知）匹配 glob 的 `*.java`，按 **package** 聚合顶层 `class` / `interface` / `enum` / `record`（无成员、无嵌套类型；大文件也秒级）                                                                                                               |
| `reverse-design bundle <project>`    | step8 编排 | 可选       | **一键**：默认 step1–3 + 可选 step4–6（**step4/step5/step5′ 在同一次 bundle 内共用一次 JDTLS**）+ 可选 `**--business-summary`**。输出 `**-o` / `--output**`（默认 `./design`）。详见 **[reverse-design bundle 详细说明](#reverse-design-bundle-详细说明)**。             |


```bash
# step1
jdtls-lsp reverse-design scan /path/to/maven-or-gradle-root

# step2
jdtls-lsp reverse-design rest-map /path/to/project --max-files 8000

# step3：用户表清单 + 抽取（可与 bundle 共用 --tables-file / --tables）
jdtls-lsp reverse-design db-tables /path/to/project --tables-file ./tables.txt

# step1 补充 / symbols（无 JDTLS；大项目可调小 --max-files）
jdtls-lsp reverse-design symbols /path/to/project --glob '**/src/main/java/**/*.java' --max-files 500

# step8 编排：跳过符号与调用链（只要 step1 模块 + step2/3 扫描时）
jdtls-lsp reverse-design bundle /path/to/project -o ./design-out --skip-symbols --skip-callchain

# step8：step5′ + step5 + step4 + step6（callchain 在同一次 bundle 内只启一个 JDTLS JVM）
jdtls-lsp reverse-design bundle /path/to/project -o ./design-out --skip-symbols \
  --queries saveMonitorData,exportData --table-callchain-up \
  --entrypoint-callchain-down --max-rest-down-endpoints 20 --business-summary

# 仅 step5′ 关键字向上（多关键字仍共用同一条 LSP 连接）
jdtls-lsp reverse-design bundle /path/to/project -o ./design-out --queries saveMonitorData,exportData

# step5：按表向上（需 JDTLS；与其它 callchain 开关同次运行则共用 JVM）
jdtls-lsp reverse-design bundle /path/to/project -o ./design-out --skip-symbols --table-callchain-up

# step4：按 entrypoints 向下（需 JDTLS；起点多时务必加 --max-rest-down-endpoints 试跑）
jdtls-lsp reverse-design bundle /path/to/project -o ./design-out --skip-symbols \
  --entrypoint-callchain-down --max-rest-down-endpoints 10

# step6：已有 callchain-down-*/**/callchain-down-*-*.md（或 data 根下遗留扁平 .md/.json）时只生成 business.md（可与 --skip-callchain 同用）
jdtls-lsp reverse-design bundle /path/to/project -o ./design-out --skip-callchain --business-summary
```

### reverse-design bundle 详细说明

**step8 编排**：在**项目根**上顺序执行 **step1–3**（含 **step1 补充** `symbols`）与可选 **step4–6**，最后写 **step8** `index.md` 与 stdout 摘要。**输出目录**默认 `./design/`。**目录层次**：根目录含 `index.md`（**step8**）、可选 `business.md`（**step6**）、汇总 JSON；`**data/`** 含 step1–3 扫描与各 callchain 明细；`**graphs/**` 含 **step2** Mermaid。**step4/step5/step5′** 在**未** `--skip-callchain` 且至少启用其一 时**共用一次 JDTLS**。**step6**：`--business-summary` 合并 `keyMethods` → `business.md`。**step7** 不在 bundle 内，见下文「耗时与日志」后 analyze 说明。

**基本命令**：

```bash
jdtls-lsp reverse-design bundle <project> [-o ./design-out] [选项…]
```

**输出目录结构**（相对 `-o`）：

```text
design-out/
  index.md                        # step8：产物分层、八步对照、Warnings
  business.md                     # step6：仅当 --business-summary
  table-callchain-summary.json   # 仅当 --table-callchain-up：按物理表分组汇总（键同 data/callchain-up-table/<表>/）
  entrypoint-callchain-down-summary.json  # 仅当 --entrypoint-callchain-down：汇总各 entrypoint 向下链结果
  data/
    modules.json                  # step1（除非 --skip-scan）
    rest-map.json                 # step2（除非 --skip-rest-map）
    tables-manifest.json          # step3（除非 --skip-table-manifest）
    symbols-by-package.json       # step1 补充（除非 --skip-symbols）
    callchain-up-<安全文件名>.md       # 仅当 --queries
    callchain-up-table/<物理表名>/callchain-up-table-<表>.md  # 仅当 --table-callchain-up 且该表成功
    callchain-up-table/<物理表名>/…-sql-NN.md、…-mapper-NN.md  # 仅当同时 --table-callchain-up-extra
    callchain-down-entrypoints/<safe_entrypoint_file>/callchain-down-entrypoints-*.md  # step4；含 step6 节点字段（文末 JSON）
  graphs/
    rest-map.mmd                  # step2（除非 --skip-rest-map）
```

**bundle 内执行顺序**（与 `需求.md` **step 叙述序号**不完全一致，语义对应 step1–6、8；前一步失败时后续仍可能继续，`warnings` 会记录）：

1. **step1** `modules.json`（`--skip-scan` 则跳过）
2. **step2** `rest-map.json` + `graphs/rest-map.mmd`（`--skip-rest-map` **或** 启用 `--entrypoint-callchain-down` 时跳过生成；详见下文「`--skip-rest-map`」）
3. **step3** `tables-manifest.json`（`--skip-table-manifest` 则跳过）
4. **step1 补充** `symbols-by-package.json`（`--skip-symbols` 则跳过；**无 JDTLS**）
5. **step5′ 关键字向上**：`--queries` 且**未** `--skip-callchain` → `data/callchain-up-*.md`
6. **step5 按表向上**：`--table-callchain-up` 且**未** `--skip-callchain` → `data/callchain-up-table/<物理表>/callchain-up-table-*.md`、根目录 `**table-callchain-summary.json`**；若再加 `**--table-callchain-up-extra**` → 同目录下另含 `*-sql-NN.md`、`*-mapper-NN.md`（上限见 `--max-table-up-extra-anchors`）
7. **step4 entrypoints 向下**：`--entrypoint-callchain-down` 且**未** `--skip-callchain` → `data/callchain-down-entrypoints/<safe_entrypoint_file>/callchain-down-entrypoints-*.md`、`**entrypoint-callchain-down-summary.json`**。起点来自 `scan_java_entrypoints`（含 `@Controller`/`@RestController` 的 **public** 方法与其它典型入口行）。可用 `**--max-rest-down-endpoints N`** 限流
  **JDTLS**：bundle 内 **step5′、step5、step4** 在至少启用其一且未 `--skip-callchain` 时 **共用一次 JVM**。单独执行 `jdtls-lsp callchain-up` / `callchain-down` 仍为**每次命令**各启 JDTLS（适合 **step7**）。
8. **step6**：`--business-summary`（在 callchain 子步骤之后）：合并 `keyMethods` → `**business.md`**；可与 `**--skip-callchain**` 同用。摘要 JSON 含 `**businessSummary**`。
9. **step8**：写 `**index.md`**（含八步对照表）+ **stdout JSON 摘要**

**CLI 参数一览**：


| 参数                             | 默认                           | 说明                                                                                                                                                                                                                                                                                    |
| ------------------------------ | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `project`                      | （位置参数）                       | 项目根目录                                                                                                                                                                                                                                                                                 |
| `-o` / `--output`              | `./design`                   | 输出根目录；会创建 `data/`、`graphs/`                                                                                                                                                                                                                                                           |
| `--skip-scan`                  | 关                            | 不生成 `modules.json`                                                                                                                                                                                                                                                                    |
| `--skip-rest-map`              | 关                            | 不生成 `rest-map.json` 与 `graphs/rest-map.mmd`                                                                                                                                                                                                                                           |
| `--skip-table-manifest`        | 关                            | 不生成 `tables-manifest.json`                                                                                                                                                                                                                                                            |
| `--skip-symbols`               | 关                            | 不生成 `symbols-by-package.json`                                                                                                                                                                                                                                                         |
| `--skip-callchain`             | 关                            | **关闭所有**需 JDTLS 的调用链：`--queries`、`--table-callchain-up`、`**--entrypoint-callchain-down`**                                                                                                                                                                                             |
| `--queries`                    | 空                            | 逗号分隔关键字；每个关键字输出一个 `data/callchain-up-*.md`（Markdown，文末嵌入 JSON）；与 `callchain-up` 子命令的关键字规则一致                                                                                                                                                                                           |
| `--table-callchain-up`         | 关                            | 按表自动向上调用链；依赖 `tables-manifest` 中的蛇形表名；与 `--skip-callchain` 同时指定则跳过                                                                                                                                                                                                                    |
| `--table-callchain-up-extra`   | 关                            | **须与** `--table-callchain-up` **同用**：额外对 manifest 中 JDBC 字符串 SQL（`*.java`）与 MyBatis XML→Mapper 方法跑 callchain-up（`*-sql-NN.md`、`*-mapper-NN.md`）                                                                                                                                       |
| `--max-table-up-extra-anchors` | `24`                         | 与上一项联用：每张表 **SQL 与 MyBatis 各自**最多几条起点；`0` 表示不限制                                                                                                                                                                                                                                       |
| `--max-table-callchain-scan`   | `12000`                      | 为每张表在仓库内最多检查多少个 `*ServiceImpl.java` 路径以查找 `EntityRepository` 注入                                                                                                                                                                                                                       |
| `--entrypoint-callchain-down`  | 关                            | 对 `scan_java_entrypoints` 的每个起点跑 `callchain-down`；与 `--skip-callchain` 同时指定则跳过                                                                                                                                                                                                        |
| `--max-rest-down-endpoints`    | `0`                          | 只处理前 N 个 entrypoint（`scan_java_entrypoints` 返回顺序）；`0` 表示**不限制**（起点多时耗时会很长）                                                                                                                                                                                                            |
| `--rest-down-depth`            | `16`                         | 向下 BFS 最大深度（同 `callchain-down --max-depth`）                                                                                                                                                                                                                                           |
| `--rest-down-max-nodes`        | `500`                        | 向下子图节点上限（同 `callchain-down --max-nodes`）                                                                                                                                                                                                                                              |
| `--rest-down-max-branches`     | `48`                         | 每层 outgoing 分支上限（同 `callchain-down --max-branches`）                                                                                                                                                                                                                                   |
| `--business-summary`           | 关                            | **step6**：递归合并向下链报告（`callchain-down-rest-*` / `callchain-down-entrypoints-*` 的 `.md`/`.json`，及历史上 `data/` 根下同名扁平文件）的 `keyMethods` → 根目录 `business.md`。可与 `**--skip-callchain`** 同用（只扫描已有报告；无 `keyMethods` 时会现场补算）。**不依赖**本轮是否 `--entrypoint-callchain-down`，但无匹配文件时 `mergedCount` 为 0 |
| `--tables-file`                | 无                            | 每行一个规范表名（`#` 注释）；与 `reverse-design db-tables` 相同语义，用于 `canonicalTables` / `unresolvedTables`                                                                                                                                                                                          |
| `--tables`                     | 空                            | 逗号分隔表名，与 `--tables-file` 合并                                                                                                                                                                                                                                                           |
| `--strict-tables-only`         | 关                            | `tables-manifest.json` 中不列出 `extractedOnly`（仍参与抽取与锚点）                                                                                                                                                                                                                                 |
| `--max-table-java-files`       | `8000`                       | **step3**（tables-manifest）最多扫描的 `.java` 数                                                                                                                                                                                                                                             |
| `--max-table-xml-files`        | `2000`                       | **step3** 最多扫描的 `.xml` 数（MyBatis 等）                                                                                                                                                                                                                                                   |
| `--glob`                       | `**/src/main/java/**/*.java` | **step1 补充** 轻量扫描的 glob（相对项目根）                                                                                                                                                                                                                                                        |
| `--max-symbol-files`           | `200`                        | **step1 补充** 最多处理的 `.java` 文件数                                                                                                                                                                                                                                                        |
| `--max-rest-files`             | `8000`                       | **step2** REST 扫描最多 `.java` 文件数                                                                                                                                                                                                                                                       |
| `--callchain-depth`            | `20`                         | `callchain-up` 最大向上深度（`--queries` 与 `--table-callchain-up` 共用）                                                                                                                                                                                                                        |
| `--jdtls`                      | 环境/默认路径                      | JDTLS 安装目录                                                                                                                                                                                                                                                                            |
| `--quiet`                      | 关                            | TTY 下也不自动升到 INFO 日志；适合脚本只收 stdout JSON                                                                                                                                                                                                                                                |


`**--skip-table-manifest` 与 `--table-callchain-up`**：

- 若**未** skip manifest：本次运行会生成 `data/tables-manifest.json`，并按其跑按表链。
- 若 **skip** manifest：不会重写 `tables-manifest.json`；此时若输出目录里**已有** `data/tables-manifest.json`（例如上次 bundle 产物），仍会读取并跑 `--table-callchain-up`；若不存在该文件，则跳过按表链并在 `warnings` 中说明。

`**--skip-rest-map` 与 `--entrypoint-callchain-down`**：

- **当两者都未启用**：会生成 `data/rest-map.json` + `graphs/rest-map.mmd`。
- **当启用 `--entrypoint-callchain-down`**：step4 **不依赖** `rest-map`，因此 bundle **跳过生成** `rest-map.json` / `graphs/rest-map.mmd`（避免额外静态扫描）；若仍需要 `rest-map.json`，请**不要**加 `--entrypoint-callchain-down`，或另外跑 `reverse-design rest-map`。
- **当启用 `--skip-rest-map`**：不会重写 `rest-map.json`；若输出目录里**已有** `data/rest-map.json`，仍保留给阅读/手工锚点使用。

`**--business-summary`（step6）**：

- 依赖 `**data/callchain-down-entrypoints/.../callchain-down-entrypoints-*.md`** 或历史 `**data/callchain-down-rest/...**`（或遗留扁平 `.md`/`.json`）：通常与 `**--entrypoint-callchain-down**` 同次或前次 bundle 产物配合使用。
- **独立使用**：`--skip-callchain --business-summary` 仅根据输出目录里**已有**的向下链 JSON 生成/覆盖 `business.md`，适合 CI 分步或只刷新聚合视图。
- 单条 `callchain-down` JSON 内已含节点级 `**businessScore` / `businessCandidate` / `businessSignals`** 与顶层 `**keyMethods**`（**不含** `javadoc` 字段；顶层 `**businessPhase`** 为 `**step6**`）。生成 `**business.md**` 时由 `**jdtls_lsp.java_javadoc.extract_javadoc_above_method**` 按 `file`+`line` 从源码解析 Javadoc。

**stdout 摘要 JSON**（成功时）常见字段：

- `projectRoot`、`outputDir`、`generatedAt`
- `artifacts`：相对输出目录的路径列表（含 `index.md` 与各 `data/*`、`graphs/*`）
- `warnings`：字符串列表（表未解析、callchain 失败等）
- `tableManifest`：在生成 manifest 时含 `canonicalCount`、`hitCount`、`unresolvedCount` 等
- `tableCallchainUp`：在启用 `--table-callchain-up` 且未 `--skip-callchain` 时含 `summaryFile`、`resolvedCount`、`errorCount`、`skippedCount`
- `entrypointCallchainDown`：在启用 `--entrypoint-callchain-down` 且未 `--skip-callchain` 时含 `summaryFile`、`resolvedCount`、`errorCount`
- `businessSummary`：在启用 `--business-summary` 时含 `file`（`business.md`）、`mergedCount`、`downchainReportFilesRead`

**耗时与日志**：

- **快**：**step1–3** 与 **step1 补充**（`symbols`）均为本地扫描（轻量实现，一般秒级～分钟级，视仓库大小与 `--max-symbol-files`）。
- **慢**：在 **bundle** 中 **step5′/5/4** **共用一次 JDTLS**；单次 `callchain-up`/`down` 命令仍为每次各启 JVM（**step7**）。向下链在起点多时仍可能较慢，可配合 `**--max-rest-down-endpoints`** 试跑。
- 在终端直接运行时，若未 `--quiet` 且未手动设日志级别，bundle 会将日志提到 **INFO**，避免「无输出像卡住」；需要完整 JSON 可重定向 stdout，进度看 stderr。

**示例**：

```bash
# 全量默认（含 symbols 与 manifest；无调用链）
jdtls-lsp reverse-design bundle /path/to/project -o ./design

# 只要结构 + REST + 表清单，不要符号、不要 JDTLS
jdtls-lsp reverse-design bundle /path/to/project -o ./design --skip-symbols --skip-callchain

# 用户表清单为准（与 db-tables 一致），并生成按表向上调用链
jdtls-lsp reverse-design bundle /path/to/project -o ./design \
  --tables-file ./db-tables.txt --skip-symbols --table-callchain-up

# 关键字 + 按表同时跑（同一次 bundle 内仍只启一个 JDTLS；耗时主要来自 LSP 请求次数）
jdtls-lsp reverse-design bundle /path/to/project -o ./design \
  --queries saveMonitorData --table-callchain-up

# step4 + step6：entrypoints 向下链 + business.md
jdtls-lsp reverse-design bundle /path/to/project -o ./design --skip-symbols \
  --entrypoint-callchain-down --max-rest-down-endpoints 50 --business-summary

# entrypoints 全量向下链（起点多时极慢；务必加 --max-rest-down-endpoints 试跑）
jdtls-lsp reverse-design bundle /path/to/project -o ./design --skip-symbols --entrypoint-callchain-down

# CI：仅 JSON 到 stdout，减少控制台日志
jdtls-lsp reverse-design bundle /path/to/project -o ./design-out --quiet > bundle-summary.json
```

**排查「卡住」**：**step1 补充**（`symbols`）已为轻量扫描，一般很快。bundle 若慢，多在 **step4/step5/step5′**（多关键字、多表、多 entrypoint 起点 → 大量 LSP 往返；**同次 bundle 内通常只启一次 JDTLS**）。**step7** 单独执行 `**callchain-up` / `callchain-down`** 仍为每次各启 JVM。单文件 `**analyze … documentSymbol**` 仍可能极慢，见 `**JDTLS_LSP_DOCUMENT_SYMBOL_TIMEOUT**`。若 **LSP 管道僵死**（旧版 `jrpc` 写锁）：升级已修复版本。

**库 API**：`jdtls_lsp.reverse_design.run_design_bundle`（**step8 编排**）、`scan_modules`（`reverse_design.scan_modules`，**step1**）、`jdtls_lsp.entry_scan.scan_rest_map`（**step2**，亦可从 `jdtls_lsp.reverse_design` 再导出）、`build_table_manifest`（**step3**）、`batch_symbols_by_package`（`reverse_design.batch_symbols_by_package`，**step1 补充**）、`run_table_callchain_up` / `resolve_service_anchor_for_table`（**step5**，对应 `--table-callchain-up`）、`run_entrypoint_callchain_down`（**step4**，对应 `--entrypoint-callchain-down`）；**step6** 亦可 `jdtls_lsp.business_summary`（与 `reverse_design` 平级，对应 `--business-summary`）内 `merge_key_methods_from_downchain_files`、`format_business_md`、`annotate_downchain_business`，以及 `jdtls_lsp.java_javadoc.extract_javadoc_above_method`（`business.md` 与链顶入口共用）。静态入口另见 `jdtls_lsp.entry_scan.scan_java_entrypoints`（或子模块 `entry_scan.java_entrypoints`）。

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

### `trace_outgoing_subgraph_sync`

```python
from pathlib import Path
from jdtls_lsp.callchain import trace_outgoing_subgraph_sync

text = trace_outgoing_subgraph_sync(
    "/path/to/project",
    "com.example.Foo",
    "bar",
    jdtls_path=Path("/path/to/jdtls"),
    max_depth=8,
    max_nodes=500,
    max_branches=32,
    output_format="json",
)
print(text)
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

调用链包装在 `**jdtls_lsp.callchain**` 包：`trace`（`trace_call_chain_sync` / `trace_outgoing_subgraph_sync`）与 `format`（`format_callchain_markdown`、`format_downchain_markdown`、`extract_trace_payload_dict`、`summarize_trace_*_json`）。原子能力为 `**analyze**`（LSP）与 `**java_grep**` 等。**逆向 design bundle**（step4–5）默认落盘 **Markdown**，文末 **「原始 JSON」** 代码块内为完整 payload。

### `java_grep_report`

```python
from pathlib import Path
from jdtls_lsp.java_grep import java_grep_report

payload = java_grep_report(Path("/path/to/project"), "saveMonitorData|foo", sort_by_score=True, max_hits=200)
# payload["hits"] -> [{"file", "line", "text", "score"}, ...]
```

### `run_design_bundle`

```python
from pathlib import Path
from jdtls_lsp.reverse_design.bundle import run_design_bundle

summ = run_design_bundle(
    "/path/to/project",
    Path("./design-out"),
    queries=["saveMonitorData"],
    skip_symbols=False,
    skip_callchain=False,
    skip_scan=False,
    skip_rest_map=False,
    skip_table_manifest=False,
    tables_file=Path("./tables.txt"),  # 可选；与 CLI --tables-file 一致
    tables_inline="",
    strict_tables_only=False,
    table_callchain_up=False,  # True 等价 CLI --table-callchain-up；按蛇形表跑 callchain-up（需 skip_callchain=False）
    table_callchain_up_extra=False,  # True 等价 CLI --table-callchain-up-extra（须与 table_callchain_up 同开）：JDBC 字符串 + MyBatis Mapper 额外锚点
    max_table_up_extra_anchors=24,  # 与 extra 联用：SQL 与 MyBatis 各自条数上限（0=不限制）
    max_table_callchain_java_scan=12_000,
    entrypoint_callchain_down=False,  # True 等价 CLI --entrypoint-callchain-down；按 scan_java_entrypoints 跑 callchain-down（bundle 内与其它 callchain 步共用 JDTLS）
    max_rest_down_endpoints=0,  # >0 时只处理前 N 个 entrypoint 起点
    rest_down_max_depth=16,
    rest_down_max_nodes=500,
    rest_down_max_branches=48,
    business_summary=False,  # True：写 business.md（合并 callchain-down-rest-* / callchain-down-entrypoints-* 子目录内及 data 根下扁平报告）
    jdtls_path=None,
    glob_pattern="**/src/main/java/**/*.java",
    max_symbol_files=200,
    max_rest_map_files=8_000,
    callchain_max_depth=20,
    max_table_java_files=8_000,
    max_table_xml_files=2_000,
)
# summ["artifacts"] -> ["index.md", "data/modules.json", ...]
# summ.get("tableCallchainUp") 在 table_callchain_up=True 时含 resolvedCount 等
# summ.get("entrypointCallchainDown") 在 entrypoint_callchain_down=True 时含 resolvedCount 等
# summ.get("businessSummary") 在 business_summary=True 时含 mergedCount、downchainReportFilesRead 等
```

### 日志（库内）

```python
from jdtls_lsp.logutil import setup_logging

setup_logging("INFO")  # 或 "DEBUG"，或环境变量 JDTLS_LSP_LOG
```

### Agent skills（仓库内）

本仓库 `**skills/**` 下 Cursor/LiteClaw 技能说明应与上文 CLI、子命令、`jdtls_lsp.callchain` / `business_summary` 等 **保持一致**；若行为以代码为准，发现漂移时请以 **本文 + `--help`** 为准并更新对应 `skills/*/SKILL.md`。

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

### `callchain-up` 出现 `jdtls_error`

多为 **JDTLS 对某次 `incomingCalls` 的内部 bug**（日志里常见 NPE）。当前实现已做锚点重试；若仍失败，可换入口（`--class`/`--file`）或缩小 `--query` 范围。

### `reverse-design bundle` / LSP 子命令卡住后按 Ctrl+C 的堆栈（排查参考）

典型现象：卡在 `**callchain-up`**、`**analyze**` 等仍使用 JDTLS 的路径；第一次 **Ctrl+C** 常在 `**jrpc.py` → `queue.Queue.get`**（等某次 LSP 响应），`finally` 里 `**client.shutdown()**` 若仍僵死，`**shutdown**` 也会在 `**q.get**` 上等待，可能需 **第二次 Ctrl+C**。


| 栈位置                                                         | 说明            |
| ----------------------------------------------------------- | ------------- |
| `callchain/trace.py` / `analyze.py` → `client.request(...)` | 等当前 LSP 请求结果  |
| `jrpc.py` → `q.get(timeout=...)`                            | 等 JSON-RPC 响应 |
| `client.shutdown` → `send_request("shutdown")`              | 优雅关闭 JVM      |


**处理**：升级已修复 `**jrpc` 写锁** 的版本；`**symbols`（step1 补充）无 JDTLS**，若仅导出 `symbols-by-package.json` 可单独跑 `**reverse-design symbols`** 验证。**JVM shutdown** 时若遇 KeyboardInterrupt 会记日志并 **terminate/kill** JVM。

---

## 离线包与 `setup.sh` 细节

`setup.sh` 会检查 Python/Java、尝试离线安装 OpenJDK、从 `offline-packages/` 解压 JDTLS 等；无离线包时输出中文指引。若解压后目录结构异常，请把正确 JDTLS 根目录放到 `jdtls-lsp-py/jdtls` 后重试。