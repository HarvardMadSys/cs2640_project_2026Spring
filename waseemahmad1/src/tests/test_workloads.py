from simulator.workload import (
    DEFAULT_WORKLOAD_SEEDS,
    generate_default_workloads,
    generate_long_context_workload,
    generate_multiturn_workload,
    generate_short_prompt_workload,
    make_default_workload_seeds,
    summarize_trace,
)


def test_short_workload_is_deterministic() -> None:
    t1 = generate_short_prompt_workload(num_requests=100, seed=11)
    t2 = generate_short_prompt_workload(num_requests=100, seed=11)
    assert t1 == t2
    assert len(t1) == 100


def test_long_context_workload_sanity() -> None:
    trace = generate_long_context_workload(num_requests=250, seed=5)
    assert len(trace) == 250
    assert any(k.startswith("long_anchor_") for k in trace)
    assert any(k.startswith("long_block_") for k in trace)


def test_multiturn_workload_sanity() -> None:
    trace = generate_multiturn_workload(num_turns=3, turn_length=20, seed=9)
    assert len(trace) == 60
    assert any(k.startswith("system_block_") for k in trace)
    assert any(k.startswith("turn_1_block_") for k in trace)


def test_default_workloads_have_expected_keys() -> None:
    workloads = generate_default_workloads(seed=0)
    assert set(workloads) == {"short_prompt", "long_context", "multiturn"}
    assert all(len(trace) > 0 for trace in workloads.values())


def test_default_workloads_match_direct_generator_defaults() -> None:
    workloads = generate_default_workloads(seed=None)

    assert workloads["short_prompt"] == generate_short_prompt_workload(
        seed=DEFAULT_WORKLOAD_SEEDS["short_prompt"]
    )
    assert workloads["long_context"] == generate_long_context_workload(
        seed=DEFAULT_WORKLOAD_SEEDS["long_context"]
    )
    assert workloads["multiturn"] == generate_multiturn_workload(
        seed=DEFAULT_WORKLOAD_SEEDS["multiturn"]
    )


def test_seed_mapping_is_deterministic() -> None:
    seeds_a = make_default_workload_seeds(seed=2026)
    seeds_b = make_default_workload_seeds(seed=2026)
    seeds_c = make_default_workload_seeds(seed=2027)

    assert seeds_a == seeds_b
    assert seeds_a != seeds_c


def test_summarize_trace_fields() -> None:
    summary = summarize_trace(["a", "b", "a", "a"])
    assert summary["total_accesses"] == 4
    assert summary["unique_blocks"] == 2
    assert summary["reuse_ratio"] == 0.5
    assert summary["avg_accesses_per_unique_block"] == 2.0
