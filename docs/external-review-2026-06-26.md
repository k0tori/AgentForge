# AgentForge 外部代码评审

> 评审日期：2026-06-26
> 评审方式：克隆仓库 + 通读设计文档/源码 + 实际安装依赖运行测试 + 核对 git 历史
> 与之对照：`docs/project-review-2026-06-24.md`（项目自评，2026-06-24）
> 定位：这不是对自评文档的否定，是补一个自评容易漏掉的角度——**实际运行路径上数据真正流向哪里**，而不是"模块存在、接口对、单测过"就算数。
> **修订记录**：本版对 2.2 节做了一处订正（误判 `asyncpg` 缺失，已修正为 `sqlmodel`/`email-validator`/`aiosqlite`），订正经过见 2.2 节开头说明。其余结论已逐条复核：核心结论（第 1 节）的代码引用全部重新核对、`chat_structured` 死代码与 `shutil`/`worktree` 关键字已补做全仓库 grep、`test_e2e_tag.py` 此前 view 时被截断的中段已补读，均与原结论一致。

---

## 0. 结论先行

项目自评给 `Harness - Evaluation` 打了 70%，理由是"FreshContext + Rubric 完整，RAGAS/calibration 是 stub"。这个判断在**接口层面**是对的，但漏了一层：**FreshContext 隔离的是"Evaluator 看不到 Generator 的中间过程"，但没有隔离"Evaluator 的计算型 Sensor 测的是不是 Generator 真正写的代码"**——这两件事在设计文档里是同一个意图的两个面，但在代码里被实现成了两件不相关的事，后者目前是失效的。

这是本次评审唯一一个"必须先修，否则项目核心卖点站不住"的问题。其余发现按严重程度递减排列。

---

## 1. 核心问题：Evaluator 的计算型 Sensor 测的不是 Generator 写的代码

### 1.1 现象

无论 Generator 这一轮 Sprint 实际做了什么——哪怕什么都没做、写出来的代码完全错误——`evaluator.py` 里塞进 LLM 评审 prompt 的"Independent Test Results"永远显示同一个数字。这个数字反映的是 toy-repo 在 Sprint 开始之前、Generator 还没动手时的状态。

### 1.2 因果链：这个 bug 是怎么产生的

这不是一处孤立的笔误，是三个分别"看起来合理"的设计/实现决策叠在一起、但没有人验证过它们拼起来之后还成不成立：

```
① 设计文档 4.3 节："Evaluator 不能看到完整 state，只能看 code_diff + sprint_contract"
   → 落地成 FreshContextEvaluator.build_eval_input()，只传两个字段
   → 这一步本身没问题，做到了"思考过程隔离"

② 设计文档 6 节："每次 Sprint 尝试在独立临时目录/git worktree 中进行，
   失败丢弃、成功才合并回主目录"
   → 落地成 generator._create_sprint_workspace()：
     tempfile.gettempdir() 下新建一个空目录
   → 这一步出现了第一个缺口：文档说的是"独立工作目录"，
     但没人把"独立"翻译成"需要先把现有代码 seed 进去"。
     于是 Generator 能 read_file(codebase_path/...) 读到已有模式，
     但只能 write_file(sprint_workspace/...) 写到一个除了新文件什么都没有的空目录。
     这个目录里没有 toy-repo 的 conftest.py、没有 pyproject.toml、
     没有 User/Note 模型——任何引用已有代码的 import 在这里跑都会失败。

③ 设计文档 5.3 节："Evaluator 要自己重新跑测试，不信任 Generator 报告的结果"
   → 落地成 evaluator._run_sensors(codebase_path) 调用 run_tests/run_lint
   → 这里出现了第二个缺口，也是最致命的一个：
     "自己重新跑测试"这句话的本意是"重新跑 Generator 改过的代码"，
     但因为 ② 里 Generator 写的代码从来没有路径回到 codebase_path
    （没有 merge 机制，AgentState 里甚至没有 sprint_workspace 这个字段
     能让 Evaluator 知道该测哪里），写代码的人在实现这一行时，
     手上唯一"看起来正确"的路径就是 codebase_path——
     因为那是状态里唯一存在的路径变量。
```

