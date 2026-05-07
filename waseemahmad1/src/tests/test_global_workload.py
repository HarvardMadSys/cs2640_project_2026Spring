import pytest

from simulator.global_workload import (
    generate_shifted_global_events,
    generate_shared_global_events,
    summarize_events,
)


def test_shared_global_events_are_deterministic() -> None:
    e1 = generate_shared_global_events(seed=123)
    e2 = generate_shared_global_events(seed=123)
    e3 = generate_shared_global_events(seed=124)

    assert e1 == e2
    assert e1 != e3
    assert len(e1) > 0


def test_shifted_events_return_split_index() -> None:
    events, split = generate_shifted_global_events(seed=9)

    assert len(events) > 0
    assert 0 < split < len(events)


def test_shifted_events_hard_mode_is_valid() -> None:
    events, split = generate_shifted_global_events(seed=9, shift_level="hard")

    assert len(events) > 0
    assert 0 < split < len(events)


def test_shifted_events_invalid_mode_raises() -> None:
    with pytest.raises(ValueError):
        generate_shifted_global_events(seed=1, shift_level="unknown")


def test_summarize_events_sanity() -> None:
    events = [
        ("r0", "shared_prefix_block_0", 3, True),
        ("r1", "shared_prefix_block_0", 3, True),
        ("r1", "req_1_block_0", 1, False),
    ]
    summary = summarize_events(events)

    assert summary["total_accesses"] == 3
    assert summary["unique_blocks"] == 2
    assert summary["unique_requests"] == 2
    assert 0.0 <= summary["reuse_ratio"] <= 1.0
    assert 0.0 <= summary["shared_prefix_fraction"] <= 1.0
