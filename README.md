# AgentForge

> **Harness Engineering** 框架 —— 展示如何通过系统化的控制、验证和可观测性构建可靠的 AI Agent，而非仅仅依赖 Prompt Engineering。

AgentForge 实现了 **Planner-Generator-Evaluator (PGE)** 多 Agent 架构。核心理念：LLM 是可替换的基础设施，包裹它的约束系统才是工程价值。

**[English Version](README_EN.md)**

---

## 为什么需要 AgentForge？

| 传统方式 | AgentForge 方式 |
|---------|----------------|
| Prompt → LLM → 输出 | 规划 → 生成 → 验证 → 重试/确认 |
| 信任模型输出 | 用 Sensors 验证（测试、Lint、评审） |
| 阅读日志调试 | 结构化追踪 + 会话回放 |
| "看起来能用" | Sprint 合同 + Default-FAIL 语义 |
| 单体 Agent | 角色隔离 + Fresh-Context 评估 |

---

## 架构总览

```
                          ┌─────────────────────────┐
                          │       API 层             │
                          │   FastAPI + SSE 流式推送  │
                          └────────────┬─────────────┘
                                       │
                                       ▼
        ┌──────────────────────────────────────────────────────────┐
        │              Harness Engine（LangGraph）                  │
        │                                                          │
        │   ┌─────────┐      ┌──────────────┐      ┌─────────────┐│
        │   │ Planner │─────▶│  Generator   │─────▶│  Evaluator  ││
        │   │  规划器   │      │   生成器      │      │   评估器     ││
        │   │         │      │              │      │             ││
        │   │ • 任务   │      │ • 工具调用    │      │ • 隔离上下文 ││
        │   │   拆解   │      │ • RAG 检索   │      │ • 传感器    ││
        │   │ • Sprint │      │ • 代码生成   │      │ • 评分标准   ││
        │   │   合同   │      │              │      │             ││
        │   └─────────┘      └──────┬───────┘      └──────┬──────┘│
        │                           │                     │       │
        │                           │   PASS ──────────────┴──▶ 结束│
        │                           │   FAIL ◀───── 反馈 ─────┘    │
        │                           │   (带熔断器的重试机制)         │
        │                           ▼                             │
        │              每次尝试在独立工作目录中进行                   │
        └──────────────────────────────────────────────────────────┘
                                       │
                                       ▼
        ┌──────────────────────────────────────────────────────────┐
        │                       基础设施                            │
        │  DeepSeek API │ PostgreSQL + pgvector │ Redis            │
        └──────────────────────────────────────────────────────────┘
```

---

## 核心设计原则

### 1. Agent = Model + Harness（约束）

LLM（DeepSeek、GPT-4、Claude 等）是可替换的基础设施。真正的工程价值在于控制、验证和观测模型行为的约束系统。

### 2. Guides 与 Sensors 双重控制

- **Guides（行动前引导）**：CONVENTIONS.md 编码规范、任务模板、工具描述
- **Sensors（行动后验证）**：pytest 测试、ruff lint、LLM 评审

### 3. 棘轮原则（Ratchet Principle）

每发现一次错误模式，就将其工程化进系统 —— 要么写入 CONVENTIONS.md（Guide 层），要么加入 Evaluator 的验收标准（Sensor 层）。口头提醒不可靠。

### 4. Fresh-Context 隔离

Evaluator **永远看不到** Generator 的中间尝试、工具调用或思考过程，它只接收：
- 最终的代码 diff
- Sprint 合同（验收标准）

这防止了"评估污染"——评估者因看到生成过程而产生偏见。

### 5. Default-FAIL 合同

每条验收标准的初始状态都是 `FAIL`。Generator 必须主动提供证据（测试通过、Lint 通过）才能翻转为 `PASS` —— 而非"默认通过，出错才标记"。

---

## 棘轮原则落地：从发现问题到工程化修复

棘轮原则不是抽象口号 —— 以下是项目实际经历的两轮外部评审和修复过程，展示"每发现一次错误模式，就将其工程化进系统"如何真正执行。

