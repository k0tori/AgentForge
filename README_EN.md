# AgentForge

> A **Harness Engineering** framework demonstrating how to build reliable AI agents through systematic control, verification, and observability — not just prompting.

AgentForge implements a **Planner-Generator-Evaluator (PGE)** multi-agent architecture. Core idea: the LLM is replaceable infrastructure; the harness system wrapping it is the engineering value.

**[中文版](README.md)**

---

## Why AgentForge?

| Traditional Approach | AgentForge Approach |
|---------------------|---------------------|
| Prompt → LLM → Output | Plan → Generate → Evaluate → Retry/Verify |
| Trust the model | Verify with sensors (tests, lint, rubrics) |
| Debug by reading logs | Structured traces + replay capability |
| "It seems to work" | Sprint contracts with default-FAIL semantics |
| Monolithic agent | Isolated roles with fresh-context evaluation |

---

## Architecture Overview

```
                          ┌─────────────────────────┐
                          │      API Layer           │
                          │   FastAPI + SSE Stream   │
                          └────────────┬─────────────┘
                                       │
                                       ▼
        ┌──────────────────────────────────────────────────────────┐
        │              Harness Engine (LangGraph)                   │
        │                                                          │
        │   ┌─────────┐      ┌──────────────┐      ┌─────────────┐│
        │   │ Planner │─────▶│  Generator   │─────▶│  Evaluator  ││
        │   │         │      │              │      │             ││
        │   │ • Task  │      │ • Tool Calls │      │ • Fresh-ctx ││
        │   │   Decomp│      │ • RAG Search │      │ • Sensors   ││
        │   │ • Sprint│      │ • Code Gen   │      │ • Rubrics   ││
        │   │   Contract      │              │      │             ││
        │   └─────────┘      └──────┬───────┘      └──────┬──────┘│
        │                           │                     │       │
        │                           │   PASS ──────────────┴──▶ END│
        │                           │   FAIL ◀───── feedback ──┘   │
        │                           │   (retry w/ circuit breaker) │
        │                           ▼                             │
        │              Isolated worktree per attempt               │
        └──────────────────────────────────────────────────────────┘
                                       │
                                       ▼
        ┌──────────────────────────────────────────────────────────┐
        │                    Infrastructure                         │
        │  DeepSeek API │ PostgreSQL + pgvector │ Redis            │
        └──────────────────────────────────────────────────────────┘
```

---

## Core Design Principles

### 1. Agent = Model + Harness

The LLM (DeepSeek, GPT-4, Claude, etc.) is a replaceable commodity. The engineering value is in the harness that controls, verifies, and observes the model's behavior.

### 2. Guides & Sensors Dual Control

- **Guides (before action)**: CONVENTIONS.md, task templates, tool descriptions
- **Sensors (after action)**: pytest, ruff lint, LLM rubric evaluation

### 3. The Ratchet Principle

Every discovered error pattern gets engineered into the system — either as a new rule in CONVENTIONS.md (Guide layer) or a new acceptance criterion in the Evaluator (Sensor layer). Manual reminders are not enough.

### 4. Fresh-Context Isolation

The Evaluator **never sees** the Generator's intermediate attempts, tool calls, or thinking process. It only receives:
- The final code diff
- The sprint contract (acceptance criteria)

This prevents "evaluation contamination" where the evaluator might be biased by seeing the generation process.

### 5. Default-FAIL Contracts

Every acceptance criterion starts as `FAIL`. The Generator must actively provide evidence (test passes, lint clean) to flip it to `PASS` — not "default pass, mark failures."

---

## Ratchet Principle in Action: From Discovery to Engineering Fix

The Ratchet Principle is not an abstract slogan — below is the actual two-round review-and-fix cycle this project went through, showing how "every discovered error pattern gets engineered into the system" works in practice.

### Round 1: Self-Assessment (2026-06-24)

After completing the initial implementation, a comprehensive self-assessment identified ~85% overall completion. Core modules (LLM Client, Workflow, Safety, Loop Control) were 95%+, but the Evaluation layer (70%), API layer (40%), and testing (50%) had clear gaps.

> 📄 See [`docs/project-review-2026-06-24.md`](docs/project-review-2026-06-24.md)

### Round 2: External Review (2026-06-26)

An independent reviewer discovered a **P0-level issue completely missed by self-assessment**:

> **The Evaluator's computational sensors were not testing the code the Generator wrote.**
>
> FreshContext isolation ensured "the Evaluator can't see the Generator's intermediate process," but it didn't ensure "the Evaluator is testing the code the Generator actually produced" — the Evaluator's `run_tests` was always running against the original codebase, not the Generator's sprint workspace.

This was a classic blind spot: at the interface level, "module exists, interface matches, unit tests pass," but cross-module data flow was never verified.

> 📄 See [`docs/external-review-2026-06-26.md`](docs/external-review-2026-06-26.md)

