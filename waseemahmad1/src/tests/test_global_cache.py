from simulator.global_cache import SharedGlobalKVCacheSimulator, run_shared_events


def test_cross_request_prefix_reuse_hits_in_shared_cache() -> None:
    events = [
        ("req_0", "shared_prefix_block_0", 3, True),
        ("req_1", "shared_prefix_block_0", 3, True),
    ]
    result = run_shared_events(events, policy_name="lru", capacity=4)

    assert result.total_accesses == 2
    assert result.hits == 1
    assert result.misses == 1


def test_adaptive_keeps_expensive_block_under_pressure() -> None:
    sim = SharedGlobalKVCacheSimulator(capacity=2, policy_name="adaptive")

    sim.access("req_0", "expensive_block", recompute_cost=5, shared_prefix=False)
    sim.access("req_0", "cheap_block", recompute_cost=1, shared_prefix=False)
    sim.access("req_0", "new_block", recompute_cost=1, shared_prefix=False)

    assert "expensive_block" in sim.entries
    assert "new_block" not in sim.entries
    assert sim.skipped_admissions >= 1


def test_adaptive_can_skip_low_value_admission() -> None:
    sim = SharedGlobalKVCacheSimulator(capacity=2, policy_name="adaptive", miss_window_size=4)

    sim.access("req_0", "high_cost_a", recompute_cost=5, shared_prefix=False)
    sim.access("req_1", "high_cost_b", recompute_cost=4, shared_prefix=False)
    sim.access("req_2", "low_cost_new", recompute_cost=1, shared_prefix=False)

    assert sim.skipped_admissions == 1
    assert "low_cost_new" not in sim.entries
    assert "high_cost_a" in sim.entries
    assert "high_cost_b" in sim.entries


def test_adaptive_prefers_retaining_shared_prefix_blocks() -> None:
    sim = SharedGlobalKVCacheSimulator(capacity=2, policy_name="adaptive")

    sim.access("req_0", "shared_prefix_block_0", recompute_cost=3, shared_prefix=True)
    sim.access("req_0", "cheap_block", recompute_cost=1, shared_prefix=False)
    sim.access("req_1", "shared_prefix_block_1", recompute_cost=3, shared_prefix=True)

    assert "cheap_block" not in sim.entries
    assert "shared_prefix_block_0" in sim.entries
    assert "shared_prefix_block_1" in sim.entries


def test_shared_result_invariants_hold() -> None:
    events = [
        ("req_0", "a", 1, False),
        ("req_0", "b", 1, False),
        ("req_1", "a", 1, False),
        ("req_2", "c", 2, False),
    ]
    result = run_shared_events(events, policy_name="fifo", capacity=2)

    assert result.hits + result.misses == result.total_accesses
    assert 0.0 <= result.hit_rate <= 1.0
    assert 0.0 <= result.miss_rate <= 1.0
    assert result.recomputation_cost >= result.misses
