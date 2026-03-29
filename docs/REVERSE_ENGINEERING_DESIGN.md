# Java 逆向工程：以调用链为核心的简化设计

**目标**：从代码反推**可交付的设计视图**，但**不以「全量结构扫描」为主线**，而以 **调用链** 为枢纽：从 **REST 接口** 与 **数据库表（持久化边界）** 两类锚点出发，**向上 / 向下** 展开调用关系，再在链上标出 **关键业务逻辑方法**。

**原则**：**Python 本地工具**做确定性图遍历、规则与 JSON/Markdown 产物；**大模型**只做链上方法的语义叙述、缺口与待确认项，不在全仓库自行「找入口」。

### 0. 八步执行流程（与仓库 `需求.md` 对齐）

| step | 目标 | 主要产物 / 命令 |
| --- | --- | --- |
| **step1** | 工程概要 | `reverse-design scan`、`symbols`；`bundle` → `data/modules.json`、`symbols-by-package.json` |
| **step2** | REST 接口清单（**静态入口扫描**，与 `entrypoints` 并列） | `reverse-design rest-map`；`bundle` → `data/rest-map.json`、`graphs/rest-map.mmd` |
| **step3** | 数据库表清单 | `reverse-design db-tables`；`bundle` → `data/tables-manifest.json` |
| **step4** | 每接口向下调用链 | `bundle --rest-callchains-down` → `data/callchain-down-rest/<Controller>/callchain-down-rest-*.md`（文末 JSON）、根目录 `rest-callchains-down-summary.json`（`resolvedByController` / `withErrorsByController`，键同目录） |
| **step5** | 每表向上调用链 | `bundle --table-callchains-up` → `data/callchain-up-table/<物理表>/callchain-up-table-*.md`、根目录 `table-callchains-summary.json`（`resolvedByPhysicalTable` / `withErrorsByPhysicalTable`，键同目录）；`--queries` 为**关键字向上**（补充，非按表） |
| **step6** | 链上关键业务位置 | 向下链 JSON 的 `keyMethods` / `businessCandidate` 等；`bundle --business-summary` → `business.md` |
| **step7** | 关键处补全实现细节 | **工具链外**：`analyze`、`callchain-up`/`down` 单点、IDE；自动化叙述可接 LLM |
| **step8** | 汇总、宏观→微观 | `bundle` 写入 `index.md` 与 stdout 摘要 JSON，与各汇总 JSON、`business.md` 构成总览 |

`bundle` 的**实现顺序**见 `需求.md` 文末说明（与 step 叙述序号不完全同序，语义一致）。

---

## 1. 核心模型（推荐工作方式）

### 1.1 两条主轴线

| 轴线 | 含义 | 典型锚点 |
| --- | --- | --- |
| **外向边界** | 系统对外暴露的行为 | **REST**（`GET/POST …` → Controller 方法） |
| **内向边界** | 系统与持久化/外部 schema 的契约 | **数据库表**：**抽取**（§1.4）+ **用户表清单为准**（§1.5） |

其它入口（消息监听、定时、`@Async`）仍可作为 **补充锚点**，与 `entry_scan.java_entry_patterns` / `callchain-up` 现有 **链顶启发式** 一致；**主线叙述**优先 **REST ↔ 业务 ↔ 持久化**。

### 1.2 调用链方向

- **向上（`callchain-up`）**：从任意方法（或 **关键字** 定位到的方法）沿 **`incomingCalls`** 追到 **链顶**（REST / 消息 / 定时 / `@Async` / abstract / 无上游等，`stopReason` 已有）。
- **向下（`callchain-down`）**：从 REST 锚点或 Service 锚点沿 **`outgoingCalls`** **BFS** 子图，直到 **Repository / Mapper / JDBC 调用** 等 **持久化边界**（或达到 `max-depth` / `max-nodes` 截断）。

**设计意图**：一次「故事线」= **某 HTTP 接口** 从 Controller **向下** 走到 **写哪张表 / 调哪条 SQL 抽象**；或从 **某表相关 Repository** **向上** 反查 **被哪些 API 间接调用**。

### 1.3 在链上识别「关键业务逻辑」

在已得到的 **调用链节点**（类#方法）上，用 **可组合、可回归** 的启发式标权（不要求一次做全自动化流水线）：

- **位置**：链上处于 **Controller 之下、Repository/Mapper 之上** 的 **Service（及域服务）** 层方法优先。
- **信号（示例）**：`@Transactional`；被 **多条上游** 调用（入边多）；**向下** 直接/间接触及 **持久化 API**；方法名/类名含业务域关键词；用户给定 **关键字**（与现有 `callchain-up --query` 一致）。
- **产出形态（目标）**：在 JSON/Markdown 中为节点打 **`businessCandidate: true`** 或单独 **`keyMethods[]`** 列表，并引用 **稳定锚点**（路径、行号、类#方法）。

