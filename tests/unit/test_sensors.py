"""Computational sensor gate."""
from __future__ import annotations

from src.agents.evaluator import assess_sensors


def test_clean_sensors_pass():
    ok, detail = assess_sensors({
        "tests": {"passed": 3, "failed": 0, "exit_code": 0, "output": ""},
        "lint": {"issues": [], "exit_code": 0, "issue_count": 0},
    })
    assert ok is True
    assert detail == ""


def test_zero_counts_with_nonzero_exit_fail():
    """Collection errors report 0 passed and 0 failed. The exit code still fails the gate."""
    ok, detail = assess_sensors({
        "tests": {"passed": 0, "failed": 0, "exit_code": 2, "output": "error: file not found"},
        "lint": {"issues": [], "exit_code": 0, "issue_count": 0},
    })
    assert ok is False
    assert "exit=2" in detail


def test_sensor_exception_fails():
    ok, detail = assess_sensors({"error": "No such file or directory: 'python'"})
    assert ok is False
    assert "python" in detail