### Fixes: 8 Issues Engineered Systematically

Each issue was fixed with tested code changes:

| Priority | Issue | Fix | Verification |
|----------|-------|-----|--------------|
| **P0** | SprintWorkspace lifecycle missing | New `SprintWorkspace` class with seed → isolated execution → merge/discard | 9 validation tests pass |
| **P0** | Evaluator testing wrong path | `evaluator._run_sensors` uses `state["sprint_workspace"]` | Real tool integration tests |
| **P0** | Generator tool registration gap | Explicit imports in `graph.py` | E2E tests cover |
| **P0** | Token budget accounting error | `LoopController` uses delta calculation | Unit tests cover |
| **P1** | `_compute_diff` full output | Rewritten as true unified diff | Integration tests verify |
| **P1** | Retry overwrites previous code | `force_reseed` + eval_feedback with PASS items | 2 new unit tests |
| **P1** | Generator path resolution error | `_resolve_tool_paths` maps relative paths to workspace | Real tool tests cover |
| **P2** | Dead code + test path error | Deleted `_post_evaluate`, fixed test path | Validation tests cover |

> 📄 Fix details: [`docs/fix-workspace-retry-eval-feedback-2026-06-26.md`](docs/fix-workspace-retry-eval-feedback-2026-06-26.md)
>
> 📄 E2E verification: [`docs/review_e2e_2026-06-26.md`](docs/review_e2e_2026-06-26.md)

### Verification Results

After the fixes, the full test suite:

```
103 passed, 3 xfailed
```

| Priority | Status |
|----------|--------|
| P0 (SprintWorkspace lifecycle) | ✅ Fully fixed |
| P1 (Dependencies, E2E, diff/retry) | ✅ Fully fixed |
| P2 (Dead code, timezone) | ✅ Mostly fixed |
| P3 (Configurability) | ⚠️ Known remaining (xfail) |

> 📄 Full report: [`docs/validation-test-report-2026-06-26.md`](docs/validation-test-report-2026-06-26.md)

### The Ratchet Effect

The most important outcome wasn't how many bugs were fixed, but **what was engineered into the system**:

1. **`SprintWorkspace` class** — lifecycle management from "scattered across three places, nobody responsible" to "one component owns it all"
2. **`force_reseed` parameter** — retry behavior from "wipe and redo everything" to "preserve existing work, do targeted fixes"
3. **Real tool integration tests** — from "14 mock tests pass but real path never ran" to "4 tests with real file I/O covering the full chain"
4. **eval_feedback with PASS items** — Generator knows what already passes during retry

These are not temporary patches — they systematically turn "the pitfall we fell into" into "impossible to fall into again."

---

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (for PostgreSQL + pgvector and Redis)
- DeepSeek API Key

### Installation

```bash
# Clone the repository
git clone https://github.com/k0tori/AgentForge.git
cd AgentForge

# Create virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"
```

### Configuration

```bash
# Copy environment template
cp .env.example .env
```

Edit `.env` with your API key:

```env
DEEPSEEK_API_KEY=sk-your-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentforge
REDIS_URL=redis://localhost:6379
```

### Start Infrastructure

```bash
docker-compose up -d
```

This starts:
- **PostgreSQL** (pgvector extension) — port 5432
- **Redis** — port 6379

### Run Tests

```bash
# Unit + integration tests (no real API needed)
pytest -m "not integration"

# Full tests (requires real DeepSeek API key)
pytest

# Single test file
pytest tests/unit/test_edges.py

# Match by name
pytest -k "test_route"
```

### Start API Server

```bash
uvicorn src.main:app --reload
```

API docs: http://localhost:8000/docs

### Submit a Task

```bash
# Create task (async PGE execution)
curl -X POST http://localhost:8000/api/v1/tasks/ \
  -H "Content-Type: application/json" \
  -d '{"request": "Add a Tag resource with Note-Tag many-to-many association"}'

# Check task status
curl http://localhost:8000/api/v1/tasks/{task_id}

# SSE stream
curl http://localhost:8000/api/v1/tasks/{task_id}/stream
```

---

## Project Structure