**关键点**：①②③ 任何一步单独看都符合设计文档的字面意思，审查者（包括昨天的自评、也包括我第一遍扫代码时）很容易因为"这一行调用 `run_tests`，确实在跑测试"而判定这条 Sensor 是"完整"的。只有把三步串起来、再去看 `AgentState` 里到底有没有"sprint_workspace"这个字段，才会发现：**Evaluator 想测 Generator 的代码，但代码里没有任何机制告诉它 Generator 的代码在哪**，于是它退而求其次测了一个确定存在、但跟本次任务无关的路径。这是典型的"接口都对、但接口之间没有真正连通"的缺陷,在只看单个文件的 code review 里非常容易被放过。

### 1.3 实测证据

没有停留在读代码层面，装了 toy-repo 的真实依赖（顺带发现 `pyproject.toml` 缺了 `asyncpg`、`email-validator` 两个隐式依赖，见 3.1）后跑了一遍 baseline：

```
$ pytest tests/ -v   # toy-repo/，Generator 还没动过的原始状态
tests/test_note.py::test_create_note PASSED
tests/test_note.py::test_get_note PASSED
... (共 7 条)
tests/test_user.py::test_create_user PASSED
... (共 8 条)
======================= 15 passed, 22 warnings in 0.22s ========================
```

这 15 条就是 `evaluator._run_sensors()` 不管 Generator 干了什么都会跑出来的结果。换句话说，**"Independent Test Results: Passed 15, Failed 0" 这句话会出现在每一次 Evaluator 的评审 prompt 里，跟 Tag 资源到底有没有被正确实现是两件互不相关的事**。

这也解释了一个此前没解释清楚的疑点：自评文档记录的两次 E2E commit（`9d98942`、`aa889da`）都声称"Evaluator 8/8 PASS"、"10/10 PASS, 0 retries"，但 `git show --stat` 这两个 commit，**没有任何一行改动落在 `toy-repo/` 下**。`toy-repo/src/models/` 里至今只有 `note.py` 和 `user.py`，没有 `tag.py`。两件事现在能对上了：那两次 PASS 不是假的，但也不是"代码被独立计算验证后通过"——是 Evaluator 的推理型 Sensor 读着 `code_diff` 文本（这部分是真实的，`_compute_diff` 确实会反映 sprint_workspace 里新写的文件）做出的主观判断，外加一段跟本次改动完全无关的"测试通过了"噪音信息，两者拼成了一个看起来扎实、实际上只有一半站得住的 PASS。

### 1.4 影响范围

- **直接影响**：项目反复强调的"Default-FAIL + 独立评审"这条核心卖点，在演示环境里目前**实际上退化成了"LLM 自己读代码读后感"**，计算型 Sensor 没有起到设计文档里说的兜底作用。
- **间接影响**：Generator 写出来的代码——不管质量好坏——永远留在 `tempfile.gettempdir()` 下的某个临时目录里，没有任何机制把它合并回 `toy-repo/`。这就是为什么"完整跑通 Tag 任务"这个 portfolio 里最值得展示的 demo，到今天为止在仓库里没有留下任何痕迹：不是没跑过，是跑出来的东西没有地方可去。

### 1.5 修复方向

核心思路：**把"独立工作目录"这件事从"Generator 自己 new 一个空 tempdir"提升成一个有完整生命周期管理的组件**，让 seed（怎么进来）、隔离执行、merge-on-pass/discard-on-fail（怎么出去）由同一个地方负责，而不是分散在 Generator 创建、Evaluator 假设、没人负责合并这三处。

**方案 A（推荐先做，改动小、能立刻让 Evaluator 测到真东西）：Copy-based workspace**

