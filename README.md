# jdtls-lsp（Python）

`jdtls-lsp` 是一个独立可运行的 Python 包，用于启动 JDTLS 并通过 LSP（stdio JSON-RPC）执行 Java 代码分析操作（与 LiteClaw 的 `lsp_java_analyze` 语义保持一致）。

## 依赖

- Python 3.10+
- Java 21+（优先使用安装包所在目录 `jdtls-lsp-py/openjdk/bin/java`，其次当前目录 `./openjdk/bin/java`，否则使用 `PATH`/`JAVA_HOME` 中的 `java`）
- JDTLS 目录（需包含 `config_mac|config_linux|config_win` 和 `plugins/org.eclipse.equinox.launcher_*.jar`）

JDTLS 默认查找优先级：

1. `LITECLAW_JDTLS_PATH`
2. 安装包所在项目目录 `jdtls-lsp-py/jdtls`
3. 当前目录 `./jdtls`
4. `~/jdtls`（兜底）

## 推荐初始化（含离线包）

项目内置 `setup.sh`，会自动：

- 检查 Python / Java 版本
- 优先离线安装 OpenJDK（如有匹配当前系统的压缩包）
- 如果 `offline-packages/` 有 JDTLS 压缩包（`*.tar.gz|*.tgz|*.zip`），自动解压到 `./jdtls`
- 优先尝试全局执行 `pip install -e .`，失败时自动回退到项目 `.venv`
- 如果没有离线包，输出中文安装指引

```bash
cd jdtls-lsp-py
./setup.sh
# 若全局安装成功可直接使用：
jdtls-lsp analyze --help
# 若回退到 .venv，可使用：
./.venv/bin/jdtls-lsp analyze --help
```

### Windows

- 在项目目录执行：`setup.bat`（与 `setup.sh` 流程一致，由 `scripts/setup_win.py` 实现）。
- 离线 OpenJDK 包文件名需包含 `windows` 或 `win`（例如 `openjdk-26_windows-x64_bin.zip`）。
- 全局安装失败时，虚拟环境入口为：`.venv\Scripts\jdtls-lsp.exe`。
- 生成 portable zip：双击或执行 `export.bat`（与 `export.sh` 相同规则，均调用 `scripts/export_portable.py`）。
- 若在 Git Bash / MSYS 下使用，也可执行 `./setup.sh`（已兼容 `.venv\Scripts\python.exe`）。

## 手动安装

```bash
cd jdtls-lsp-py
pip install -e .
```

## 不安装直接运行

```bash
cd jdtls-lsp-py
PYTHONPATH=src python3 -m jdtls_lsp analyze --help
```

也可以不使用 `-m`：

```bash
PYTHONPATH=src python3 src/jdtls_lsp/__main__.py analyze --help
```

## 命令行示例

```bash
# 文档符号（file 为相对 project 的 .java 路径）
jdtls-lsp analyze /path/to/project documentSymbol \
  --file src/main/java/com/example/App.java

# 工作区符号
jdtls-lsp analyze /path/to/project workspaceSymbol --query com.example.MyService

# 引用（line/char 为 1-based）
jdtls-lsp analyze /path/to/project references \
  --file src/main/java/com/example/App.java --line 10 --char 1

# 调用链（给定类名+方法名，向上追到 REST 或 abstract 类）
jdtls-lsp callchain /path/to/project \
  --class com.example.service.OrderService \
  --method createOrder \
  --max-depth 20

# 文件 + 行号（1-based，在该位置解析方法后追踪）
jdtls-lsp callchain /path/to/project \
  --file src/main/java/com/example/Foo.java --line 42

# 关键字：仅方法名（依赖 workspace/symbol），或「类名.方法名」直接解析
jdtls-lsp callchain /path/to/project --query createOrder
jdtls-lsp callchain /path/to/project --query OrderServiceImpl.createOrder
```

`operation` 支持：

- `documentSymbol`
- `workspaceSymbol`
- `definition`
- `references`
- `hover`
- `implementation`
- `incomingCalls`
- `outgoingCalls`

输出为完整 JSON（不做长度截断）。

## 调用链追踪（callchain）

`callchain` 子命令用于从某一方法出发**向上**追踪调用链，直到 REST 接口、abstract 类、无上游、环或达到 `max_depth`。

**入口三选一**（互斥）：

