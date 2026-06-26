# Retry 优化与 eval_feedback 增强修复报告

> 修复日期：2026-06-26
> 问题来源：代码审查发现

---

## 修复一：`_compute_diff` 函数（死参数 / 全量输出）

### 问题描述

`GeneratorAgent._compute_diff()` 接收 `codebase_path` 参数但从未使用，将 sprint workspace 中**所有文件的完整内容**以 diff 格式输出，而非仅输出与原始 codebase 的差异。

| 受影响组件 | 影响 |
|-----------|------|
| `GeneratorAgent` | 返回的 `code_diff` 包含全量文件内容，膨胀 state |
| `EvaluatorAgent` | 接收未过滤的 diff，评估时需处理大量无关内容 |
| 工作流整体 | token 消耗与仓库大小挂钩，而非改动量 |

### 修复方案

| 文件 | 改动 |
|------|------|
| `src/agents/generator.py` 第 3 行 | 新增 `import difflib` |
| `src/agents/generator.py` 第 186–230 行 | 重写 `_compute_diff` 方法体 |

修复后逻辑：遍历 sprint workspace 文件 → 与 codebase 同路径文件对比 → 相同跳过，不同用 `difflib.unified_diff` 输出，不存在则标记为 new file。无变化时返回 `"(no changes)"`。

### 修复效果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| `code_diff` 大小 | 与仓库文件总量成正比 | 与 Generator 实际改动量成正比 |
| 未修改文件 | 全量输出 | 完全跳过 |
| diff 格式 | 全部标为 `--- /dev/null`（new file） | modified 用 `--- a/` / `+++ b/`，new 用 `--- /dev/null` |
| `codebase_path` 参数 | 死参数 | 用于定位原始文件进行对比 |

---

## 修复二：SprintWorkspace retry 行为（clean slate → 保留上次代码）

### 问题描述

`SprintWorkspace.create()` 每次调用都执行 `rmtree` + `copytree`，从原始 codebase 重新 seed。retry 之间 `current_sprint` 不变，workspace 路径名相同，导致 Generator 上一次写的代码被完全冲掉。

当某次 retry 只有 1 条 criterion FAIL、其余 9 条已 PASS 时，Generator 要把那 9 条的工作全部重做——浪费 token，且重做时可能引入新的 regression。

### 修复方案

| 文件 | 改动 |
|------|------|
| `src/harness/workspace.py` 第 31–46 行 | `create()` 新增 `force_reseed` keyword-only 参数 |
| `src/agents/generator.py` 第 166–178 行 | `_create_sprint_workspace()` 根据 `retry_count` 决定 `force_reseed` 值 |

```python
# workspace.py — create() 新逻辑
def create(self, *, force_reseed: bool = True) -> str:
    if self.path.exists():
        if force_reseed:
            shutil.rmtree(self.path)
        else:
            logger.info("Sprint workspace preserved for retry: %s", self.path)
            return str(self.path)
    shutil.copytree(self.codebase_path, self.path, ignore=IGNORE_PATTERNS)
    return str(self.path)

# generator.py — 根据 retry_count 决策
retry_count = state.get("retry_count", 0)
workspace.create(force_reseed=(retry_count == 0))
```

### 修复效果

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 首次尝试 | rmtree + copytree（clean seed） | 不变 |
| Retry | rmtree + copytree（冲掉上次代码） | 保留上次代码，在其上做 targeted fix |
| 9/10 PASS 时 retry | 全部重写 | 只修 FAIL 的那条，不碰已 PASS 的 |

### 测试变更

| 文件 | 改动 |
|------|------|
| `tests/unit/test_workspace.py` 第 97–118 行 | `test_create_cleans_existing_workspace` 改为显式 `force_reseed=True` |
| `tests/unit/test_workspace.py` 第 121–140 行 | 新增 `test_create_preserves_workspace_on_retry` 验证 `force_reseed=False` 行为 |

---

## 修复三：eval_feedback 增加 PASS 条目

### 问题描述

修复二让 Generator 在 retry 时保留上次代码，但它不知道哪些 criterion 已经通过了——可能在修 FAIL 时不小心破坏已 PASS 的。

### 修复方案

| 文件 | 改动 |
|------|------|
| `src/agents/evaluator.py` 第 85–97 行 | `eval_feedback` 构造逻辑增加 PASS 条目 |