**说明**：**step6** 已在 **向下子图**（`callchain-down` / `data/callchain-down-rest/<Controller>/callchain-down-rest-*.md` 文末 JSON）上落地：节点带 `businessScore` / `businessCandidate` / `businessSignals`，顶层 `keyMethods`；`reverse-design bundle --business-summary` 合并为根目录 **`business.md`**（扫描递归匹配，兼容历史上 `data/` 根下扁平 `callchain-down-rest-*.md`）。**向上链**（`callchain-up`）的同类标权仍为可选后续。

### 1.4 数据库表识别（须兼容历史遗留工程）

持久化边界不能只依赖 **JPA / MyBatis 声明式** 锚点；须把 **JDBC 直连 + SQL 以 Java 字符串拼接** 的工程视为 **一等公民**。

| 来源 | 说明 |
| --- | --- |
| **声明式（易）** | `@Table` / `@Entity`、JPA `Repository`、MyBatis **XML/注解** 中的表名；与调用链结合可反查方法。 |
| **JDBC 字面量 SQL（中）** | `prepareStatement("SELECT … FROM user …")`、`executeQuery("…")` 等 **字符串字面量**；可对 **单文件内** 字面量做 **FROM / JOIN / INTO / UPDATE / DELETE FROM** 等子句的 **启发式表名抽取**（正则 + 标识符规则，排除关键字）。 |
| **拼接 / `StringBuilder`（难、须覆盖）** | `"SELECT * FROM " + TABLE_NAME`、`sql.append(" FROM ")`、`MessageFormat` 等：表名可能 **部分在常量、部分在变量**。兼容策略应是 **分层**：① 先做 **常量折叠近似**（同一方法内相邻字符串字面量拼接成候选 SQL 片段）；② 再对 **折叠后的片段** 跑与上相同的 **SQL 子句解析**；③ 对 **纯变量** 表名输出 **`tableUnknown: true`** 或 **引用配置键 / 常量名**，不假装精确。 |
| **外部 SQL 文件** | `.sql`、资源路径加载：可作为 **可选** 第二数据源（路径与 Java 调用点关联）。 |

**原则**：产物中区分 **`tableResolved`**（高置信字面量/声明）与 **`tableHeuristic`** / **`tableUnknown`**（拼接或动态）；**调用链 + 方法锚点** 始终保留，便于人工与模型补全。

**局限**：完全不运行的 **静态分析** 无法保证与运行时 SQL 一致；**存储过程 / 动态 schema** 需单独标注。

### 1.5 表抽取产物与用户表清单（以清单为准）

工具应 **支持从代码中抽取表名**（汇总为 **`extractedTables[]`** 或等价结构：表名、置信度、来源文件/行、关联 SQL 片段类型等），并 **支持用户提供的表清单作为权威输入**。

| 能力 | 说明 |
| --- | --- |
| **抽取（`extract`）** | 综合 §1.4 各来源，输出 **机器可读** 的表引用列表；每条带 **`source`**（entity / mybatis / jdbc_literal / jdbc_concat_heuristic …）与 **`confidence`**。 |
| **用户清单（`tables manifest`，权威）** | 用户给定 **固定集合**（如 `tables.txt` 每行一表、`tables.yaml` 或 JSON：`schema`、`logicalName`、`physicalName` 可选）。**以该清单为准** 指：① **范围**：分析、报告与调用链锚点 **默认只关心清单内表**（可配置是否附带清单外抽取结果作「待认领」）；② **命名**：清单中的表名为 **规范名**（canonical），代码里别名/大小写差异 **归并到清单条目**；③ **缺口**：清单中有、抽取中 **无命中** 的表单独列出 **`unresolvedTables[]`**，提示补 grep / 人工或动态 SQL。 |
| **合并策略** | **清单 ∪ 抽取** 展示时：**清单优先**（排序、章节标题、REST↔表映射以清单为索引）；抽取得到但 **不在清单** 的表可作为 **`extractedOnly[]`**（低优先级或 `--strict-tables` 时隐藏）。 |
| **驱动调用链** | 对清单中每张表：在仓库内 **按表名/规范名做受控搜索**（字符串、注解、XML）→ 得到 **方法锚点** → **`callchain-up`** 反查 API；与 **REST → callchain-down** 路径互补。 |