| 方式 | 参数 | 说明 |
|------|------|------|
| 类 + 方法 | `--class` 与 `--method` | 与原先相同；类名可为全限定或简单类名 |
| 文件 + 位置 | `--file` 与 `--line`（可选 `--char`，默认 1） | 相对项目根或绝对路径的 `.java`；在该行解析所在方法后进入 call hierarchy |
| 关键字 | `--query` | `workspace/symbol` 搜索（优先方法符号）；搜索词为**原样**（不做 `monitor_data`→`MonitorData` 等转换）；若只匹配到类/接口，则优先从 `*ServiceImpl` / `*Controller` 等类型中取「第一个方法」作为起点；若写成 `类名.方法名`，则直接按类+方法解析；若回退到 ***.java 全文搜索**且命中**多个不同文件**，则对每个文件各起一个调用链起点，并用线程池**并行**追踪（每文件独立 JDTLS 进程） |

其他参数：

- `project`：项目根目录或任意路径（会自动向上找 Maven/Gradle 根）
- `--max-depth`：最大追踪深度（默认 20）
- `--grep-workers`：仅关键字多文件 grep 时有效，并行 worker 数（默认 `min(8, 文件数)`；也可用环境变量 `JDTLS_LSP_GREP_WORKERS`）
- `--format`：`markdown`（默认，含 ASCII 流程说明 + 每条链图示 + 末尾嵌入完整 JSON）或 `json`（仅 JSON）

输出：

- **Markdown**（默认）：含 `## 流程概览` 与每条链的 ASCII 图示，并在 `## 原始 JSON` 中附上完整 JSON（fenced `json`）
- **JSON**（`--format json`）：与嵌入块中的结构一致
- `chainCount`：链路数量（存在分支时会输出多条）
- `chains[].chain`：从目标方法到上层调用方的完整路径
- `chains[].stopReason`：停止原因（`rest_endpoint` / `abstract_class` / `no_incoming` / `cycle` / `max_depth`）
- `chains[].topEntry`（可选）：当 `stopReason` 为 `no_incoming` 或 `rest_endpoint` 时，对**最上层**节点解析 Spring **类级 `@RequestMapping` + 方法级映射**，得到 `httpMethod`、`restPath`、`restSummary`、`classBasePath`，并提取方法上方 **JavaDoc**（`javadoc`）；非 Controller 或无法解析时字段可能缺失

## 作为库调用

```python
from jdtls_lsp.analyze import analyze_sync

out = analyze_sync(
    "/path/to/project",
    "references",
    file_path="src/main/java/App.java",
    line=1,
    character=1,
)
print(out)
```

## 常见报错排查

### 1) `需要 Python 3.10+` / `未找到 Python`

- 先确认版本：

```bash
python3 --version
```

- 如果系统有多个 Python，优先用 `python3`：

```bash
cd jdtls-lsp-py
python3 -m pip install -e .
PYTHONPATH=src python3 -m jdtls_lsp analyze --help
```

### 2) `需要 Java 21+` / `未找到 Java`

- 检查 Java 版本：

```bash
java -version
```

- 如果 `java` 不在 PATH，请设置 `JAVA_HOME` 并补 PATH（按你的系统方式配置）。

### 3) `JDTLS not found under ...`

表示没有在默认位置找到 JDTLS，按以下顺序检查：

1. 是否设置了 `LITECLAW_JDTLS_PATH`
2. 安装包所在项目目录下是否存在 `jdtls-lsp-py/jdtls`
3. 当前目录下是否存在 `./jdtls`
4. `~/jdtls` 是否存在

目录必须至少包含：

- `config_mac` / `config_linux` / `config_win`（与你系统对应）
- `plugins/org.eclipse.equinox.launcher_*.jar`

可直接使用：

```bash
cd jdtls-lsp-py
./setup.sh
```

### 4) 离线包已解压，但提示目录结构不识别

脚本会输出临时解压目录。请手动检查后，把正确的 JDTLS 根目录移动到：

```bash
jdtls-lsp-py/jdtls
```

然后重试命令。

### 5) `workspaceSymbol` 无结果

这是 JDTLS 刚启动时常见现象。当前实现已内置一次延迟重试（约 8 秒）。

建议：

- 使用更完整的类名或包名前缀（例如 `com.example.MyService`）
- 确认项目根目录正确（包含 `pom.xml` / `build.gradle`）
- 先执行一次 `documentSymbol` 验证文件可被正常打开与解析

### 6) 直接运行报 `ModuleNotFoundError: No module named 'jdtls_lsp'`

未安装时需要设置 `PYTHONPATH=src`：

```bash
cd jdtls-lsp-py
PYTHONPATH=src python3 -m jdtls_lsp analyze --help
```

或直接安装：

```bash
cd jdtls-lsp-py
python3 -m pip install -e .
```