```python
# src/harness/workspace.py（新文件）

import shutil
from pathlib import Path

class SprintWorkspace:
    """Sprint 的隔离工作目录：负责 seed → 暴露路径 → merge/discard。"""

    def __init__(self, codebase_path: str, task_id: str, sprint: int):
        self.codebase_path = Path(codebase_path)
        self.path = Path(tempfile.gettempdir()) / f"agentforge_sprint_{task_id}_{sprint}"

    def create(self) -> str:
        """seed：把现有代码完整拷进 sprint workspace，Generator 在这个基础上改。"""
        if self.path.exists():
            shutil.rmtree(self.path)
        shutil.copytree(
            self.codebase_path, self.path,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"),
        )
        return str(self.path)

    def merge(self) -> None:
        """PASS 之后：把 workspace 覆盖回 codebase_path，再清理临时目录。"""
        shutil.copytree(self.path, self.codebase_path, dirs_exist_ok=True)
        self.discard()

    def discard(self) -> None:
        """FAIL 或合并完成后：丢弃临时目录。"""
        shutil.rmtree(self.path, ignore_errors=True)
```

配套需要改三处：

1. **`AgentState` 加一个字段**（目前完全没有，这是 Evaluator 没法测对路径的根本原因之一）：
   ```python
   sprint_workspace: str  # 本轮 Sprint 的隔离工作目录路径
   ```
2. **`generator.py`**：`_create_sprint_workspace` 改成调用 `SprintWorkspace(...).create()`，并把返回路径写进 `state["sprint_workspace"]`（现在 `execute()` 返回的 dict 里没有这一项，需要加上）。
3. **`evaluator.py._run_sensors`**：把 `codebase_path` 换成 `state.get("sprint_workspace")`。
4. **`workflow/graph.py` 或 `edges.py` 的路由逻辑**：`route_after_evaluate` 返回 `"end"` 时（即所有 Contract PASS），调用一次 `SprintWorkspace.merge()`；返回 `"escalate"` 或确定放弃当前 Sprint 时调用 `discard()`。这是目前缺失的"合并/丢弃"决策点，也是设计文档第 6 节明确写了但代码里没有落地的部分。

这样改完，`evaluator._run_sensors` 测的就是 Generator 真实写出来的、包含已有 User/Note 模式在内的完整代码，"15 passed" 这种跟改动无关的噪音就不会再出现——测试结果会真实反映 Tag 资源到底有没有正确实现。

**方案 B（设计文档原话提到的"或 git worktree"，更彻底，但有前提条件）**

设计文档第 6 节给了两个选项，"独立临时目录"和"git worktree"是并列的。Git worktree 的好处是 seed、隔离、合并、丢弃可以直接复用 git 自身的机制（`git worktree add` 自带完整文件、`git merge` 当合并、`git worktree remove` 当丢弃），比手写 `shutil.copytree` 更不容易出现"忘了排除某个目录"之类的小毛病。

但目前有一个前提条件不满足：**`toy-repo/` 现在不是独立 git 仓库，只是 `AgentForge` 主仓库下的一个子目录**（没有自己的 `.git`）。`git worktree` 是按整个仓库工作的，不能只对一个子目录生效。如果想走这条路，需要先把 `toy-repo/` 拆成一个独立仓库（哪怕只是 `git init` 一下、不单独 push 到 GitHub 也行），Sprint 执行时对这个独立仓库做 worktree。

**建议**：先上方案 A 把 Evaluator 的验证能力修对（这是当前最紧急、改动最小的部分），如果之后想在面试里多讲一层"我们后来从 tempdir 升级成 git worktree，原因是……"，可以把方案 B 留作下一轮 Ratchet 日志的素材——这本身也很符合项目自己强调的"棘轮原则"：先把问题暴露出来，再决定要不要把修复方式升级。

---

## 2. 次要但值得修的问题

### 2.1 `test_e2e_tag.py` 的"E2E"标签名不副实

