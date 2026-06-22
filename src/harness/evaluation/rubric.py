from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvalDimension:
    """A single evaluation dimension."""

    name: str
    weight: float
    description: str
    criteria: list[str]


# Sprint-level evaluation dimensions (section 5.6)
# These are for the FINAL REPORT only — they do NOT drive StateGraph routing.
EVAL_DIMENSIONS = [
    EvalDimension(
        name="functional_correctness",
        weight=0.30,
        description="All contract acceptance criteria have corresponding implementations",
        criteria=[
            "Each criterion in sprint_contract has matching code in code_diff",
            "Tests pass when run independently (Evaluator re-runs them)",
            "No placeholder or TODO implementations",
        ],
    ),
    EvalDimension(
        name="code_quality",
        weight=0.20,
        description="Naming conventions, type annotations, CONVENTIONS.md compliance",
        criteria=[
            "snake_case for variables/functions, PascalCase for classes",
            "Type annotations on all function parameters and return values",
            "Custom exceptions used instead of HTTPException in service layer",
            "File organization: models -> schemas -> services -> routers",
        ],
    ),
    EvalDimension(
        name="security",
        weight=0.20,
        description="No obvious security vulnerabilities",
        criteria=[
            "No hardcoded passwords or secrets",
            "Password fields excluded from response schemas",
            "Input validation on user-facing endpoints",
            "No raw SQL without parameterization",
        ],
    ),
    EvalDimension(
        name="architecture_fit",
        weight=0.15,
        description="Follows existing project layer patterns",
        criteria=[
            "Same 4-layer structure as existing resources",
            "Service layer is async, receives session as first param",
            "Router only calls service, never accesses DB directly",
            "Foreign key validation in service layer",
        ],
    ),
    EvalDimension(
        name="test_coverage",
        weight=0.15,
        description="Positive and edge case tests present",
        criteria=[
            "Happy path tests for all CRUD operations",
            "Error case tests: not found, validation failure",
            "Edge cases: duplicate unique fields, invalid FK references",
            "Tests use shared fixtures from conftest.py",
        ],
    ),
]


def compute_verdict(dimensions: dict[str, float]) -> str:
    """Compute Sprint-level verdict from dimension scores.

    Returns: PASS / PASS_WITH_WARNINGS / FAIL
    This is for the final report ONLY — does NOT drive StateGraph routing.
    """
    weighted_sum = 0.0
    for dim in EVAL_DIMENSIONS:
        score = dimensions.get(dim.name, 0.0)
        weighted_sum += score * dim.weight

    if weighted_sum >= 80:
        return "PASS"
    elif weighted_sum >= 60:
        return "PASS_WITH_WARNINGS"
    else:
        return "FAIL"