### 第一轮：自评（2026-06-24）

项目完成初版实现后进行了一次全面自评，识别出整体完成度约 85%。核心模块（LLM Client、Workflow、Safety、Loop Control）完成度 95%+，但 Evaluation 层（70%）、API 层（40%）、测试（50%）存在明显缺口。

> 📄 详见 [`docs/project-review-2026-06-24.md`](docs/project-review-2026-06-24.md)

### 第二轮：外部评审（2026-06-26）

邀请外部评审者独立审查代码，发现了一个**自评完全漏掉的 P0 级问题**：

> **Evaluator 的计算型 Sensor 测的不是 Generator 写的代码。**
>
> FreshContext 隔离做到了"Evaluator 看不到 Generator 的中间过程"，但没有隔离"Evaluator 测的是不是 Generator 真正写的代码" —— Evaluator 的 `run_tests` 永远跑的是原始 codebase，而非 Generator 的 sprint workspace。

这正是自评的盲区：接口层面"模块存在、接口对、单测过"，但跨模块的数据流没有被验证。

> 📄 详见 [`docs/external-review-2026-06-26.md`](docs/external-review-2026-06-26.md)

### 修复：8 项问题逐一工程化

收到外部评审后，逐项修复并将每项修复写成带测试的代码变更：

| 优先级 | 问题 | 修复方式 | 测试验证 |
|--------|------|----------|----------|
| **P0** | SprintWorkspace 生命周期缺失 | 新建 `SprintWorkspace` 类，实现 seed → 隔离执行 → merge/discard | 9 项验证测试全通过 |
| **P0** | Evaluator 测错路径 | `evaluator._run_sensors` 改用 `state["sprint_workspace"]` | 真实工具集成测试覆盖 |
| **P0** | Generator 工具注册断层 | `graph.py` 显式导入所有工具模块 | E2E 测试覆盖 |
| **P0** | Token 预算计费错误 | `LoopController` 改为 delta 计算 | 单元测试覆盖 |
| **P1** | `_compute_diff` 全量输出 | 重写为真正的 unified diff | 集成测试验证 |
| **P1** | Retry 冲掉上次代码 | `force_reseed` 参数 + eval_feedback 含 PASS 条目 | 新增 2 个单测 |
| **P1** | Generator 路径解析错误 | `_resolve_tool_paths` 将相对路径映射到 workspace | 真实工具测试覆盖 |
| **P2** | 死代码 + 测试路径错误 | 删除 `_post_evaluate`，修正测试路径 | 验证测试覆盖 |

> 📄 修复详情：[`docs/fix-workspace-retry-eval-feedback-2026-06-26.md`](docs/fix-workspace-retry-eval-feedback-2026-06-26.md)
>
> 📄 E2E 验证：[`docs/review_e2e_2026-06-26.md`](docs/review_e2e_2026-06-26.md)

### 验证结果

修复后运行完整测试套件：

```
103 passed, 3 xfailed
```

| 优先级 | 状态 |
|--------|------|
| P0（SprintWorkspace 生命周期） | ✅ 全部修复 |
| P1（依赖完整性、E2E 验证、diff/retry） | ✅ 全部修复 |
| P2（死代码、时区一致性） | ✅ 大部分修复 |
| P3（配置可配置性） | ⚠️ 已知遗留（xfail） |

> 📄 完整验证报告：[`docs/validation-test-report-2026-06-26.md`](docs/validation-test-report-2026-06-26.md)

### 棘轮效应

这轮迭代中最关键的收获不是修了多少 bug，而是**哪些东西被工程化进了系统**：

1. **`SprintWorkspace` 类** — seed/merge/discard 生命周期从"分散在三处没人负责"变成"一个组件统一管理"
2. **`force_reseed` 参数** — retry 行为从"每次冲掉重来"变成"保留已有成果做 targeted fix"
3. **真实工具集成测试** — 从"14 个 mock 测试全通过但真实链路从未跑过"变成"4 个测试用真实文件 I/O 覆盖全链路"
4. **eval_feedback 含 PASS 条目** — Generator 在 retry 时知道哪些已通过、不要动

