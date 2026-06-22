from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel


class ContractStatus(BaseModel):
    contract_id: str
    description: str
    status: str  # FAIL / PASS
    evidence: str = ""


class SprintVerdict(BaseModel):
    sprint_id: int
    verdict: str  # PASS / PASS_WITH_WARNINGS / FAIL
    score: float
    contracts: list[ContractStatus]
    blocking_issues: list[str]


class EvaluationReport(BaseModel):
    task_id: uuid.UUID
    final_verdict: str
    total_sprints: int
    sprint_verdicts: list[SprintVerdict]
    cost_breakdown: dict[str, Any]


class CostBreakdown(BaseModel):
    role: str
    tokens: int
    estimated_cost_cny: float
