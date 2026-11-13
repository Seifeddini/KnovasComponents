"""The refusing budget and the one-read-per-user gate (SS-627)."""
import pytest

from custom_views.budget import OneAtATime, RefusingBudget, shared_budget


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_full_budget_then_refusal_names_the_wait():
    clock = Clock()
    budget = RefusingBudget(60, clock=clock)
    assert budget.take(60) is None
    assert budget.take(1) == 1


def test_refill_is_continuous():
    clock = Clock()
    budget = RefusingBudget(60, clock=clock)
    assert budget.take(60) is None
    clock.now += 30
    assert budget.take(30) is None
    assert budget.take(1) == 1


def test_wait_is_rounded_up():
    clock = Clock()
    budget = RefusingBudget(60, clock=clock)
    assert budget.take(60) is None
    clock.now += 0.5
    assert budget.take(1) == 1
    assert budget.take(10) == 10


def test_more_than_capacity_is_refused_and_takes_nothing():
    clock = Clock()
    budget = RefusingBudget(60, clock=clock)
    wait = budget.take(61)
    assert wait is not None and wait >= 1
    assert budget.take(60) is None


def test_refusal_takes_nothing():
    clock = Clock()
    budget = RefusingBudget(60, clock=clock)
    assert budget.take(50) is None
    assert budget.take(20) is not None
    assert budget.take(10) is None


def test_zero_takes_nothing_and_negative_raises():
    budget = RefusingBudget(60, clock=Clock())
    assert budget.take(0) is None
    with pytest.raises(ValueError):
        budget.take(-1)


def test_capacity_does_not_grow_with_idle_time():
    clock = Clock()
    budget = RefusingBudget(60, clock=clock)
    clock.now += 3600
    assert budget.take(60) is None
    assert budget.take(1) == 1


def test_shared_budget_is_one_per_rate():
    assert shared_budget(7) is shared_budget(7)
    assert shared_budget(7) is not shared_budget(8)


def test_one_at_a_time_per_key():
    gate = OneAtATime()
    assert gate.try_enter("u") is True
    assert gate.try_enter("u") is False
    assert gate.try_enter("v") is True
    gate.leave("u")
    assert gate.try_enter("u") is True


def test_leave_of_unknown_key_is_harmless():
    gate = OneAtATime()
    gate.leave("nobody")
    assert gate.try_enter("nobody") is True