```python
# 修复前
eval_feedback = f"Issues found:\n{json.dumps(blocking, indent=2)}\n\nFeedback:\n{feedback}"

# 修复后
passed = [c for c in updated_contract if c["status"] == "PASS"]
parts = []
if passed:
    parts.append("Already passing (DO NOT break these):\n" + json.dumps(passed, indent=2))
parts.append("Issues found:\n" + json.dumps(blocking, indent=2))
parts.append("Feedback:\n" + feedback)
eval_feedback = "\n\n".join(parts)
```

### 修复效果

Generator 在 retry 时收到的 `eval_feedback` 格式：

```
Already passing (DO NOT break these):
[
  {"id": "c1", "status": "PASS", "evidence": "..."},
  {"id": "c2", "status": "PASS", "evidence": "..."}
]

Issues found:
[... FAIL 的 blocking issues ...]

Feedback:
[... Evaluator 的整体反馈 ...]
```

---

## 三个修复的协同效果

修复二和修复三配合工作，形成完整的 retry 优化闭环：

1. **保留代码**：`force_reseed=False` 让 Generator 在自己上次写的代码上修
2. **知道边界**：`eval_feedback` 告诉它哪些已 PASS（不要动）、哪些 FAIL（要修）
3. **节省 token**：不再从零重写，只做 targeted fix
4. **减少 regression**：明确的"DO NOT break these"指令 + 代码未被冲掉 = 不容易误伤

## 全部改动文件汇总

| 文件 | 改动类型 |
|------|----------|
| `src/harness/workspace.py` | `create()` 新增 `force_reseed` 参数 |
| `src/agents/generator.py` | 新增 `import difflib`；重写 `_compute_diff`；`_create_sprint_workspace` 传 `force_reseed` |
| `src/agents/evaluator.py` | `eval_feedback` 构造增加 PASS 条目 |
| `tests/unit/test_workspace.py` | 更新 `test_create_cleans_existing_workspace`；新增 `test_create_preserves_workspace_on_retry` |

---

## 修复四：Generator 工具调用相对路径解析（真实文件 I/O 阻断 bug）

### 问题描述

`GeneratorAgent.execute()` 处理 LLM 返回的工具调用时，将 LLM 返回的相对路径（如 `src/models/tag.py`）原样传给 `pre_write_hook` 和 `registry.execute`。

`pre_write_hook` 内部对路径调用 `Path(file_path).resolve()`，这会相对于 **CWD**（`G:\AgentForge\`）解析，得到 `G:\AgentForge\src\models\tag.py`。而 sprint workspace 位于系统临时目录（如 `C:\Users\kotori\AppData\Local\Temp\agentforge_sprint_...`）。`is_within_workspace` 判定路径不在 workspace 内 → 返回 `exit_code=2` → **所有 `write_file` 调用被 BLOCKED**。

Generator 进入死循环：LLM 返回相同工具调用 → `pre_write_hook` 拒绝 → LLM 再次返回相同调用 → 无限循环。

| 受影响组件 | 影响 |
|-----------|------|
| `GeneratorAgent` | 所有 `write_file` 工具调用被安全钩子拦截，无法写入任何文件 |
| `SprintWorkspace` | workspace 被创建但始终为空（无文件写入） |
| `EvaluatorAgent` | sensors 在空 workspace 上运行，结果无意义 |
| 完整 PGE 链路 | **从 Generator 到 Evaluator 的完整链路从未真实跑通过** |

### 根因分析

在原有集成测试（`test_e2e_tag.py`）中，`registry.execute` 被整体 mock 掉：

```python
with patch("src.tools.registry.registry.execute", new_callable=AsyncMock) as mock_execute:
    mock_execute.return_value = "Tool executed successfully"
```

`pre_write_hook` 从未被真实调用，路径解析 bug 完全隐藏。**14 个集成测试全部通过，但真实文件 I/O 一次都没有跑过。**

### 修复方案

| 文件 | 改动 |
|------|------|
| `src/agents/generator.py` 第 7 行 | 新增 `from pathlib import Path` |
| `src/agents/generator.py` 第 104 行 | `tool_args = dict(tc["args"])` 复制避免修改原始数据 |
| `src/agents/generator.py` 第 107–110 行 | 工具调用前调用 `_resolve_tool_paths()` |
| `src/agents/generator.py` 第 186–197 行 | 新增 `_resolve_tool_paths()` 静态方法 |

```python
# generator.py — 新增路径解析
@staticmethod
def _resolve_tool_paths(tool_args: dict, sprint_workspace: str) -> None:
    """Resolve relative paths in tool args against the sprint workspace."""
    if "path" not in tool_args:
        return
    p = Path(tool_args["path"])
    if not p.is_absolute():
        tool_args["path"] = str(Path(sprint_workspace) / p)

