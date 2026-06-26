# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AgentForge is a Harness Engineering framework demonstrating how to build reliable AI agents through systematic control, verification, and observability -- not just prompting. It implements a **Planner-Generator-Evaluator (PGE)** multi-agent architecture using LangGraph. The demo target is a toy FastAPI service under `toy-repo/`.

The LLM (DeepSeek) is replaceable infrastructure; the harness system wrapping it is the engineering value.

## Commands

### AgentForge (main project)

```bash
pip install -e ".[dev]"          # Install with dev dependencies
pytest                           # Run all tests (asyncio_mode=auto)
pytest tests/test_agents.py      # Run single test file
pytest -k "test_name"            # Run single test by name
pytest -m "not integration"      # Skip integration tests
ruff check .                     # Lint (py311, line-length=120)
uvicorn src.main:app --reload    # Run API server
docker-compose up -d postgres redis  # Start infrastructure
```

### toy-repo (demo target service)

```bash
cd toy-repo
pip install -e ".[dev]"
pytest                           # Tests (line-length=100 for ruff)
ruff check .
```

## Architecture (8 Layers)

```
Layer 8: LLM Client          src/llm/           DeepSeek API wrapper, token tracking
Layer 7: Storage              src/storage/       PostgreSQL + pgvector + Redis
Layer 6: Retrieval/RAG        src/retrieval/     AST chunking + embedding + search
Layer 5: Tools                src/tools/         Registry + 6 tools + annotation system
Layer 4: Harness              src/harness/       Safety, loop control, evaluation, observability
Layer 3: Agents               src/agents/        Planner / Generator / Evaluator
Layer 2: Workflow              src/workflow/      LangGraph StateGraph orchestration
Layer 1: API                  src/api/           FastAPI REST + SSE endpoints
```

Dependencies flow downward only. Each layer only imports from layers below it.

## Key Architectural Patterns

### PGE Loop (src/workflow/graph.py)

`plan -> generate -> evaluate -> conditional edge`. The routing in `edges.py:route_after_evaluate()` uses **Contract-level binary PASS/FAIL** (all criteria must pass), not weighted verdict scores. Returns "end" (all PASS), "generate" (FAIL + retries left), or "escalate" (max retries exceeded).

### SprintWorkspace (src/harness/workspace.py)

Each generate attempt runs in an isolated directory seeded from the target codebase. On PASS, changes merge back. On escalation, workspace is discarded. The `force_reseed` flag handles retries that need a clean workspace.

### Fresh-Context Evaluation (src/harness/evaluation/fresh_context.py)

The Evaluator **never sees** the Generator's execution trace. It receives only `code_diff + sprint_contract` via `EvalInput`. This prevents evaluation bias. Computational sensors (pytest, ruff) run on the sprint workspace; reasoning sensors use LLM.

### Default-FAIL Contracts (src/workflow/state.py)

Every `Criterion` in `sprint_contract` defaults to `status="FAIL"`. The Evaluator must explicitly mark each as PASS. Absence of evaluation means failure, not success.

### Tool Safety (src/harness/safety/)

- **Annotations** (`annotations.py`): Each tool tagged with readOnly, destructive, idempotent, openWorld metadata
- **Rule of Two** (`rule_of_two.py`): Tools with openWorld=true AND readOnly=false violate the constraint
- **pre_write_hook** (`hooks.py`): Rejects writes to system files, secret files, or outside sprint workspace

### Loop Control (src/harness/loop/)

`LoopController` enforces iteration budget, token budget, timeout, and repeat detection (same action hash >= 3 times forces strategy change). `RepeatDetector` uses SHA256 of tool_name + args.

## Configuration

Settings in `src/config.py` via pydantic-settings, loaded from `.env`:
- `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL` -- LLM backend
- `DATABASE_URL` (asyncpg), `REDIS_URL` -- infrastructure
- `MAX_SPRINT_RETRIES` (3), `MAX_ITERATIONS` (10), `TOKEN_BUDGET` (100k), `TIMEOUT_SECONDS` (300) -- loop params
- `TOY_REPO_PATH` (./toy-repo) -- demo target
- `EMBEDDING_MODEL` (all-MiniLM-L6-v2), `EMBEDDING_DIM` (384) -- RAG embeddings

## toy-repo Conventions

The demo target follows a strict 4-layer pattern per resource: `models/` (SQLModel) -> `schemas/` (Pydantic) -> `services/` (async business logic) -> `routers/` (FastAPI routes). See `toy-repo/CONVENTIONS.md` -- this is a Guide-layer file that the Planner reads and the Evaluator enforces.

Key rules: Router never touches DB directly. Service never raises HTTPException (uses custom exceptions mapped by global handler). Schema never inherits SQLModel.

## Testing Notes

- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (no manual `@pytest.mark.asyncio` needed)
- Integration tests marked with `@pytest.mark.integration`; skip with `-m "not integration"`
- The test suite mocks the LLM client to avoid real API calls in unit tests
- Tests validate the PGE loop, workspace lifecycle, tool safety, and routing logic

## Ruff Rules

Active rule sets: E (pycodestyle errors), F (pyflakes), I (isort), N (pep8-naming), W (pycodestyle warnings), UP (pyupgrade). Target: Python 3.11, line-length 120.
