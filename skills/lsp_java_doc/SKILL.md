# lsp_java_doc

```yaml
name: lsp_java_doc
description: >
  jdtls-lsp analyze 各 operation 参数、用途与调用顺序速查；配合 lsp_plan
  做 Java 项目结构/调用链分析。底层基于 JDTLS LSP。
```

# jdtls-lsp analyze 参考

> 说明：本 skill 是文档说明；实际调用入口为 `jdtls-lsp analyze`（或 Python API `analyze_sync`）。

**公共参数**：`projectPath`（项目根目录，必填）。

| operation | 必填参数 | 说明与用途 |
| --- | --- | --- |
| **documentSymbol** | projectPath, filePath | 获取单个 `.java` 文件符号树（类/方法/字段/签名），用于按文件理解结构。 |
| **workspaceSymbol** | projectPath, query | 工作区符号搜索。建议 query 使用完整包名或类名（如 `com.example.service.UserService`）。 |
| **definition** | projectPath, filePath, line, character | 解析指定位置符号的定义位置（跳转定义）。 |
| **references** | projectPath, filePath, line, character | 查找符号引用（谁在使用它）。 |
| **hover** | projectPath, filePath, line, character | 获取类型/注释等悬停信息。 |
| **implementation** | projectPath, filePath, line, character | 查找接口/抽象方法的实现。 |
| **incomingCalls** | projectPath, filePath, line, character | 查找入站调用（谁调用我）。 |
| **outgoingCalls** | projectPath, filePath, line, character | 查找出站调用（我调用谁）。 |

## 行列参数说明

- `line` / `character` 输入为 **1-based**（命令行层）。
- 内部会转换为 LSP 需要的 0-based，无需手动换算。

## 推荐调用顺序

1. 先 `documentSymbol` 获取目标符号位置。
2. 再做 `definition/references/implementation` 定位关系。
3. 需要调用链时，补 `incomingCalls/outgoingCalls`。
4. `workspaceSymbol` 用于跨文件快速定位候选类或方法。

## 命令示例

```bash
# 先看文件结构
jdtls-lsp analyze /path/to/project documentSymbol \
  --file src/main/java/com/example/App.java

# 查找引用
jdtls-lsp analyze /path/to/project references \
  --file src/main/java/com/example/App.java --line 10 --char 1

# 工作区符号搜索
jdtls-lsp analyze /path/to/project workspaceSymbol \
  --query com.example.UserService
```

> 若全局安装失败并回退到虚拟环境，请把命令前缀改为 `./.venv/bin/`。

## Python API（可选）

```python
from jdtls_lsp.analyze import analyze_sync

out = analyze_sync(
    "/path/to/project",
    "references",
    file_path="src/main/java/com/example/App.java",
    line=10,
    character=1,
)
print(out)
```

## 前提

- Java 21+
- JDTLS 目录可用（优先 `./jdtls`，其次 `LITECLAW_JDTLS_PATH`，最后 `~/jdtls`）
- 推荐先执行 `./setup.sh`（macOS/Linux）或 `setup.bat`（Windows）完成离线 OpenJDK/JDTLS 初始化与 Python 安装（全局优先，失败回退 `.venv`）
