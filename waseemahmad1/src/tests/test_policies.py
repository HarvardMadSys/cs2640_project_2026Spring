from simulator.policies import FIFOPolicy, LRUPolicy


def test_lru_eviction_order_updates_on_access() -> None:
    policy = LRUPolicy()
    policy.on_insert("a", 1)
    policy.on_insert("b", 2)
    policy.on_insert("c", 3)

    assert policy.choose_victim() == "a"

    policy.on_access("a", 4)
    assert policy.choose_victim() == "b"

    policy.on_evict("b")
    assert policy.choose_victim() == "c"


def test_fifo_eviction_order_ignores_access() -> None:
    policy = FIFOPolicy()
    policy.on_insert("a", 1)
    policy.on_insert("b", 2)
    policy.on_insert("c", 3)

    policy.on_access("a", 4)
    assert policy.choose_victim() == "a"

    policy.on_evict("a")
    assert policy.choose_victim() == "b"

    # Reinserting an existing key should not duplicate queue entries.
    policy.on_insert("b", 5)
    assert policy.choose_victim() == "b"