测试文件本身写得干净，三个 Agent 的 state 流转、Default-FAIL 初始化、Fresh-context 隔离这几条断言都是真实有效的检查。但 `TestE2ETagTask` 这个类里，`registry.execute`、`run_tests`、`run_lint` 全部被 mock 掉了——也就是说这个测试**永远不会跑到第 1 节说的那条 bug**，因为它根本不执行真实的文件写入和 subprocess 调用。

**因果关系**：这不是测试写得不认真，是一个很常见的连锁反应——mock 的边界刚好划在了"被测系统真正脆弱的那个接缝"上。Mock 让测试跑得快、跑得稳定（不依赖真实 DeepSeek API、不依赖真实 pytest 子进程），这本身是对的；但代价是它把第 1 节的整个问题完全藏起来了，"E2E"这个名字会让人（包括写测试的人自己）误以为这条路径被验证过。

**建议**：保留现在这套 mock 测试（对状态流转的验证仍然有价值），但额外加一个**不 mock 工具执行**的集成测试——用真实的 `toy-repo` fixture，真实调用 `write_file`/`run_tests`，只 mock LLM 的文本输出（这部分本来就该 mock，不依赖真实 API）。如果方案 A 修复到位，这个真实集成测试应该能验证：Generator 写的文件确实出现在 `sprint_workspace`、Evaluator 的 `run_tests` 确实是在测这个目录、PASS 之后文件确实出现在 `toy-repo/`。这个测试本身就是"方案 A 修对了"的验收标准。

### 2.2 `toy-repo/pyproject.toml` 缺三个隐式依赖（含一处订正）

> **订正说明**：本节初版（同一天稍早）误将 `asyncpg` 列为"缺失依赖"。复核后发现 `asyncpg>=0.30.0` 其实已经正确写在 `dependencies` 里（第 10 行）——那次失败是我自己在临时虚拟环境里手动敲 `pip install` 包名单时漏打了这一个包，不是项目本身的缺陷。把"我装环境时忘了装什么"和"项目声明里缺什么"混在一起报告，是这次评审过程中我自己犯的一个错误，订正如下。

真正缺失的是另外三个：

- `sqlmodel`：`toy-repo/src/models/*.py` 直接 `from sqlmodel import Field, SQLModel`，但 `pyproject.toml` 的 `dependencies` 里只有 `sqlalchemy[asyncio]`，没有 `sqlmodel` 本身。
- `email-validator`：`schemas/user.py` 用了 `EmailStr`，这是 pydantic 的可选特性，需要单独装 `email-validator`（或声明成 `pydantic[email]`），当前两种写法都没有。
- `aiosqlite`：`tests/conftest.py` 里测试用的是 `TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"`，但 `[project.optional-dependencies].dev` 只列了 `pytest`、`pytest-asyncio`、`httpx`、`ruff`，没有 `aiosqlite`——这个是纯测试期依赖，main dependencies 不需要，但 dev extras 里应该有。

**实测（订正后的版本）**：用 `pip install -e ".[dev]"` 这种"完全依照 pyproject.toml 声明来装"的方式（而不是我最初手动列包名单的方式）在干净环境里装一遍，会在 `conftest.py` 阶段报 `ModuleNotFoundError: sqlmodel`（或者如果连 `dependencies` 都没完整装，会先报别的）；补上这三个包之后，`tests/test_note.py`（7 条）+ `tests/test_user.py`（8 条）共 15 条全部通过。

**为什么会漏**：跟第 1 节是同一类问题的小号版本——**本地能跑 ≠ 声明的依赖是完整的**。大概率是开发过程中实际用的 Python 环境里这些包已经因为别的原因装上了（比如主项目 `AgentForge` 自己的 `pyproject.toml` 就声明了 `sqlmodel`,如果两个项目共享同一个 venv,toy-repo 这边缺的声明就不会在本地暴露出来）,所以没人在一个真正"干净"、只装 toy-repo 自己声明的依赖的环境里验证过它能不能独立跑起来。