# generator.py — 工具调用循环中使用
tool_args = dict(tc["args"])  # copy to avoid mutating the original
self._resolve_tool_paths(tool_args, sprint_workspace)
```

### 修复效果

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| LLM 返回 `"path": "src/models/tag.py"` | `pre_write_hook` 解析为 `G:\AgentForge\src\models\tag.py` → BLOCKED | 解析为 `<sprint_workspace>/src/models/tag.py` → ALLOWED |
| `read_file("src/models/user.py")` | 读取 CWD 下的文件（错误位置） | 读取 sprint workspace 下的文件（正确位置） |
| `run_tests({"path": "."})` | pytest 在 CWD 下运行 | pytest 在 sprint workspace 下运行（路径解析为 workspace 根目录） |
| 完整 PGE 链路 | Generator 死循环，无法产出文件 | Generator 正常写入 → Evaluator 正常评估 |

---

## 修复五：`_compute_diff` 泄漏 `.pytest_cache` 目录

### 问题描述

`GeneratorAgent._compute_diff()` 遍历 sprint workspace 时，排除列表为 `("__pycache__", ".git", "node_modules")`，遗漏了 `.pytest_cache`。

当 Generator 的工具调用中包含 `run_tests`（或 Evaluator 的 sensors 触发 pytest），pytest 会在 workspace 内创建 `.pytest_cache/` 目录。`_compute_diff` 将这些缓存文件也纳入 diff，导致 `code_diff` 包含无关内容（`.gitignore`、`CACHEDIR.TAG`、`lastfailed`、`nodeids` 等）。

### 修复方案

| 文件 | 改动 |
|------|------|
| `src/agents/generator.py` 第 216 行 | 排除列表增加 `".pytest_cache"` |

```python
# 修复前
dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]