**CLI / bundle 形态**：**`jdtls-lsp reverse-design db-tables`** 与 **`reverse-design bundle`** 支持 **`--tables-file`**、**`--tables`**、**`--strict-tables-only`**；bundle 另支持 **`--skip-table-manifest`**、**`--table-callchains-up`**（按表 `callchain-up`）。产物 **`data/tables-manifest.json`**（清单 + 抽取 + 未解析 + 锚点行）；**拼接 SQL** 仍待增强。

---

## 2. 与现有工具的一览对照

| 角色 | 实现 | 说明 |
| --- | --- | --- |
| **静态入口扫描（无 JDTLS）** | `entrypoints`、`reverse-design rest-map` / `entry_scan` | **非 HTTP**：`java_entry_patterns` + `line_patterns.scan_java_entrypoints`；**HTTP**：`rest_http.scan_rest_map` → `rest-map.json` |
| **向上调用链** | `callchain-up` / `trace_call_chain_sync` | 关键字 / 类#方法 / 文件行入口；**需 JDTLS** |
| **向下子图** | `callchain-down` / `trace_outgoing_subgraph_sync` | **需 JDTLS**；深度/分支上限 |
| **报告** | `callchain/format.py` | 向上/向下 Markdown；`stopReason` 等 |
| **链顶规则（与入口同源）** | `entry_scan/java_entry_patterns.py` | 供 `entrypoints` 与 callchain **stopReason** 等复用 |
| **模块与符号背景** | `reverse-design scan` / `reverse-design symbols` / `reverse-design bundle` | **step1**（模块扫描）+ **step1 补充**（**轻量**顶层类型索引，**非**调用链主线）；bundle 同时覆盖 **step2–3** |
| **单次 LSP** | `analyze` | `definition`、`references`、`documentSymbol`（大文件可能慢）等 |

**数据库锚点（现状）**：**已实现** **`jdtls-lsp reverse-design db-tables`** 与 **`reverse-design bundle`** 产出 **`data/tables-manifest.json`**（`build_table_manifest`）：**`--tables-file` / `--tables`** 为规范名；**`unresolvedTables` / `extractedOnly` / `anchorsByTable`** 见 schema。抽取覆盖 **§1.4** 中 **声明式 `@Table`、JDBC 字符串字面量内 SQL 片段、MyBatis `table=` 与 XML 字面量**；**字符串拼接 SQL** 仍为后续增强（见 §1.4）。

---

## 3. 推荐编排（给人与智能体）

**若有数据字典 / 已知表集合**：先准备 **`tables` 清单**（§1.5），再跑抽取与调用链，避免在海量无关字符串里捞表名。

1. **`jdtls-lsp reverse-design bundle …`**（或单独 **`reverse-design rest-map`**）得到 **`rest-map.json`**，选 **关心的 API** → 得到 **类#方法** 与文件行。
2. **`callchain-down`**：从该 Controller 方法（或下一层 Service 方法）向下，观察子图是否进入 **Repository/Mapper** 或 **含 JDBC/SQL 的方法**（持久化边界，含遗留拼接 SQL，见 §1.4）。
3. **`callchain-up`**：从 **关键字**、**Repository 方法** 或 **清单内某表对应锚点方法** 向上，看 **链顶** 是否为 **REST**（或其它 `stopReason`）。
4. **表维度**：对清单中每张表，用 **抽取/搜索** 得到锚点 → **`callchain-up`**；将 **抽取结果与清单 diff**（**`unresolvedTables`** / **`extractedOnly`**，§1.5）。
5. 在导出 Markdown/JSON 上 **人工** 或 **后续自动化** 标出 **关键业务方法**（§1.3）。

**bundle** 编排：**step5′** 用 **`--queries`** 预跑 **`callchain-up` JSON**；**step5** 用 **`--table-callchains-up`**；**step4** 用 **`--rest-callchains-down`**（与同次 bundle 内其它 callchain **共用一次 JDTLS**，可用 **`--max-rest-down-endpoints`** 限流）；**step6** 用 **`--business-summary`** → **`business.md`**。**step7** 仍以单点 **`analyze` / callchain** 为主。**不以** `symbols-by-package` **轻量符号表** 为主线索。

---

## 4. 与 `需求.md` 八步的对应（主线为 step 编号）

文档与 CLI **只使用** **`需求.md` / 上文 §0** 的 **step1–step8** 编号；**不再使用**「阶段 A/B/C/D」或 **A1 / A2.5** 等字母标签。