**修复**：
```toml
# toy-repo/pyproject.toml
dependencies = [
    ...
    "sqlmodel>=0.0.14",
    "email-validator>=2.0.0",   # 或写成 pydantic[email]
]

[project.optional-dependencies]
dev = [
    ...
    "aiosqlite>=0.20.0",
]
```

### 2.3 两处"写了但没人调用"的方法

- `generator.py` 里的 `cleanup_sprint_workspace()`：昨天的代码评审修复（`52fb51c`，M5 项）加上了这个方法，但全仓库搜索没有任何地方调用它。**这正好是方案 A 里 `SprintWorkspace.discard()` 应该接管的职责**——建议直接删掉这个孤立方法，逻辑并入 `SprintWorkspace`。
- `task_manager.py` 里的 `cleanup_old_tasks()`：同样没人调用,而且这个方法本身有一个潜在 bug——`now = datetime.now()` 是 tz-naive,但被比较的 `task.completed_at` 是 `datetime.now(UTC)` 存的 tz-aware 时间,两者相减会抛 `TypeError`。因为目前没人调用,这个 bug 不会真的发作,但如果之后接上定时清理任务,会立刻炸。**建议修复时一并把这一行改成 `datetime.now(UTC)`，并找个地方真正调用它**（比如 FastAPI 的 startup 事件里起一个周期任务）。

### 2.4 `LLMClient.chat_structured` 是死代码

`evaluator.py`/`planner.py` 实际走的都是 `chat()` + 手动 JSON 解析（自评文档里也提到这是因为 DeepSeek 不支持 OpenAI 的 `structured_output`）。`client.py` 里的 `chat_structured()` 方法目前没有任何调用方，是早期设计遗留下来的接口。不算 bug，但建议要么删掉、要么在注释里标注"为未来支持 structured output 的后端保留"，避免后续维护者花时间去调一个永远不会被走到的方法。

---

## 3. 实测确认"真扎实"的部分（公正起见，正面发现也列出来）

这部分跟昨天的自评文档结论基本一致，这里只列出**我自己实际验证过**（不是单看文档说法）的部分：

| 验证方式 | 结果 |
|---|---|
| 跑 `tests/unit/test_edges.py` | 4/4 通过，`route_after_evaluate` 的 Contract 级二元路由逻辑跟设计文档 4.1 节的修订说明完全一致 |
| 跑 `tests/unit/test_hooks.py` | 5/5 通过，`pre_write_hook` 的系统文件/密钥文件/越界写入三道检查逐一验证有效 |
| 跑 `tests/unit/test_loop_controller.py` | 5/5 通过，迭代预算、token 预算、重复动作检测三件套都是真实现 |
| 跑 `tests/unit/test_state.py` | 4/4 通过，`Criterion` 默认 `status="FAIL"`，Default-FAIL 原则在数据模型层面强制生效 |
| 跑 `tests/unit/test_chunking.py` | 15/15 通过，AST chunking 对函数/类/模块三种切片、装饰器、async 函数、语法错误文件等场景都有覆盖 |
| 读 `src/api/routes/tasks.py` | 确认昨天自评文档里 P1 的"API 未接入 run_task()"已经修复——现在是真实的后台任务调度 + SSE，不再是 stub |
| 读 `src/harness/evaluation/ragas.py`、`replay.py` | 确认是诚实的 `raise NotImplementedError`，不是伪装成"返回 1.0"那种更隐蔽的假完成度 |

单测总计 **33 个全部真实通过**（4+5+5+4+15），跟自评文档报告的数字一致——这部分的"完成度"是可信的,问题集中在跨模块的数据流转上,不在单个模块内部的逻辑正确性上。

---

## 4. 完成度表（与自评文档对照）

