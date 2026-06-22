from __future__ import annotations

from src.harness.loop.controller import LoopController


def test_iteration_budget():
    """Test iteration budget check."""
    ctrl = LoopController(max_iterations=5)
    assert ctrl.check_iteration_budget(0) is True
    assert ctrl.check_iteration_budget(4) is True
    assert ctrl.check_iteration_budget(5) is False


def test_token_budget():
    """Test token budget check."""
    ctrl = LoopController(token_budget=1000)
    assert ctrl.check_token_budget(500) is True
    assert ctrl.check_token_budget(1000) is False


def test_repeat_detection():
    """Test action repeat detection."""
    ctrl = LoopController(repeat_threshold=3)
    action_hash = "abc123"

    assert ctrl.should_force_strategy_change(action_hash) is False
    ctrl.record_action(action_hash)
    assert ctrl.should_force_strategy_change(action_hash) is False
    ctrl.record_action(action_hash)
    assert ctrl.should_force_strategy_change(action_hash) is False
    ctrl.record_action(action_hash)
    assert ctrl.should_force_strategy_change(action_hash) is True


def test_hash_action_deterministic():
    """Test that action hashing is deterministic."""
    h1 = LoopController.hash_action("read_file", {"path": "/foo"})
    h2 = LoopController.hash_action("read_file", {"path": "/foo"})
    assert h1 == h2


def test_hash_action_different_args():
    """Test that different args produce different hashes."""
    h1 = LoopController.hash_action("read_file", {"path": "/foo"})
    h2 = LoopController.hash_action("read_file", {"path": "/bar"})
    assert h1 != h2
