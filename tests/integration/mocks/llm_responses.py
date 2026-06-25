"""Pre-recorded LLM responses for E2E testing.

These responses simulate what DeepSeek would return for the Tag task.
Update these if the prompt templates change significantly.
"""

from __future__ import annotations

# Planner response for Tag task
PLANNER_RESPONSE = '''```json
{
  "plan": [
    {
      "id": 1,
      "description": "Create Tag model with id and name fields",
      "dependencies": []
    },
    {
      "id": 2,
      "description": "Create NoteTag association table for many-to-many relationship",
      "dependencies": [1]
    },
    {
      "id": 3,
      "description": "Create Tag CRUD endpoints following existing patterns",
      "dependencies": [1]
    },
    {
      "id": 4,
      "description": "Add tests for Tag model and CRUD operations",
      "dependencies": [2, 3]
    }
  ],
  "sprint_contract": [
    {
      "id": "c1",
      "description": "Tag model created with id (int, PK) and name (str, unique) fields",
      "status": "FAIL",
      "evidence": ""
    },
    {
      "id": "c2",
      "description": "NoteTag association table created with note_id and tag_id foreign keys",
      "status": "FAIL",
      "evidence": ""
    },
    {
      "id": "c3",
      "description": "Tag CRUD endpoints: POST /tags, GET /tags, GET /tags/{id}, PUT /tags/{id}, DELETE /tags/{id}",
      "status": "FAIL",
      "evidence": ""
    },
    {
      "id": "c4",
      "description": "Tests cover Tag creation, CRUD operations, and Note-Tag association",
      "status": "FAIL",
      "evidence": ""
    },
    {
      "id": "c5",
      "description": "All tests pass with pytest",
      "status": "FAIL",
      "evidence": ""
    },
    {
      "id": "c6",
      "description": "No ruff lint errors in new code",
      "status": "FAIL",
      "evidence": ""
    }
  ]
}
```'''

# Generator tool calls sequence
GENERATOR_TOOL_CALLS = [
    # First: read existing models to understand patterns
    {
        "tool_name": "read_file",
        "args": {"path": "src/models/user.py"},
        "result": "from sqlmodel import SQLModel, Field\nfrom typing import Optional\n\nclass User(SQLModel, table=True):\n    __tablename__ = \"users\"\n\n    id: Optional[int] = Field(default=None, primary_key=True)\n    username: str = Field(max_length=50, unique=True)\n    email: str = Field(max_length=100)\n"
    },
    {
        "tool_name": "read_file",
        "args": {"path": "src/models/note.py"},
        "result": "from sqlmodel import SQLModel, Field\nfrom typing import Optional\n\nclass Note(SQLModel, table=True):\n    __tablename__ = \"notes\"\n\n    id: Optional[int] = Field(default=None, primary_key=True)\n    title: str = Field(max_length=100)\n    content: str\n    user_id: int = Field(foreign_key=\"users.id\")\n"
    },
    # Create Tag model
    {
        "tool_name": "write_file",
        "args": {
            "path": "src/models/tag.py",
            "content": "from sqlmodel import SQLModel, Field\nfrom typing import Optional\n\nclass Tag(SQLModel, table=True):\n    __tablename__ = \"tags\"\n\n    id: Optional[int] = Field(default=None, primary_key=True)\n    name: str = Field(max_length=50, unique=True)\n"
        },
        "result": "File created: src/models/tag.py"
    },
    # Create NoteTag association table
    {
        "tool_name": "write_file",
        "args": {
            "path": "src/models/note_tag.py",
            "content": "from sqlmodel import SQLModel, Field\nfrom typing import Optional\n\nclass NoteTag(SQLModel, table=True):\n    __tablename__ = \"note_tags\"\n\n    id: Optional[int] = Field(default=None, primary_key=True)\n    note_id: int = Field(foreign_key=\"notes.id\")\n    tag_id: int = Field(foreign_key=\"tags.id\")\n"
        },
        "result": "File created: src/models/note_tag.py"
    },
    # Create Tag router
    {
        "tool_name": "write_file",
        "args": {
            "path": "src/routers/tags.py",
            "content": "from fastapi import APIRouter, HTTPException\nfrom sqlmodel import select\nfrom ..models.tag import Tag\nfrom ..database import get_session\n\nrouter = APIRouter(prefix=\"/api/v1/tags\", tags=[\"tags\"])\n\n\n@router.post(\"/\", response_model=Tag)\nasync def create_tag(tag: Tag):\n    session = get_session()\n    session.add(tag)\n    session.commit()\n    session.refresh(tag)\n    return tag\n\n\n@router.get(\"/\", response_model=list[Tag])\nasync def list_tags():\n    session = get_session()\n    return session.exec(select(Tag)).all()\n\n\n@router.get(\"/{tag_id}\", response_model=Tag)\nasync def get_tag(tag_id: int):\n    session = get_session()\n    tag = session.get(Tag, tag_id)\n    if not tag:\n        raise HTTPException(status_code=404, detail=\"Tag not found\")\n    return tag\n\n\n@router.put(\"/{tag_id}\", response_model=Tag)\nasync def update_tag(tag_id: int, tag_data: Tag):\n    session = get_session()\n    tag = session.get(Tag, tag_id)\n    if not tag:\n        raise HTTPException(status_code=404, detail=\"Tag not found\")\n    tag.name = tag_data.name\n    session.add(tag)\n    session.commit()\n    session.refresh(tag)\n    return tag\n\n\n@router.delete(\"/{tag_id}\")\nasync def delete_tag(tag_id: int):\n    session = get_session()\n    tag = session.get(Tag, tag_id)\n    if not tag:\n        raise HTTPException(status_code=404, detail=\"Tag not found\")\n    session.delete(tag)\n    session.commit()\n    return {\"message\": \"Tag deleted\"}\n"
        },
        "result": "File created: src/routers/tags.py"
    },
    # Create tests
    {
        "tool_name": "write_file",
        "args": {
            "path": "tests/test_tags.py",
            "content": "import pytest\nfrom sqlmodel import SQLModel, Session, create_engine\nfrom src.models.tag import Tag\nfrom src.models.note_tag import NoteTag\n\n\n@pytest.fixture\ndef session():\n    engine = create_engine(\"sqlite:///:memory:\")\n    SQLModel.metadata.create_all(engine)\n    with Session(engine) as session:\n        yield session\n\n\ndef test_create_tag(session):\n    tag = Tag(name=\"python\")\n    session.add(tag)\n    session.commit()\n    assert tag.id is not None\n    assert tag.name == \"python\"\n\n\ndef test_tag_unique_name(session):\n    tag1 = Tag(name=\"python\")\n    tag2 = Tag(name=\"python\")\n    session.add(tag1)\n    session.commit()\n    with pytest.raises(Exception):\n        session.add(tag2)\n        session.commit()\n\n\ndef test_note_tag_association(session):\n    tag = Tag(name=\"fastapi\")\n    session.add(tag)\n    session.commit()\n    note_tag = NoteTag(note_id=1, tag_id=tag.id)\n    session.add(note_tag)\n    session.commit()\n    assert note_tag.id is not None\n"
        },
        "result": "File created: tests/test_tags.py"
    },
    # Run tests
    {
        "tool_name": "run_tests",
        "args": {"path": "."},
        "result": "===== test session starts =====\nplatform win32 -- Python 3.11.0, pytest-8.0.0\ncollecting ... collected 3 items\n\ntests/test_tags.py::test_create_tag PASSED\ntests/test_tags.py::test_tag_unique_name PASSED\ntests/test_tags.py::test_note_tag_association PASSED\n\n===== 3 passed in 0.15s ====="
    },
    # Run lint
    {
        "tool_name": "run_lint",
        "args": {"path": "."},
        "result": "All checks passed!"
    }
]