这些不是临时补丁，是系统性地把"上次踩过的坑"变成了"下次不可能再踩"。

---

## 快速开始

### 环境要求

- Python 3.11+
- Docker（用于 PostgreSQL + pgvector 和 Redis）
- DeepSeek API Key

### 安装

```bash
# 克隆仓库
git clone https://github.com/k0tori/AgentForge.git
cd AgentForge

# 创建虚拟环境
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 安装依赖
pip install -e ".[dev]"
```

### 配置

```bash
# 复制环境变量模板
cp .env.example .env
```

编辑 `.env`，填入你的 API Key：

```env
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentforge
REDIS_URL=redis://localhost:6379
```

### 启动基础设施

```bash
docker-compose up -d
```

这会启动：
- **PostgreSQL**（pgvector 扩展）— 端口 5432
- **Redis** — 端口 6379

### 运行测试

```bash
# 单元测试 + 集成测试（不需要真实 API）
pytest -m "not integration"

# 完整测试（需要真实 DeepSeek API Key）
pytest

# 单个测试文件
pytest tests/unit/test_edges.py

# 按名称匹配
pytest -k "test_route"
```

### 启动 API 服务

```bash
uvicorn src.main:app --reload
```

API 文档：http://localhost:8000/docs

### 提交任务

```bash
# 创建任务（异步执行 PGE 循环）
curl -X POST http://localhost:8000/api/v1/tasks/ \
  -H "Content-Type: application/json" \
  -d '{"request": "参照 Note 资源，新增 Tag 资源并建立多对多关联"}'

# 查询任务状态
curl http://localhost:8000/api/v1/tasks/{task_id}

# SSE 流式监听
curl http://localhost:8000/api/v1/tasks/{task_id}/stream
```

---

## 项目结构

```
AgentForge/
├── src/
│   ├── agents/                 # PGE 三角色
│   │   ├── base.py            # 基础 Agent，带 safe_execute
│   │   ├── planner.py         # 任务拆解 + Sprint 合同生成
│   │   ├── generator.py       # 工具增强的代码生成
│   │   └── evaluator.py       # Fresh-Context 隔离评估
│   │
│   ├── harness/               # 约束工程核心
│   │   ├── evaluation/        # Sensors 与验证
│   │   ├── loop/              # 循环控制（预算、超时、重复检测）
│   │   ├── safety/            # 工具安全标注与钩子
│   │   ├── context/           # 受控信息暴露
│   │   ├── observability/     # 执行追踪与成本统计
│   │   └── workspace.py       # Sprint 工作区生命周期管理
│   │
│   ├── workflow/              # LangGraph StateGraph 编排
│   │   ├── graph.py           # PGE 循环定义 + 路由清理
│   │   ├── state.py           # 类型化状态 Schema
│   │   └── edges.py           # 条件路由（合同级 PASS/FAIL）
│   │
│   ├── tools/                 # Generator 工具集
│   │   ├── registry.py        # 带标注的工具注册
│   │   ├── file_ops.py        # 文件读写
│   │   ├── code_ops.py        # 代码搜索
│   │   └── test_ops.py        # 测试 / Lint 执行
│   │
│   ├── retrieval/             # RAG 流水线（工具，非核心流程）
│   ├── api/                   # FastAPI REST + SSE
│   ├── llm/                   # DeepSeek API 封装
│   └── storage/               # PostgreSQL + pgvector + Redis
│
├── toy-repo/                  # Demo 目标：FastAPI 服务（User + Note）
│   └── CONVENTIONS.md         # Evaluator 强制执行的编码规范
│
├── tests/
│   ├── unit/                  # 单元测试
│   ├── integration/           # 集成测试（含真实工具执行）
│   ├── e2e/                   # E2E 测试（需真实 DeepSeek API）
│   └── validation/            # 评审问题验证测试
│
└── docs/                      # 评审报告与修复记录
```

