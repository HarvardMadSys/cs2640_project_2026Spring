from simulator.runner import run_trace


def test_run_trace_sanity_invariants() -> None:
    result = run_trace(
        trace=["a", "b", "a", "c", "a"],
        policy_name="lru",
        capacity=2,
        workload_name="toy",
        miss_cost=1,
    )

    assert result.hits + result.misses == result.total_accesses
    assert 0.0 <= result.hit_rate <= 1.0
    assert 0.0 <= result.miss_rate <= 1.0
    assert result.recomputation_cost == result.misses


def test_run_trace_respects_miss_cost() -> None:
    result = run_trace(
        trace=["x", "y", "x"],
        policy_name="fifo",
        capacity=2,
        workload_name="toy",
        miss_cost=3,
    )
    assert result.recomputation_cost == result.misses * 3
