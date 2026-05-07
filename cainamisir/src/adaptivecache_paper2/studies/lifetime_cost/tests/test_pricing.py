from studies.lifetime_cost.pipeline.pricing import PriceSheet, cost_of
from studies.lifetime_cost.pipeline.types import (
    CompactionEvent,
    Message,
    Step,
    Trajectory,
    Usage,
)


def _traj(model: str, usages):
    steps = []
    for i, u in enumerate(usages):
        steps.append(Step(
            index=i,
            messages_in=[Message(role="user", content="x")],
            response=Message(role="assistant", content="y"),
            usage=u,
        ))
    return Trajectory(task_id="t", benchmark="b", model=model, policy="p", steps=steps, resolved=True)


def test_pricing_sheet_loads():
    sheet = PriceSheet()
    assert "openai/gpt-4.1" in sheet.names()


def test_cost_basic():
    sheet = PriceSheet()
    t = _traj("openai/gpt-4.1", [Usage(prompt_tokens=1_000_000, completion_tokens=0, cached_tokens=0)])
    c = cost_of(t, sheet)
    # 1M uncached input @ $2.00
    assert abs(c.input_uncached_dollars - 2.00) < 1e-6
    assert c.input_cached_dollars == 0.0


def test_cached_savings():
    sheet = PriceSheet()
    t = _traj("openai/gpt-4.1", [Usage(prompt_tokens=1_000_000, completion_tokens=0, cached_tokens=900_000)])
    c = cost_of(t, sheet)
    # 100K uncached @ $2 + 900K cached @ $0.50 = 0.20 + 0.45 = 0.65
    assert abs(c.total - 0.65) < 1e-6


def test_override_model_keeps_ranking():
    """Reosting under a different price column should be linear in tokens."""
    sheet = PriceSheet()
    t = _traj("openai/gpt-4.1", [Usage(prompt_tokens=1_000_000, completion_tokens=0, cached_tokens=0)])
    c1 = cost_of(t, sheet, override_model="openai/gpt-4.1-mini")
    c2 = cost_of(t, sheet, override_model="openai/gpt-4.1")
    # gpt-4.1 is 5x more expensive on input than gpt-4.1-mini
    assert c2.total / c1.total > 4.0