# Generator final response (no more tool calls)
GENERATOR_FINAL_RESPONSE = '''I've implemented the Tag resource following the existing patterns:

1. **Tag model** (`src/models/tag.py`): Created with `id` (int, PK) and `name` (str, unique) fields, matching the User/Note pattern.

2. **NoteTag association** (`src/models/note_tag.py`): Created many-to-many association table with `note_id` and `tag_id` foreign keys.

3. **Tag CRUD endpoints** (`src/routers/tags.py`): Implemented POST, GET (list and single), PUT, DELETE endpoints following the existing router pattern.

4. **Tests** (`tests/test_tags.py`): Added tests for Tag creation, unique name constraint, and Note-Tag association.

All tests pass and lint checks are clean.'''

# Evaluator response
EVALUATOR_RESPONSE = '''```json
{
  "criteria_results": [
    {
      "id": "c1",
      "status": "PASS",
      "evidence": "Tag model in src/models/tag.py has id (int, PK) and name (str, unique) fields matching conventions"
    },
    {
      "id": "c2",
      "status": "PASS",
      "evidence": "NoteTag model in src/models/note_tag.py has note_id and tag_id foreign keys for many-to-many relationship"
    },
    {
      "id": "c3",
      "status": "PASS",
      "evidence": "Tag CRUD endpoints implemented: POST /api/v1/tags, GET /api/v1/tags, GET /api/v1/tags/{id}, PUT /api/v1/tags/{id}, DELETE /api/v1/tags/{id}"
    },
    {
      "id": "c4",
      "status": "PASS",
      "evidence": "Tests cover Tag creation, unique name constraint, and Note-Tag association"
    },
    {
      "id": "c5",
      "status": "PASS",
      "evidence": "Independent test run: 3 passed, 0 failed"
    },
    {
      "id": "c6",
      "status": "PASS",
      "evidence": "Independent lint run: All checks passed"
    }
  ],
  "blocking_issues": [],
  "feedback": "Implementation follows existing patterns correctly. Good use of SQLModel and consistent naming conventions.",
  "dimension_scores": {
    "functional_correctness": 100,
    "code_quality": 95,
    "security": 100,
    "architecture_fit": 100,
    "test_coverage": 90
  }
}
```'''
