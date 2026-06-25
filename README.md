# AgentForge

> A **Harness Engineering** framework demonstrating how to build reliable AI agents through systematic control, verification, and observability — not just prompting.

AgentForge implements a **Planner-Generator-Evaluator (PGE)** multi-agent architecture where the real engineering value lies not in the LLM calls themselves, but in the comprehensive harness system that wraps around them.

## Why AgentForge?

Most AI agent projects focus on "what the model can do." AgentForge focuses on **"how to make the model's work verifiably correct."**

| Traditional Approach | AgentForge Approach |
|---------------------|---------------------|
| Prompt → LLM → Output | Plan → Generate → Evaluate → Retry/Verify |
| Trust the model | Verify with sensors (tests, lint, rubrics) |
| Debug by reading logs | Structured traces + replay capability |
| "It seems to work" | Sprint contracts with default-FAIL semantics |
| Monolithic agent | Isolated roles with fresh-context evaluation |

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

## Core Design Principles

### 1. Agent = Model + Harness

The LLM (DeepSeek, GPT-4, Claude, etc.) is a replaceable commodity. The engineering value is in the harness that controls, verifies, and observes the model's behavior.

### 2. Guides & Sensors Dual Control

- **Guides (before action)**: CONVENTIONS.md, task templates, tool descriptions
- **Sensors (after action)**: pytest, ruff lint, LLM rubric evaluation, RAGAS faithfulness checks

### 3. The Ratchet Principle

Every discovered error pattern gets engineered into the system — either as a new rule in CONVENTIONS.md (Guide layer) or a new acceptance criterion in the Evaluator (Sensor layer). Manual reminders are not enough.

### 4. Fresh-Context Isolation

The Evaluator **never sees** the Generator's intermediate attempts, tool calls, or thinking process. It only receives:
- The final code diff
- The sprint contract (acceptance criteria)

This prevents "evaluation contamination" where the evaluator might be biased by seeing the generation process.

### 5. Default-FAIL Contracts

Every acceptance criterion starts as `FAIL`. The Generator must actively provide evidence (test passes, lint clean) to flip it to `PASS` — not "default pass, mark failures."

## Project Structure

```
src/
├── agents/                 # Multi-agent roles
│   ├── base.py            # Base agent with safe_execute
│   ├── planner.py         # Task decomposition + sprint contracts
│   ├── generator.py       # Tool-augmented code generation
│   └── evaluator.py       # Fresh-context verification
│
├── harness/               # The engineering core
│   ├── evaluation/        # Sensors & verification
│   │   ├── fresh_context.py   # Context isolation
│   │   ├── ragas.py          # Retrieval faithfulness
│   │   ├── rubric.py         # LLM-as-judge evaluation
│   │   └── calibration.py    # Score calibration
│   │
│   ├── loop/              # Loop control
│   │   ├── controller.py     # Retry limits & circuit breaker
│   │   └── repeat_detector.py # Degradation detection
│   │
│   ├── safety/            # Safety annotations & hooks
│   │   ├── annotations.py    # Tool capability annotations
│   │   ├── rule_of_two.py    # Minimum privilege enforcement
│   │   └── hooks.py          # Pre/post execution hooks
│   │
│   ├── context/           # Context management
│   │   └── disclosure.py     # Controlled information disclosure
│   │
│   └── observability/     # Structured logging & replay
│       ├── trace.py          # Execution tracing
│       ├── cost.py           # Token cost tracking
│       └── replay.py         # Session replay capability
│
├── workflow/              # LangGraph StateGraph
│   ├── graph.py           # PGE loop definition
│   ├── state.py           # Typed state schema
│   └── edges.py           # Conditional routing logic
│
├── tools/                 # Generator's tool kit
│   ├── file_ops.py        # File read/write operations
│   ├── code_ops.py        # AST analysis, pattern detection
│   ├── test_ops.py        # Test execution
│   └── registry.py        # Tool registration with annotations
│
├── retrieval/             # RAG pipeline (Tool, not core flow)
│   ├── chunking.py        # Code-aware chunking
│   ├── embeddings.py      # Sentence-transformer embeddings
│   ├── indexer.py         # pgvector indexing
│   └── search.py          # Semantic search
│
├── api/                   # FastAPI REST + SSE
│   ├── routes/
│   └── schemas/
│
└── storage/               # Persistence layer
    ├── database.py        # SQLAlchemy/SQLModel
    ├── vector.py          # pgvector operations
    └── cache.py           # Redis caching
```

## Harness Engineering Deep Dive

### Safety Annotations

Every tool is annotated with capability metadata:

```python
@tool_metadata(
    read_only=True,
    destructive=False,
    idempotent=True,
    open_world=False
)
def read_file(path: str) -> str:
    """Read a file from the repository."""
    ...
```

### Rule of Two

When a component has two sensitive capabilities (e.g., "accesses private code" + "processes untrusted retrieval content"), a third capability (e.g., "can make external API calls") must be explicitly excluded.

### Sprint Contracts

```python
sprint_contract = [
    Criterion(description="Tag model created", status=FAIL),
    Criterion(description="Note-Tag association table exists", status=FAIL),
    Criterion(description="CRUD endpoints complete", status=FAIL),
    Criterion(description="Tests cover association scenarios", status=FAIL),
]
```

### Loop Controller

```python
class LoopController:
    max_sprint_retries: int = 3
    
    def should_continue(self, state: AgentState) -> str:
        if all(c.status == PASS for c in state["sprint_contract"]):
            return "end"
        if state["retry_count"] >= self.max_sprint_retries:
            return "escalate"  # Deterministic circuit breaker
        return "generate"  # Retry with feedback
```

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL with pgvector extension
- Redis
- DeepSeek API key (or OpenAI-compatible endpoint)

### Installation

```bash
# Clone the repository
git clone https://github.com/k0tori/AgentForge.git
cd AgentForge

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env with your API keys and database credentials
```

### Configuration

```env
# .env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/agentforge
REDIS_URL=redis://localhost:6379/0
```

### Running

```bash
# Start infrastructure
docker-compose up -d postgres redis

# Run the API server
uvicorn src.main:app --reload

# Run tests
pytest

# Run with specific task
curl -X POST http://localhost:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"request": "Add a Tag resource with Note-Tag many-to-many association"}'
```

## Toy Repository

The project includes a `toy-repo/` directory — a minimal FastAPI service with:

- **User resource**: Baseline patterns (Pydantic schema, SQLModel, service layer, tests)
- **Note resource**: Adds one-to-many relationships for richer RAG corpus
- **CONVENTIONS.md**: Coding standards that the Evaluator enforces

This serves as both the testbed for the PGE loop and a reference implementation of the patterns the system maintains.

## Key Metrics & Observability

AgentForge tracks:

- **Token usage per sprint** — cost awareness built in
- **Tool call traces** — full replay capability for debugging
- **Acceptance criteria pass/fail rates** — measure harness effectiveness
- **Retry patterns** — detect degradation loops early

## Design Philosophy: For One, Designed for Many

v1 supports one task pattern: "implement a new resource following existing patterns." But the interfaces between Planner, Generator, and Evaluator are designed to support future task types without architectural changes — not by premature generalization, but by not blocking the extension points.

## Acknowledgments

Built with:
- [LangGraph](https://github.com/langchain-ai/langgraph) — StateGraph orchestration
- [LangChain](https://github.com/langchain-ai/langchain) — LLM abstraction
- [FastAPI](https://github.com/tiangolo/fastapi) — API framework
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) — Embeddings
- [pgvector](https://github.com/pgvector/pgvector) — Vector similarity search

## License

MIT
