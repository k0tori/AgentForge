"""API path and SSE payload helpers."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from src.api.routes.tasks import _sse_payload, _validate_codebase_path


def test_sse_payload_is_json():
    assert _sse_payload({"status": "running"}) == '{"status": "running"}'


def test_sibling_prefix_is_rejected():
    cwd = Path.cwd().resolve()
    sibling = Path(str(cwd) + "-other")
    with pytest.raises(HTTPException) as exc:
        _validate_codebase_path(str(sibling))
    assert exc.value.status_code == 400


def test_project_relative_path_is_absolute():
    resolved = _validate_codebase_path("./toy-repo")
    assert Path(resolved).is_absolute()
    assert resolved.endswith("toy-repo")