# 修复后
dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules", ".pytest_cache")]
```

---

## 修复六：新增 `test_real_tools.py` 真实工具执行集成测试

### 问题描述

原有集成测试（`test_e2e_tag.py`）mock 了 `registry.execute`、`run_tests`、`run_lint`，只验证了 agent 之间的状态流转，从未验证真实文件 I/O。修复四的路径 bug 在这套测试中完全不可见。

### 新增测试

| 文件 | 改动 |
|------|------|
| `tests/integration/test_real_tools.py` | 新增 4 个测试，仅 mock LLM，工具调用全部真实执行 |

| 测试 | 验证内容 |
|------|----------|
| `test_generator_writes_files_to_sprint_workspace` | `write_file` 真实写入磁盘；`tag.py`、`note_tag.py`、`tags.py`、`test_tags.py` 出现在 sprint workspace；文件内容匹配 LLM 请求 |
| `test_diff_reflects_actual_changes` | `code_diff` 包含实际 unified diff；新文件使用 `--- /dev/null`；路径包含 `tag.py` |
| `test_evaluator_sensors_run_on_sprint_workspace` | `run_tests` / `run_lint` 真实执行 pytest / ruff（非 mock）；返回值包含 `passed`、`exit_code` 等真实结果；Evaluator 正确更新 contract |
| `test_workspace_merge_writes_back_to_codebase` | `merge()` 后 `tag.py` / `note_tag.py` / `tags.py` / `test_tags.py` 出现在原始 codebase；sprint workspace 被清理 |

### Mock 策略

```
LLM client     → mock（pre-recorded responses，无 API 调用）
registry.execute → 真实（write_file / read_file 真实写入/读取磁盘）
run_tests       → 真实（pytest 在 sprint workspace 中执行）
run_lint        → 真实（ruff 在 sprint workspace 中执行）
SprintWorkspace → 真实（copytree / rmtree 操作临时目录）
pre_write_hook  → 真实（路径安全检查在真实路径上运行）
```

---

## 四～六修复的协同效果

修复四、五、六共同解决了"完整 PGE 链路从未跑通"的根本问题：

1. **路径解析**（修复四）：`_resolve_tool_paths` 让 LLM 返回的相对路径正确映射到 sprint workspace
2. **diff 干净**（修复五）：排除 `.pytest_cache`，`code_diff` 只包含 Generator 的实际改动
3. **真实验证**（修复六）：4 个测试用真实文件 I/O 覆盖 Generator → SprintWorkspace → Evaluator 全链路

```
修复前：14 个 mock 测试全通过，但真实链路从未跑通（tag.py 不存在）
修复后：4 个真实工具测试验证文件写入 / diff 计算 / 传感器执行 / workspace 合并
```

## 全部改动文件汇总（含本次修复）

| 文件 | 改动类型 |
|------|----------|
| `src/harness/workspace.py` | `create()` 新增 `force_reseed` 参数 |
| `src/agents/generator.py` | 新增 `import difflib` / `from pathlib import Path`；重写 `_compute_diff`；`_create_sprint_workspace` 传 `force_reseed`；新增 `_resolve_tool_paths`；`.pytest_cache` 排除 |
| `src/agents/evaluator.py` | `eval_feedback` 构造增加 PASS 条目 |
| `tests/unit/test_workspace.py` | 更新 `test_create_cleans_existing_workspace`；新增 `test_create_preserves_workspace_on_retry` |
| `tests/integration/test_real_tools.py` | **新增** — 4 个真实工具执行集成测试 |

---

## 修复七：删除 `_post_evaluate` 死代码

### 问题描述

`src/workflow/graph.py` 中定义了 `_post_evaluate()` 函数（第 18–30 行），其 docstring 声称负责"run evaluation, then merge/discard sprint workspace"，但函数体只有 `return state`（空壳）。实际的 merge/discard 逻辑已由紧随其后的 `_route_and_cleanup()` 处理。

该函数是方案迭代时的遗留产物，全项目无任何调用点（grep 仅命中定义处）。

### 修复方案

| 文件 | 改动 |
|------|------|
| `src/workflow/graph.py` 第 18–30 行 | 删除 `_post_evaluate` 函数定义（13 行） |

### 修复效果

- 消除代码阅读时的误导（读到 docstring 会以为 merge/discard 在这里处理）
- 减少无用代码维护负担

---

## 修复八：修正 `test_cleanup_old_tasks_has_timezone_fix` 测试路径

### 问题描述

`tests/validation/test_report_issues.py` 中的 `test_cleanup_old_tasks_has_timezone_fix` 测试（第 346–358 行）检查 `task_manager.py` 是否使用了 `datetime.now(UTC)` 修复时区 bug。但测试中拼接的路径为：

```python
task_manager_path = SRC_ROOT / "workflow" / "task_manager.py"
```

实际文件位于 `src/api/task_manager.py`。路径不存在 → `pytest.skip("task_manager.py 不存在")` → 测试永远 SKIP，无法验证时区修复是否正确。

经检查，真实文件中的时区修复本身是正确的（已使用 `datetime.now(UTC)`），只是这条测试从未真正执行过验证。

### 修复方案

| 文件 | 改动 |
|------|------|
| `tests/validation/test_report_issues.py` 第 348 行 | `"workflow"` → `"api"` |

```python
# 修复前
task_manager_path = SRC_ROOT / "workflow" / "task_manager.py"

# 修复后
task_manager_path = SRC_ROOT / "api" / "task_manager.py"
```

### 修复效果

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 测试状态 | 永远 SKIP | 正常执行，验证 `datetime.now(UTC)` 存在 |
| 时区修复覆盖 | 无实际测试覆盖 | 测试覆盖到位 |

---

## 七～八修复的性质

这两项是代码审查中发现的**遗留问题清理**，不涉及功能变更：

- **修复七**：删除废函数，纯减法，零风险
- **修复八**：修正测试路径，使已有测试从 SKIP 变为实际执行

## 全部改动文件汇总（含本次修复）

| 文件 | 改动类型 |
|------|----------|
| `src/harness/workspace.py` | `create()` 新增 `force_reseed` 参数 |
| `src/agents/generator.py` | 新增 `import difflib` / `from pathlib import Path`；重写 `_compute_diff`；`_create_sprint_workspace` 传 `force_reseed`；新增 `_resolve_tool_paths`；`.pytest_cache` 排除 |
| `src/agents/evaluator.py` | `eval_feedback` 构造增加 PASS 条目 |
| `src/workflow/graph.py` | 删除 `_post_evaluate` 死函数 |
| `tests/unit/test_workspace.py` | 更新 `test_create_cleans_existing_workspace`；新增 `test_create_preserves_workspace_on_retry` |
| `tests/integration/test_real_tools.py` | **新增** — 4 个真实工具执行集成测试 |
| `tests/validation/test_report_issues.py` | 修正 `test_cleanup_old_tasks_has_timezone_fix` 路径：`workflow` → `api` |