| 能力块 | 涵盖 | 备注 |
| --- | --- | --- |
| **扫描与汇总** | **step1–3**、**step8**（`reverse-design` / `bundle`） | 模块、REST 映射、表 manifest、轻量 symbols、`index.md` |
| **调用链与入口** | **step4 / step5 / step5′**、**step7** 所用 `callchain-up`/`down`、**静态入口**（`entrypoints` + `rest-map`）、`entry_scan.java_entry_patterns` | 与 JDTLS 配合 |
| **关键业务摘要** | **step6** | `keyMethods`、`business.md` |
| **其它** | 依赖树、配置草图等 | 可选后续 |

---

## 5. 架构示意（调用链为中心）

```
                    ┌─────────────────────┐
                    │ rest-map 静态入口（step2） │  REST 锚点：Path → 类#方法
                    └──────────┬──────────┘
                               │
       callchain-up ◄──────────┼──────────► callchain-down
    （incomingCalls）          │          （outgoingCalls BFS）
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
     消息 / 定时 / @Async …              Service / Domain
     （链顶 stopReason）                 （关键业务候选 §1.3）
                                              │
                                              ▼
              Repository / Mapper / @Table / JDBC·SQL …
              （表锚点：抽取 §1.4；范围/规范名以用户清单 §1.5 为准）
```

---

## 6. 风险与约束

- **JDTLS**：调用链与多数 `analyze` 操作依赖 JDTLS；**step1 补充**（`symbols`）**轻量扫描不调用** `documentSymbol`。
- **REST 表**：**step2**（`rest-map`）为**正则启发式**，与运行时路由可能不完全一致。
- **DB**：除 JPA/MyBatis 外，须预期 **JDBC + 字符串拼接 SQL**（§1.4）；**用户表清单**（§1.5）可缩小误报，但 **清单有而代码无静态命中** 不代表表未使用（动态 SQL）。**以清单为准** 是产品与报告边界，不是运行时真理。
- **合规**：仅用于自有或授权源码。

---

## 7. 与仓库文件的衔接

| 资产 | 用途 |
| --- | --- |
| `trace_call_chain_sync` / `trace_outgoing_subgraph_sync` | 上/下调用链核心 |
| `callchain/` 包（`trace` + `format`） | 调用链追踪与 Markdown 展示 |
| `entry_scan/java_entry_patterns.py` | 链顶与入口规则 |
| `entry_scan/rest_http.py`（`scan_rest_map`） | REST 静态入口 / `rest-map` 锚点 |
| `reverse_design/scan_modules.py` | **scan_modules**：Maven/Gradle 模块概要（**step1**） |
| `reverse_design/scan_java_top_level_types.py` | **scan_java_top_level_types**：单文件顶层类型轻量解析（**step1 补充** 子步骤） |
| `reverse_design/batch_symbols_by_package.py` | **batch_symbols_by_package**：按包聚合轻量符号（**step1 补充**） |
| `jdtls_lsp/business_summary/`（与 `reverse_design/` 平级） | **step6**、CLI `--business-summary`：`annotate_downchain_business`、`extract_javadoc_above_method`、`merge_key_methods_from_downchain_files`、`format_business_md`（`business.md` 附方法 Javadoc） |
| `reverse_design/bundle.py` | 一键产出 `design/` + 可选 `callchain-up-*` / `callchain-up-table/<表>/…` / `callchain-down-rest/<Controller>/…` Markdown（文末 JSON） |
| `reverse_design/rest_callchains_down.py` | 从 `rest-map` 批量 `callchain-down`（CLI `--rest-callchains-down`） |
| `reverse_design/table_callchains_up.py` | 按表 `callchain-up`（CLI `--table-callchains-up`） |

---

## 8. 建议的下一步（与 §1 对齐）

1. **文档与 skill**：智能体说明改为 **「先 rest-map → 再 callchain-down/up」**；可选新增 **`java-reverse-design`** skill（仅描述编排，不展开全表 analyze）。
2. **产品化 DB 锚点**：实现 **表抽取**（§1.4）+ **用户清单输入**（§1.5：`--tables-file` 等）；输出 **`tables-manifest.json`**：`canonicalTables`（以清单为准）、`extractedHits`、`unresolvedTables`、`extractedOnly`；支持 **`strict` / `loose`** 是否展示清单外抽取。
3. ~~**step6 业务摘要**~~：**callchain-down** 已写 **`keyMethods`** 与节点 **`businessCandidate`**；bundle **`--business-summary`** → **`business.md`**。可选：对 **callchain-up** 对称标权、链上代码摘录块。
4. **测试**：小 Spring 样例工程：单 REST → Service → Repository → 表名 **可回归** 路径。

---

*本文为设计方案，非排期承诺；实现状态随仓库更新（`reverse-design` **step1–8** 编排、`callchain-up`/`down`、`entry_scan` 等）。*