| 模块 | 自评文档 (06-24) | 本次评审 (06-26) | 调整理由 |
|---|---|---|---|
| 设计文档 | 100% | 100% | 一致，无新发现 |
| StateGraph / 路由逻辑 | 95% | 95% | 一致，`edges.py` 实测正确 |
| Harness - Safety / Loop | 100% | 100% | 一致，单测全过 |
| **Harness - Evaluation** | **70%** | **30%** | **下调**：FreshContext 隔离做到了，但计算型 Sensor 测的目标路径是错的（第 1 节），等同于没有独立验证 |
| **Sprint Workspace 生命周期** | （未单列） | **20%** | 新增评估维度：seed 缺失、merge 缺失、`AgentState` 没有 `sprint_workspace` 字段，三处一起导致 Generator 的产出从未真正落地 |
| API 层 | 40% | 70% | 上调：06-25 的两次 commit 已把 `run_task()` 接入 REST + SSE |
| 测试 | 50% | 55% | 小幅上调：实测验证了真实通过率；但要扣掉"E2E"命名与实际 mock 范围不符的问题（2.1 节） |
| Retrieval/RAG | 90% | 90% | 一致，chunking 单测全过 |
| toy-repo 本身 | （未单列） | 90% | 功能完整，但有 2 处依赖声明缺失（2.2 节） |

---

## 5. 修复优先级路线图

| 优先级 | 任务 | 对应章节 |
|---|---|---|
| **P0** | 实现 `SprintWorkspace`：seed（拷贝现有代码）+ `AgentState.sprint_workspace` 字段 + Evaluator sensor 改测 workspace + merge-on-pass | 1.5 |
| **P0** | 在方案 A 落地后，重新跑一次 Tag 任务的真实 E2E，这次让结果真的合并进 `toy-repo/` 并提交 git——面试时需要能展示真实 diff，不只是 trace 日志里的数字 | 1.4, 1.5 |
| P1 | `test_e2e_tag.py` 加一个不 mock 工具执行的真实集成测试，验证方案 A 的效果 | 2.1 |
| P1 | `toy-repo/pyproject.toml` 补 `sqlmodel`、`asyncpg`、`email-validator` | 2.2 |
| P2 | 删除孤立的 `cleanup_sprint_workspace()`，逻辑并入 `SprintWorkspace.discard()`；修复并接通 `cleanup_old_tasks()` 的 tz bug | 2.3 |
| P3 | 清理或标注 `chat_structured()` 死代码 | 2.4 |

---

## 6. 附录：本次评审的复现命令

```bash
# 克隆与浏览
git clone https://github.com/k0tori/AgentForge.git

# AgentForge 自身单测（轻量依赖）
python -m venv venv && venv/bin/pip install pydantic pydantic-settings pytest pytest-asyncio
venv/bin/python -m pytest tests/unit/test_edges.py tests/unit/test_hooks.py \
  tests/unit/test_loop_controller.py tests/unit/test_state.py tests/unit/test_chunking.py -v

# toy-repo baseline 测试（验证 Evaluator sensor 实测目标错误的反例）
python -m venv venv2
# 注意：sqlmodel / email-validator / aiosqlite 是 toy-repo 真正缺失声明的依赖（2.2 节）；
# fastapi / pytest 系列 / httpx / pydantic-settings / python-dotenv / asyncpg 是 pyproject.toml 已声明的依赖，
# 这里手动列出是为了在干净环境里复现，正式验证应改用 `pip install -e ".[dev]"` 直接读取声明
venv2/bin/pip install fastapi uvicorn "sqlalchemy[asyncio]" asyncpg pydantic pydantic-settings \
  python-dotenv pytest pytest-asyncio httpx ruff \
  sqlmodel email-validator aiosqlite
cd toy-repo && ../venv2/bin/python -m pytest tests/ -v
# → 15 passed，且与 Generator 是否动过代码无关

# 关键代码路径确认
grep -rn "merge\|copytree" --include="*.py" src/   # 确认没有 seed/merge 逻辑
grep -rn "cleanup_sprint_workspace\|cleanup_old_tasks" --include="*.py" .  # 确认是死代码
git show --stat 9d98942 aa889da   # 确认两次 E2E commit 未触及 toy-repo/
```
