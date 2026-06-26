# AgentForge 架构指南

> 本文档供开发者快速理解 AgentForge 的整体架构、核心机制和开发流程。

---

## 项目定位

AgentForge 是一个 **Harness Engineering（约束工程）** 框架，展示如何通过系统化的控制、验证和可观测性来构建可靠的 AI Agent。核心理念：LLM（DeepSeek）是可替换的基础设施，真正的工程价值在于包裹在模型外部的约束系统。

系统实现了 **Planner-Generator-Evaluator (PGE)** 多 Agent 架构，Demo 目标是 `toy-repo/` 下的简易 FastAPI 服务。

---

## 常用命令

### AgentForge 主项目

```bash
# 安装
pip install -e ".[dev]"

# 启动基础设施
docker-compose up -d postgres redis

# 运行 API 服务
uvicorn src.main:app --reload

# 测试
pytest                           # 全部测试（asyncio_mode=auto）
pytest tests/test_agents.py      # 单个文件
pytest -k "test_name"            # 按名称匹配
pytest -m "not integration"      # 跳过集成测试

# 代码检查
ruff check .                     # lint（py311, line-length=120）
```

### toy-repo（Demo 目标服务）

```bash
cd toy-repo
pip install -e ".[dev]"
pytest
ruff check .                     # line-length=100
```

---

## 八层架构

```
Layer 8: LLM Client          src/llm/           DeepSeek API 封装，Token 追踪
Layer 7: Storage              src/storage/       PostgreSQL + pgvector + Redis
Layer 6: Retrieval/RAG        src/retrieval/     AST 代码分块 + 向量嵌入 + 语义搜索
Layer 5: Tools                src/tools/         工具注册表 + 6 个工具 + 注解系统
Layer 4: Harness              src/harness/       安全、循环控制、评估、可观测性
Layer 3: Agents               src/agents/        Planner / Generator / Evaluator
Layer 2: Workflow              src/workflow/      LangGraph StateGraph 编排
Layer 1: API                  src/api/           FastAPI REST + SSE 流式推送
```

**依赖方向**：严格自上而下，每层只从下层导入。

---

## 核心机制

### 1. PGE 循环（src/workflow/graph.py）

执行流程：`plan → generate → evaluate → 条件路由`

路由逻辑在 `edges.py:route_after_evaluate()`，使用 **合同级二元判定**（所有验收标准必须通过），而非加权评分：

- **end** — 全部 PASS，任务完成
- **generate** — 有 FAIL 且重试次数未用尽，进入下一轮
- **escalate** — 有 FAIL 且超过最大重试次数，升级报警

### 2. Sprint 工作区（src/harness/workspace.py）

每次 generate 在独立目录中运行，从目标代码库 seed 而来：

- **PASS** → 变更合并回原代码库
- **escalate** → 工作区丢弃
- `force_reseed` 标志处理需要干净工作区的重试场景

### 3. Fresh-Context 评估隔离（src/harness/evaluation/fresh_context.py）

Evaluator **永远看不到** Generator 的执行过程。它只接收：

- 最终的代码 diff
- Sprint 合同（验收标准列表）

这防止了"评估污染"——评估者因看到生成过程而产生偏见。计算传感器（pytest、ruff）在 sprint 工作区上运行；推理传感器使用 LLM。

### 4. Default-FAIL 合同（src/workflow/state.py）

`sprint_contract` 中每个 `Criterion` 默认 `status="FAIL"`。Evaluator 必须显式标记为 PASS。**缺失评估 = 失败**，这是核心安全机制。

### 5. 工具安全（src/harness/safety/）

| 模块 | 职责 |
|------|------|
| `annotations.py` | 每个工具标注 readOnly / destructive / idempotent / openWorld |
| `rule_of_two.py` | openWorld=true 且 readOnly=false 的工具违反 Rule of Two |
| `hooks.py` | `pre_write_hook()` 拒绝写入系统文件、密钥文件或 sprint 工作区外的路径 |

### 6. 循环控制（src/harness/loop/）

`LoopController` 强制执行：

- 迭代次数上限（`MAX_ITERATIONS`）
- Token 预算上限（`TOKEN_BUDGET`）
- 超时限制（`TIMEOUT_SECONDS`）
- 重复检测：相同动作哈希出现 ≥3 次 → 强制切换策略

`RepeatDetector` 使用 SHA256(tool_name + args) 作为哈希。

---

## 配置项（src/config.py）

通过 pydantic-settings 从 `.env` 加载：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DEEPSEEK_API_KEY` | (空) | DeepSeek API 密钥 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 端点 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名称 |
| `DATABASE_URL` | `postgresql+asyncpg://...` | 数据库连接 |
| `REDIS_URL` | `redis://localhost:6379` | Redis 连接 |
| `MAX_SPRINT_RETRIES` | 3 | 最大重试次数 |
| `MAX_ITERATIONS` | 10 | 单次 generate 最大迭代 |
| `TOKEN_BUDGET` | 100,000 | Token 预算 |
| `TIMEOUT_SECONDS` | 300 | 超时秒数 |
| `TOY_REPO_PATH` | `./toy-repo` | Demo 目标路径 |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | 嵌入模型 |
| `EMBEDDING_DIM` | 384 | 嵌入维度 |

---

## toy-repo 编码规范

Demo 目标遵循严格的四层资源结构（详见 `toy-repo/CONVENTIONS.md`）：

```
models/   → SQLModel 数据库模型（table=True）
schemas/  → Pydantic 请求/响应模型（不绑定数据库）
services/ → 业务逻辑层（纯 async，接收 AsyncSession）
routers/  → FastAPI 路由层（只做 HTTP 映射，调用 service）
```

关键约束：

- Router **不直接操作数据库**，只调用 Service
- Service **不抛 HTTPException**，使用自定义异常（NotFoundError→404, ConflictError→409, ValidationError→422），由全局 handler 统一转换
- Schema **不继承 SQLModel**，保持纯 Pydantic

这个文件是 Guide 层的核心——Planner 读取它来理解项目模式，Evaluator 的风格评审 Sensor 对照它判断代码合规性。

---

## 测试说明

- 使用 `pytest-asyncio`，`asyncio_mode = "auto"`（无需手动加 `@pytest.mark.asyncio`）
- 集成测试标记 `@pytest.mark.integration`，跳过用 `-m "not integration"`
- 测试套件 mock 了 LLM Client，单元测试不产生真实 API 调用
- 测试覆盖：PGE 循环、工作区生命周期、工具安全、路由逻辑

---

## Ruff 规则

启用的规则集：

- **E** — pycodestyle 错误
- **F** — pyflakes
- **I** — isort 排序
- **N** — pep8 命名
- **W** — pycodestyle 警告
- **UP** — pyupgrade（Python 3.11 现代化写法）

目标版本：Python 3.11，行宽限制：120 字符。
