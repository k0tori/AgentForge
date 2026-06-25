# AgentForge

> 一个 **Harness Engineering（约束工程）** 框架，展示如何通过系统化的控制、验证和可观测性来构建可靠的 AI Agent —— 而非仅仅依赖 Prompt Engineering。

AgentForge 实现了 **Planner-Generator-Evaluator (PGE)** 多 Agent 架构，其核心工程价值不在于 LLM 调用本身，而在于包裹在模型外部的完整约束系统。

**[English Version](README_EN.md)**

## 为什么需要 AgentForge？

大多数 AI Agent 项目关注"模型能做什么"，AgentForge 关注的是**"如何让模型的产出可验证地正确"**。

| 传统方式 | AgentForge 方式 |
|---------|----------------|
| Prompt → LLM → 输出 | 规划 → 生成 → 验证 → 重试/确认 |
| 信任模型输出 | 用 Sensors 验证（测试、Lint、评审） |
| 阅读日志调试 | 结构化追踪 + 会话回放 |
| "看起来能用" | Sprint 合同 + Default-FAIL 语义 |
| 单体 Agent | 角色隔离 + Fresh-Context 评估 |

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

## 核心设计原则

### 1. Agent = Model + Harness（约束）

LLM（DeepSeek、GPT-4、Claude 等）是可替换的基础设施。真正的工程价值在于控制、验证和观测模型行为的约束系统。

### 2. Guides 与 Sensors 双重控制

- **Guides（行动前引导）**：CONVENTIONS.md 编码规范、任务模板、工具描述
- **Sensors（行动后验证）**：pytest 测试、ruff lint、LLM 评审、RAGAS 检索忠实度检查

### 3. 棘轮原则（Ratchet Principle）

每发现一次错误模式，就将其工程化进系统 —— 要么写入 CONVENTIONS.md（Guide 层），要么加入 Evaluator 的验收标准（Sensor 层）。口头提醒不可靠。

### 4. Fresh-Context 隔离

Evaluator **永远看不到** Generator 的中间尝试、工具调用或思考过程，它只接收：
- 最终的代码 diff
- Sprint 合同（验收标准）

这防止了"评估污染"——评估者因看到生成过程而产生偏见。

### 5. Default-FAIL 合同

每条验收标准的初始状态都是 `FAIL`。Generator 必须主动提供证据（测试通过、Lint 通过）才能翻转为 `PASS` —— 而非"默认通过，出错才标记"。

## 项目结构

```
src/
├── agents/                 # 多 Agent 角色
│   ├── base.py            # 基础 Agent，带 safe_execute
│   ├── planner.py         # 任务拆解 + Sprint 合同生成
│   ├── generator.py       # 工具增强的代码生成
│   └── evaluator.py       # Fresh-Context 隔离评估
│
├── harness/               # 约束工程核心
│   ├── evaluation/        # Sensors 与验证
│   │   ├── fresh_context.py   # 上下文隔离
│   │   ├── ragas.py          # 检索忠实度检查
│   │   ├── rubric.py         # LLM-as-Judge 评估
│   │   └── calibration.py    # 评分校准
│   │
│   ├── loop/              # 循环控制
│   │   ├── controller.py     # 重试限制与熔断器
│   │   └── repeat_detector.py # 退化检测
│   │
│   ├── safety/            # 安全标注与钩子
│   │   ├── annotations.py    # 工具能力元数据标注
│   │   ├── rule_of_two.py    # 最小权限强制执行
│   │   └── hooks.py          # 前置/后置执行钩子
│   │
│   ├── context/           # 上下文管理
│   │   └── disclosure.py     # 受控信息暴露
│   │
│   └── observability/     # 可观测性
│       ├── trace.py          # 执行追踪
│       ├── cost.py           # Token 成本统计
│       └── replay.py         # 会话回放能力
│
├── workflow/              # LangGraph StateGraph
│   ├── graph.py           # PGE 循环定义
│   ├── state.py           # 类型化状态 Schema
│   └── edges.py           # 条件路由逻辑
│
├── tools/                 # Generator 工具集
│   ├── file_ops.py        # 文件读写操作
│   ├── code_ops.py        # AST 分析、模式检测
│   ├── test_ops.py        # 测试执行
│   └── registry.py        # 带标注的工具注册
│
├── retrieval/             # RAG 流水线（工具，非核心流程）
│   ├── chunking.py        # 代码感知分块
│   ├── embeddings.py      # Sentence-Transformer 向量化
│   ├── indexer.py         # pgvector 索引
│   └── search.py          # 语义搜索
│
├── api/                   # FastAPI REST + SSE
│   ├── routes/
│   └── schemas/
│
└── storage/               # 持久化层
    ├── database.py        # SQLAlchemy/SQLModel
    ├── vector.py          # pgvector 操作
    └── cache.py           # Redis 缓存
```

