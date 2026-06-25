# AgentForge 项目架构审查

> 审查日期：2026-06-24
> 审查范围：全项目源码、设计文档、git历史、测试覆盖
> 审查视角：架构完整性、工程完成度、可维护性

---

## 一、项目概述

AgentForge 是一个 **Planner-Generator-Evaluator (PGE) 代码助手**，核心价值是展示 Harness Engineering 方法论——LLM 是可替换的基础设施，包裹它的工程系统才是价值所在。

系统接收用户请求（如"Add a Tag resource following existing patterns"），自动完成：分析代码库 → 生成实现计划 → 编写代码 → 独立评估 → 必要时重试。演示目标是 `toy-repo/` 下的 FastAPI 服务（User + Note 资源）。

---

## 二、架构分层

```
Layer 8: LLM Client          src/llm/           DeepSeek API 封装，token 追踪
Layer 7: Storage              src/storage/       PostgreSQL + pgvector + Redis
Layer 6: Retrieval/RAG        src/retrieval/     AST chunking + embedding + search
Layer 5: Tools                src/tools/         注册表 + 6 个工具 + 注解系统
Layer 4: Harness              src/harness/       安全、循环控制、评估、可观测性
Layer 3: Agents               src/agents/        Planner / Generator / Evaluator
Layer 2: Workflow              src/workflow/      LangGraph StateGraph 编排
Layer 1: API                  src/api/           FastAPI REST 端点
```

每层职责单一，依赖方向单向向下。`BaseAgent` 抽象强制统一接口，`ToolRegistry` 解耦工具注册与调用，`FreshContextEvaluator` 物理隔离 Evaluator 上下文。

---

## 三、完成度评估

| 模块 | 完成度 | 备注 |
|------|--------|------|
| 设计文档 | ✅ 100% | 850 行，8 条工程原则清晰，可作为独立产出 |
| LLM Client | ✅ 100% | DeepSeek 封装，token 追踪，chat/tool 双模式 |
| Workflow (LangGraph) | ✅ 95% | StateGraph + 条件路由完整，缺错误恢复路径 |
| Agents (PGE) | ✅ 90% | 三角色实现完整，JSON 修复鲁棒 |
| Tools | ✅ 95% | 注册表 + 6 个工具 + 注解系统 |
| Harness - Safety | ✅ 100% | hooks + annotations + Rule of Two |
| Harness - Loop Control | ✅ 100% | 预算、超时、重复检测全到位 |
| Harness - Evaluation | ⚠️ 70% | FreshContext + Rubric 完整，RAGAS/calibration 是 stub |
| Harness - Observability | ⚠️ 60% | Trace + Cost 完整，Replay 是 stub |
| Retrieval/RAG | ✅ 90% | AST chunking + embedding + search 完整 |
| Storage | ✅ 95% | PG + pgvector + Redis，安全措施到位 |
| API Layer | ⚠️ 40% | 路由骨架在，task 创建未接入 workflow |
| 测试 | ⚠️ 50% | 33 个单元测试 + E2E 验证通过（ad-hoc），无可重跑的集成测试 |

**总体判断：骨架 90%，血肉 85%，皮肤 50%。**

---

## 四、E2E 验证状态

### 已验证

PGE 循环**已通过真实 DeepSeek API 端到端跑通**，证据来自两次 git commit：

| Commit | 时间 | 结果 |
|--------|------|------|
| `9d98942` | 2026-06-24 00:34 | Planner: 5 steps / 8 criteria；Generator: 21 tool calls；Evaluator: 8/8 PASS |
| `aa889da` | 2026-06-24 03:05 | 10/10 criteria PASS，0 次重试 |

### E2E 过程中修复的真实问题

**DeepSeek API 兼容性**（commit `9d98942`）：
- Prompt 模板中未转义的 JSON 花括号 `{}` 导致 Python `.format()` 失败 → 转义为 `{{}}`
- DeepSeek 不支持 `structured_output`（OpenAI 专属）→ 改用 `chat()` + 手动 JSON 解析
- JSON 提取的贪婪正则失败 → 改用括号计数算法
- Generator 未对每个 `tool_call_id` 返回 `ToolMessage` → 修复为 1:1 响应

**JSON 鲁棒性**（commit `aa889da`）：
- LLM 输出经常缺少逗号或有尾逗号 → 添加 `_repair_json()` 正则修复
- Generator 随机探索目录而非读取已有模式 → prompt 中添加明确的 `read_file()` 调用指引

### 验证方式

E2E 测试通过直接调用 `run_task()` 完成（Python REPL 或 Claude Code session），**非**通过 REST API。`POST /api/v1/tasks/` 端点仍为 stub，未接入 `run_task()`。

---

## 五、架构优点

### 1. 分层极其清晰，职责单一

8 层架构从 API 到 LLM，每层只做一件事。不是"看起来像分层"，是真的在用代码约束架构意图。

### 2. 安全设计前置而非后补

- `pre_write_hook` 阻止写系统文件/密钥文件
- `Rule of Two` 从工具集层面禁止网络外泄 + 写入的组合
- `table_name_allowlist` 防 SQL 注入

这些是从设计文档第 5 章直接映射到代码的。作为 demo 项目，安全意识超过多数生产项目。

### 3. Contract-level 路由决策的克制

Sprint-level 加权评分只用于报告，路由决策只看 Contract 的二元 PASS/FAIL。避免了"分数 65 分算过还是 70 分算过"的无尽调参。

### 4. JSON 修复的工程务实

`_repair_json()` 处理尾逗号、缺逗号、代码围栏——承认 LLM 输出不完美并在工程层面兜底，而非在 prompt 里祈祷。

### 5. RAG 作为 Tool 而非核心流

