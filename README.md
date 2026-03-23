# liteclaw-lsp（Python）

与 [LiteClaw](../liteclaw) 中 `src/lsp` 行为对齐的 **独立可运行** Python 包装：启动 JDTLS、通过 LSP（stdio JSON-RPC）调用与 `lsp_java_analyze` 相同的操作。

## 依赖

- **Python 3.10+**
- **Java 21+**（`java` 在 `PATH` 或 `JAVA_HOME`）
- **JDTLS**：默认使用 `~/.liteclaw/jdtls`（与 LiteClaw 相同布局：含 `config_mac|config_linux|config_win` 与 `plugins/org.eclipse.equinox.launcher_*.jar`）。若未安装，请先在本机用 LiteClaw 执行 `liteclaw lsp install-jdtls`，或自行解压官方包到该目录。

## 安装（开发模式）

```bash
cd liteclaw-lsp-py
pip install -e .
```

不安装也可直接运行（将 `src` 加入 `PYTHONPATH`）：

```bash
cd liteclaw-lsp-py
PYTHONPATH=src python3 -m liteclaw_lsp analyze --help
```

## 命令行

```bash
# 文档符号（相对 project 的 .java 路径）
liteclaw-lsp analyze /path/to/maven-or-gradle-project documentSymbol \
  --file src/main/java/com/example/App.java

# 工作区符号（query 建议完整类名或包名前缀）
liteclaw-lsp analyze /path/to/project workspaceSymbol --query com.example.MyService

# 定义 / 引用 / hover（行、列为 1-based，与 LiteClaw 工具一致）
liteclaw-lsp analyze /path/to/project references \
  --file src/main/java/com/example/App.java --line 10 --column 1

# 环境变量
export LITECLAW_JDTLS_PATH=/path/to/jdtls   # 可选，默认 ~/.liteclaw/jdtls
```

子命令 `analyze` 的 `operation` 取值：`documentSymbol` | `workspaceSymbol` | `definition` | `references` | `hover` | `implementation` | `incomingCalls` | `outgoingCalls`。

**输出**：默认输出完整 JSON，**不截断**字符长度（与 LiteClaw `lsp_java_analyze` 当前行为一致）。

## 作为库

```python
from liteclaw_lsp.analyze import analyze_sync

out = analyze_sync(
    "/path/to/project",
    "references",
    file_path="src/main/java/App.java",
    line=1,
    character=1,
)
print(out)
```

## 与 LiteClaw 的关系

- 不依赖 Node / LiteClaw 运行时；仅复用同一套 JDTLS 安装路径与 LSP 请求语义。
- 项目根目录通过向上查找 `pom.xml` / `build.gradle` 等标记解析，与 `liteclaw/src/lsp/jdtls.ts` 的 `findProjectRoot` 一致。