## Harness 工程深入

### 安全标注（Safety Annotations）

每个工具都标注了能力元数据：

```python
@tool_metadata(
    read_only=True,       # 只读操作
    destructive=False,    # 非破坏性
    idempotent=True,      # 幂等操作
    open_world=False      # 不访问外部世界
)
def read_file(path: str) -> str:
    """读取仓库内文件"""
    ...
```

### Rule of Two（二元规则）

当一个组件同时拥有两个敏感能力（如"访问私有代码" + "处理不可信的检索内容"）时，第三个能力（如"外发 API 调用"）必须被显式排除。

### Sprint 合同

```python
sprint_contract = [
    Criterion(description="Tag 模型已创建", status=FAIL),
    Criterion(description="Note-Tag 关联表存在", status=FAIL),
    Criterion(description="CRUD 端点齐全", status=FAIL),
    Criterion(description="测试覆盖关联场景", status=FAIL),
]
```

### 循环控制器

```python
class LoopController:
    max_sprint_retries: int = 3
    
    def should_continue(self, state: AgentState) -> str:
        # 所有合同项都通过 → 结束
        if all(c.status == PASS for c in state["sprint_contract"]):
            return "end"
        # 超过重试上限 → 升级（确定性熔断，不依赖模型判断）
        if state["retry_count"] >= self.max_sprint_retries:
            return "escalate"
        # 否则带反馈重试
        return "generate"
```

## 快速开始

### 环境要求

- Python 3.11+
- PostgreSQL（带 pgvector 扩展）
- Redis
- DeepSeek API Key（或 OpenAI 兼容端点）

### 安装

```bash
# 克隆仓库
git clone https://github.com/k0tori/AgentForge.git
cd AgentForge

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key 和数据库配置
```

### 配置

```env
# .env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/agentforge
REDIS_URL=redis://localhost:6379/0
```

### 运行

```bash
# 启动基础设施
docker-compose up -d postgres redis

# 启动 API 服务
uvicorn src.main:app --reload

# 运行测试
pytest

# 提交任务
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"request": "参照 Note 资源，新增 Tag 资源并建立多对多关联"}'
```

## Toy 仓库

项目包含 `toy-repo/` 目录 —— 一个最小化的 FastAPI 服务：

- **User 资源**：基线模式（Pydantic Schema、SQLModel、Service 层、测试）
- **Note 资源**：添加一对多关系，提供更丰富的 RAG 语料
- **CONVENTIONS.md**：Evaluator 强制执行的编码规范

这既是 PGE 循环的测试床，也是系统维护的模式参考实现。

## 关键指标与可观测性

AgentForge 追踪以下指标：

- **每个 Sprint 的 Token 用量** —— 内置成本意识
- **工具调用追踪** —— 完整的调试回放能力
- **验收标准通过率** —— 衡量约束系统有效性
- **重试模式** —— 尽早发现退化循环

## 设计理念：为一而造，为多而设计

v1 只支持一种任务模式："参照现有模式实现新功能"。但 Planner、Generator、Evaluator 之间的接口设计，按照"未来可能支持更多任务类型"的标准来写 —— 不是现在就把更多模式做出来，而是现在不把路堵死。

## 致谢

基于以下技术构建：
- [LangGraph](https://github.com/langchain-ai/langgraph) — StateGraph 编排引擎
- [LangChain](https://github.com/langchain-ai/langchain) — LLM 抽象层
- [FastAPI](https://github.com/tiangolo/fastapi) — API 框架
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) — 向量嵌入
- [pgvector](https://github.com/pgvector/pgvector) — 向量相似度搜索

## 许可证

MIT