Retrieval 是 Generator 可以调用的工具，不是 pipeline 的必经之路。保持了架构简洁。

### 6. Ratchet 原则的活 demo

E2E 两轮迭代本身就是设计文档第 3 条原则的实践：第一轮的失败（DeepSeek 兼容性、JSON 解析）被转化为工程修复（代码变更），而非口头提醒。第二轮达到 10/10 PASS + 0 重试。

---

## 六、架构缺陷

### 🟡 中等问题

#### 1. E2E 是 ad-hoc 的，未固化为可重跑测试

E2E 验证通过直接调用 `run_task()` 完成，没有提交为测试文件。下次改代码无法快速回归验证。

**建议**：在 `tests/integration/` 下添加一个 `test_e2e_tag.py`，用 mock LLM 或录制的 API 响应重放，确保可重复运行。

#### 2. API 层未接入 run_task()

`POST /api/v1/tasks/` 返回 UUID 但不调用 `run_task()`。只能 REPL 调用，无法 HTTP 调用。

**建议**：将 `run_task()` 接入路由，配合 SSE streaming 返回执行进度。

#### 3. 错误处理几乎为零

所有 Agent 的 `execute()` 方法都是 happy path。缺少：
- LLM 调用失败（网络超时、API 限流、返回空内容）的重试/降级策略
- 工具执行失败（文件不存在、测试崩溃）的状态恢复
- LangGraph 节点异常时的 graceful degradation

E2E 跑通了 happy path，但异常路径未被验证。

**建议**：在每个 Agent 节点加 try-catch，LLM 调用加 retry with backoff，工具执行加 timeout。

#### 4. AgentState 是 TypedDict 而非 Pydantic Model

运行时没有字段验证，Agent 可以往 state 里塞任何东西。与项目的严谨调性矛盾。

**建议**：改为 Pydantic BaseModel，获得运行时验证 + 默认值管理 + 序列化支持。

### 🟢 轻微问题

#### 5. 三个 stub 模块制造虚假完成度

- `ragas.py`：所有指标返回 1.0
- `replay.py`：原样返回 trace
- `calibration.py`：空壳框架

**建议**：用 `raise NotImplementedError("Phase 2: ...")` 替代假返回值，或在模块顶部加 `# STUB` 注释。

#### 6. 配置硬编码与可配置性的矛盾

- `Temperature=0.1` 硬编码在 `client.py`
- Embedding 模型和维度硬编码在 `config.py` 默认值
- Prompt 模板硬编码为字符串常量

设计文档说"Build for one, design for many"，当前是"Build for one, configure for one"。

#### 7. `scripts/codegraph-sync.sh` 是 bash 脚本但项目是 Windows 环境

主开发环境是 Windows 11（PowerShell），脚本是 bash/WSL2 专用。

#### 8. 日志系统缺失

没有 `logging` 配置。所有输出要么是 print 要么是 trace 结构体。

---

## 七、技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| API | FastAPI + uvicorn |
| Agent 编排 | LangGraph (StateGraph) |
| LLM 框架 | langchain-core + langchain-openai |
| LLM 后端 | DeepSeek API（OpenAI 兼容） |
| ORM | SQLModel + SQLAlchemy (async) |
| 数据库 | PostgreSQL + pgvector (asyncpg) |
| 缓存 | Redis (redis-py async) |
| Embedding | sentence-transformers (all-MiniLM-L6-v2, 384-dim) |
| 验证 | Pydantic v2 + pydantic-settings |
| SSE | sse-starlette |
| HTTP | httpx |

---

## 八、设计原则（8 条）

1. **Agent = Model + Harness**：LLM 是可替换基础设施，Harness 是价值
2. **Guides + Sensors 双控**：行动前有 Guide（CONVENTIONS.md），行动后有 Sensor（pytest, ruff, LLM review）
3. **Ratchet 原则**：每个 Generator 错误转化为工程修复（新规则或新 Sensor），而非口头提醒
4. **Generator-Evaluator 分离 + Fresh-context**：Evaluator 永远看不到 Generator 的中间尝试
5. **Default-FAIL 合约**：每个验收标准初始为 FAIL，Generator 必须证明 PASS
6. **RAG as Tool**：检索是 Generator 调用的工具，不是核心流程
7. **Build for one, design for many**：当前单任务模式，接口可扩展
8. **Rule of Two**：Generator 有代码访问 + 不受信 RAG 内容，因此排除网络外泄

---

## 九、下一步建议（按优先级）

| 优先级 | 任务 | 工作量 |
|--------|------|--------|
| P0 | 将 E2E 固化为可重跑测试（mock LLM 或录制回放） | 1-2 天 |
| P1 | 接通 REST API → `run_task()` + SSE streaming | 1 天 |
| P1 | 补错误处理（Agent try-catch、LLM retry、工具 timeout） | 1-2 天 |
| P2 | AgentState 改用 Pydantic Model | 半天 |
| P2 | 删除或明确标注 stub 模块 | 半天 |
| P3 | 添加 structured logging | 半天 |
| P3 | 补充单元测试覆盖率（目标 80%） | 2-3 天 |

---

## 十、总结

AgentForge 的核心价值——证明 Harness Engineering 方法论可行——**已经被 E2E 验证**。两个 commit 记录了一次完整的"发现问题 → 工程修复 → 验证通过"循环，这本身就是设计原则 #3（Ratchet）的活 demo。

剩余工作量是**工程化收尾**而非**架构补洞**：固化 E2E 测试、接通 REST API、补错误处理。这些都是增量工作，不涉及架构决策。

项目作为**设计文档 + 架构示范**是优秀的。8 条工程原则有代码支撑，分层清晰，安全前置。作为**可工作的软件**，它已经跑通了核心路径，剩下的只是把它变得更易用、更健壮。