---

## Toy 仓库

`toy-repo/` 是一个最小化的 FastAPI 服务，作为 PGE 循环的演示目标：

- **User 资源**：基线模式（Pydantic Schema、SQLModel、Service 层、测试）
- **Note 资源**：添加一对多关系，提供更丰富的 RAG 语料
- **CONVENTIONS.md**：Evaluator 强制执行的编码规范（命名、分层、错误处理、测试）

每个资源严格遵循四层结构：`models/` → `schemas/` → `services/` → `routers/`

---

## 技术栈

| 层次 | 技术 |
|------|------|
| LLM | DeepSeek API（OpenAI 兼容协议） |
| Agent 编排 | LangGraph StateGraph |
| LLM 框架 | LangChain Core + LangChain OpenAI |
| API | FastAPI + uvicorn + SSE-Starlette |
| 嵌入模型 | Sentence-Transformers（all-MiniLM-L6-v2, 384 维） |
| 向量存储 | PostgreSQL + pgvector |
| 缓存 | Redis |
| ORM | SQLModel + SQLAlchemy（async） |
| 验证 | Pydantic v2 + pydantic-settings |
| HTTP 客户端 | httpx |
| 测试 | pytest + pytest-asyncio |
| Lint | Ruff（Python 3.11, line-length=120） |

---

## 可观测性

AgentForge 追踪以下指标：

- **每个 Sprint 的 Token 用量** — 内置成本意识（基于 DeepSeek 定价的 CNY 估算）
- **工具调用追踪** — 完整的调试回放能力
- **验收标准通过率** — 衡量约束系统有效性
- **重试模式** — 相同动作哈希 ≥3 次触发策略切换，尽早发现退化循环

---

## 设计理念：为一而造，为多而设计

v1 只支持一种任务模式："参照现有模式实现新功能"。但 Planner、Generator、Evaluator 之间的接口设计，按照"未来可能支持更多任务类型"的标准来写 —— 不是现在就把更多模式做出来，而是现在不把路堵死。

---

## 致谢

AgentForge 建立在以下优秀的开源项目之上：

**Agent 编排与 LLM**
- [LangGraph](https://github.com/langchain-ai/langgraph) — StateGraph 编排引擎，驱动 PGE 循环的条件路由与状态流转
- [LangChain](https://github.com/langchain-ai/langchain) — LLM 抽象层，提供 ChatOpenAI 接口与工具调用协议

**Web 框架**
- [FastAPI](https://github.com/tiangolo/fastapi) — 异步 API 框架，提供路由、依赖注入与自动文档
- [uvicorn](https://github.com/encode/uvicorn) — ASGI 服务器
- [SSE-Starlette](https://github.com/sysid/sse-starlette) — Server-Sent Events 支持，用于任务状态流式推送

**数据存储**
- [PostgreSQL](https://www.postgresql.org/) + [pgvector](https://github.com/pgvector/pgvector) — 关系数据库 + 向量相似度搜索
- [Redis](https://github.com/redis/redis) — 缓存与会话存储
- [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) + [SQLModel](https://github.com/tiangolo/sqlmodel) — 异步 ORM 与模型定义
- [asyncpg](https://github.com/MagicStack/asyncpg) — PostgreSQL 异步驱动

**AI 与向量**
- [Sentence-Transformers](https://github.com/UKPLab/sentence-transformers) — 代码嵌入模型（all-MiniLM-L6-v2）
- [DeepSeek](https://platform.deepseek.com/) — LLM 后端（OpenAI 兼容协议）

**工具与验证**
- [pytest](https://github.com/pytest-dev/pytest) + [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) — 测试框架
- [Ruff](https://github.com/astral-sh/ruff) — 高性能 Python Linter
- [Pydantic](https://github.com/pydantic/pydantic) — 数据验证与 Settings 管理
- [httpx](https://github.com/encode/httpx) — 异步 HTTP 客户端

---

## 许可证

MIT
