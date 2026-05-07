from simulator.cache import KVCacheSimulator
from simulator.policies import LRUPolicy


def test_hit_miss_and_recomputation_accounting() -> None:
    sim = KVCacheSimulator(capacity=2, policy=LRUPolicy(), miss_recompute_cost=1)

    accesses = ["a", "b", "a", "c", "a"]
    outcomes = [sim.access(k) for k in accesses]

    assert outcomes == [False, False, True, False, True]
    assert sim.total_accesses == 5
    assert sim.hits == 2
    assert sim.misses == 3
    assert sim.recomputation_cost == 3


def test_summary_metrics_are_correct() -> None:
    sim = KVCacheSimulator(capacity=3, policy=LRUPolicy(), miss_recompute_cost=2)

    for key in ["x", "y", "x"]:
        sim.access(key)

    summary = sim.get_summary()
    assert summary["total_accesses"] == 3
    assert summary["hits"] == 1
    assert summary["misses"] == 2
    assert summary["hit_rate"] == 1 / 3
    assert summary["miss_rate"] == 2 / 3
    assert summary["recomputation_cost"] == 4
