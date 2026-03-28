# jdtls-lsp-py 测试方案

本文档供对 **jdtls-lsp**（Python + JDTLS + LSP）做验证与回归时使用。当前仓库**尚无**自动化测试用例，建议按下列层次逐步补齐（pytest / 脚本 / CI）。

---

## 1. 测试目标与范围

| 目标 | 说明 |
|------|------|
| **正确性** | `analyze` 各 operation 与 LiteClaw `lsp_java_analyze` 语义一致；`callchain-up` 入口解析、向上追踪、终止原因与输出 schema 符合 README |
| **健壮性** | 非法参数、无 JDTLS、非 Java 项目、路径不存在、JDTLS 启动失败等有明确退出码与 stderr 提示 |
| **可重复** | 固定 **fixture 项目**（建议小型 Maven/Gradle 多模块样例 + 含 Controller/Service/接口 的典型 Spring 结构） |

**范围外（除非单独立项）**：JDTLS/Eclipse 自身 bug、极端巨型单文件性能、非 LTS JDK 全矩阵。

---

## 2. 环境与前置条件

### 2.1 必备

- Python **3.10+**
- Java **21+**（`JAVA_HOME` 或项目内 `openjdk`）
- 可访问的 **JDTLS** 目录（`LITECLAW_JDTLS_PATH` 或包内/本地 `jdtls`，含对应平台 `config_*` 与 launcher jar）

### 2.2 可选

- **ripgrep (`rg`)**：在 `callchain-up` 中关键字解析走 `java_text_grep` 时，与纯 Python 扫描路径应对照各测一轮

### 2.3 基线检查（每条流水线或本地手测前执行）

```bash
python3 -V
java -version
jdtls-lsp --help
jdtls-lsp analyze --help
jdtls-lsp callchain-up --help
```

---

## 3. 测试分层

### 3.1 静态与单元（不启 JDTLS）

| 编号 | 内容 | 说明 |
|------|------|------|
| U1 | **JSON-RPC 编解码** | `jrpc.py`：Content-Length 分帧、非法报文、超时（若可 mock socket） |
| U2 | **项目根解析** | `jdtls.py` / `analyze` 使用的「向上找 pom/build.gradle」：含多模块、仅子目录传入 |
| U3 | **关键字拆分** | `callchain-up`：`query` 中 `\|` / `｜` 拆分、单段 vs 多段行为与 README 一致 |
| U4 | **CLI 参数互斥** | `callchain-up`：类+方法 / 文件+行 / query **三选一**；缺参、多选报错 |
| U5 | **java_grep**（若可单测） | 模式、过滤 `--grep-skip-interface` 等仅影响 grep 路径时的筛选逻辑 |

建议框架：**pytest**，对纯函数与 `Path` 级逻辑做 **mock**，避免拉起 JVM。

### 3.2 集成测试（真实 JDTLS，慢）

**前置**：环境变量或 `pytest` fixture 指向 **fixture 项目根** 与 **JDTLS 根**。

#### A. `analyze`

对每个 `operation` 至少 1 个正向用例（见 README 表格）：

| operation | 最小用例要点 |
|-----------|----------------|
| `documentSymbol` | 任选含 2+ 类/方法的 `.java`，断言返回非空或结构含 `name`/`kind` |
| `workspaceSymbol` | `--query` 单关键字 + **多关键字 `\|`** 合并去重（≤20 条） |
| `definition` / `references` / `hover` / `implementation` | 同一锚点（`--file --line [--char]`），断言与 IDE 行为一致（可人工基准） |
| `incomingCalls` / `outgoingCalls` | 选在已知有调用关系的方法上 |

**负例**：不存在的 `--file`、越界行号、无 `pom`/`gradle` 的目录（应失败或明确提示）。

#### B. `callchain-up`

| 编号 | 场景 | 验证点 |
|------|------|--------|
| C1 | **`--class` + `--method`** | 链非空或符合预期的 `stopReason` |
| C2 | **`--file` + `--line`** | 与 C1 同一逻辑起点时结果应一致（允许链表述差异） |
| C3 | **query：`类名.方法名`** | 不走全文 grep 时的解析 |
| C4 | **query：单段关键字** | workspace → 类首方法 → `java_text_grep` 降级路径（需可控 fixture） |
| C5 | **query：多段 `\|`** | 仅 grep 合并、多起点串行、**无并发 incomingCalls**（日志或单进程可观测） |
| C6 | **grep 过滤** | `--grep-skip-interface` / `--grep-skip-rest-entry` / `--grep-max-entry-points` 组合与 README 一致 |
| C7 | **`--format json` / `markdown`** | stdout 可解析 JSON；markdown 内含嵌入 JSON 块 |
| C8 | **`--max-depth`** | 浅深度时链更短或 `stopReason` 为深度相关 |

#### C. 进程与资源

- 连续运行多条命令：**JDTLS 子进程退出**、无僵尸（可用脚本跑 N 次 `analyze` + 一次 `callchain-up`）。
- **并发**：文档说明多起点 **串行**；自动化可跑两次并行 CLI **预期其一失败或加锁**——以当前实现为准写清预期。

---

## 4. 回归与基准

| 类型 | 做法 |
|------|------|
| **黄金输出** | 对 fixture 项目固定 `analyze`/`callchain-up` 的 **JSON 快照**（路径脱敏）；变更 JDTLS 版本时重审 |
| **手工记录** | 与 IDEA「Navigate / Call Hierarchy」对照 1～2 个锚点，记录在 `docs/` 或 issue |
| **现有材料** | 仓库内 `test.md`（若为用户本地会话记录）**不作为** CI 断言，仅作场景参考 |

---

## 5. CI 建议（GitHub Actions 等）

|  job | 条件 | 步骤 |
|------|------|------|
| **lint** | 无 JDTLS | `ruff`/`flake8` + `mypy`（可选）+ `pytest tests/unit -m "not integration"` |
| **integration** | 缓存 JDTLS + Temurin 21 | 下载固定版本 JDTLS zip 或使用缓存；跑 `pytest -m integration` |
| **矩阵** | 可选 | `ubuntu-latest` 为主；`macos` 若 JDTLS 路径差异大再开 |

**密钥**：集成 job 需 **较长超时**（JDTLS 冷启动 + 索引，建议 ≥10～15 分钟上限或分步缓存 workspace）。

---

## 6. 交付物清单（建议）

1. `tests/conftest.py`：fixture 项目路径、JDTLS 路径、`jdtls-lsp` 可执行入口。  
2. `tests/unit/`：U1–U5 及 CLI parse。  
3. `tests/integration/`：analyze + callchain-up，标记 `@pytest.mark.integration`。  
4. `Makefile` 或 `scripts/run_tests.sh`：`unit` / `integration` 目标。  
5. 本文档随实现更新 **通过标准**（何种差异可接受）。

---

## 7. 风险与注意事项

- **索引时序**：`workspaceSymbol` / callchain-up 首次可能空结果，与 liteclaw 类似可考虑 **重试**（集成测试 sleep 或轮询）。  
- **本机路径**：快照与日志避免写死用户 home。  
- **Windows**：路径分隔符、`setup.bat` 与 `export.bat` 单独加一条 smoke。

---

*版本：与当前 README / CLI 行为对齐；实现自动化后请在本文件「通过标准」小节补充具体命令与覆盖率目标。*