```
AgentForge/
├── src/
│   ├── agents/                 # PGE three roles
│   │   ├── base.py            # Base agent with safe_execute
│   │   ├── planner.py         # Task decomposition + sprint contracts
│   │   ├── generator.py       # Tool-augmented code generation
│   │   └── evaluator.py       # Fresh-context evaluation
│   │
│   ├── harness/               # Harness engineering core
│   │   ├── evaluation/        # Sensors & verification
│   │   ├── loop/              # Loop control (budget, timeout, repeat detection)
│   │   ├── safety/            # Tool safety annotations & hooks
│   │   ├── context/           # Controlled information disclosure
│   │   ├── observability/     # Execution tracing & cost tracking
│   │   └── workspace.py       # Sprint workspace lifecycle
│   │
│   ├── workflow/              # LangGraph StateGraph orchestration
│   │   ├── graph.py           # PGE loop definition + route cleanup
│   │   ├── state.py           # Typed state schema
│   │   └── edges.py           # Conditional routing (contract-level PASS/FAIL)
│   │
│   ├── tools/                 # Generator's tool kit
│   │   ├── registry.py        # Annotated tool registration
│   │   ├── file_ops.py        # File read/write
│   │   ├── code_ops.py        # Code search
│   │   └── test_ops.py        # Test / lint execution
│   │
│   ├── retrieval/             # RAG pipeline (tool, not core flow)
│   ├── api/                   # FastAPI REST + SSE
│   ├── llm/                   # DeepSeek API wrapper
│   └── storage/               # PostgreSQL + pgvector + Redis
│
├── toy-repo/                  # Demo target: FastAPI service (User + Note)
│   └── CONVENTIONS.md         # Coding standards enforced by Evaluator
│
├── tests/
│   ├── unit/                  # Unit tests
│   ├── integration/           # Integration tests (with real tool execution)
│   ├── e2e/                   # E2E tests (requires real DeepSeek API)
│   └── validation/            # Review issue validation tests
│
└── docs/                      # Review reports & fix records
```

---

## Toy Repository

`toy-repo/` is a minimal FastAPI service serving as the PGE loop's demo target:

- **User resource**: Baseline patterns (Pydantic schema, SQLModel, service layer, tests)
- **Note resource**: Adds one-to-many relationships for richer RAG corpus
- **CONVENTIONS.md**: Coding standards enforced by the Evaluator (naming, layering, error handling, testing)

Each resource follows a strict 4-layer structure: `models/` → `schemas/` → `services/` → `routers/`

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| LLM | DeepSeek API (OpenAI-compatible) |
| Agent Orchestration | LangGraph StateGraph |
| LLM Framework | LangChain Core + LangChain OpenAI |
| API | FastAPI + uvicorn + SSE-Starlette |
| Embeddings | Sentence-Transformers (all-MiniLM-L6-v2, 384-dim) |
| Vector Store | PostgreSQL + pgvector |
| Cache | Redis |
| ORM | SQLModel + SQLAlchemy (async) |
| Validation | Pydantic v2 + pydantic-settings |
| HTTP Client | httpx |
| Testing | pytest + pytest-asyncio |
| Linting | Ruff (Python 3.11, line-length=120) |

---

## Observability

AgentForge tracks:

- **Token usage per sprint** — built-in cost awareness (CNY estimation based on DeepSeek pricing)
- **Tool call traces** — full replay capability for debugging
- **Acceptance criteria pass/fail rates** — measure harness effectiveness
- **Retry patterns** — same action hash ≥3 times triggers strategy switch, detect degradation loops early

---

## Design Philosophy: For One, Designed for Many

v1 supports one task pattern: "implement a new resource following existing patterns." But the interfaces between Planner, Generator, and Evaluator are designed to support future task types without architectural changes — not by premature generalization, but by not blocking the extension points.

---

## Acknowledgments

AgentForge is built on top of these excellent open-source projects:

**Agent Orchestration & LLM**
- [LangGraph](https://github.com/langchain-ai/langgraph) — StateGraph orchestration engine driving PGE loop routing and state transitions
- [LangChain](https://github.com/langchain-ai/langchain) — LLM abstraction layer providing ChatOpenAI interface and tool calling protocol

**Web Framework**
- [FastAPI](https://github.com/tiangolo/fastapi) — Async API framework with routing, dependency injection, and auto-documentation
- [uvicorn](https://github.com/encode/uvicorn) — ASGI server
- [SSE-Starlette](https://github.com/sysid/sse-starlette) — Server-Sent Events support for task status streaming

**Data Storage**
- [PostgreSQL](https://www.postgresql.org/) + [pgvector](https://github.com/pgvector/pgvector) — Relational database + vector similarity search
- [Redis](https://github.com/redis/redis) — Caching and session storage
- [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) + [SQLModel](https://github.com/tiangolo/sqlmodel) — Async ORM and model definition
- [asyncpg](https://github.com/MagicStack/asyncpg) — PostgreSQL async driver

**AI & Vectors**
- [Sentence-Transformers](https://github.com/UKPLab/sentence-transformers) — Code embedding model (all-MiniLM-L6-v2)
- [DeepSeek](https://platform.deepseek.com/) — LLM backend (OpenAI-compatible protocol)

**Tools & Validation**
- [pytest](https://github.com/pytest-dev/pytest) + [pytest-asyncio](https://github.com/pytest-dev/pytest-asyncio) — Testing framework
- [Ruff](https://github.com/astral-sh/ruff) — High-performance Python linter
- [Pydantic](https://github.com/pydantic/pydantic) — Data validation and settings management
- [httpx](https://github.com/encode/httpx) — Async HTTP client

---

## License

MIT
