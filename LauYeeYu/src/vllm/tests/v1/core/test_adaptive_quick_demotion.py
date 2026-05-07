import pytest

from vllm.v1.core.free_block_manager import (
    RandomQuickDemotionGhostAdaptiveBinPropFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveBinAimdFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveBinDirectFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveNormPropFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveNormAimdFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveNormDirectFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveHistPropFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveHistAimdFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveHistDirectFreeBlockManager,
)
from vllm.v1.core.kv_cache_utils import KVCacheBlock


VARIANTS = [
    RandomQuickDemotionGhostAdaptiveBinPropFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveBinAimdFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveBinDirectFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveNormPropFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveNormAimdFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveNormDirectFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveHistPropFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveHistAimdFreeBlockManager,
    RandomQuickDemotionGhostAdaptiveHistDirectFreeBlockManager,
]


def _make(cls, n_blocks=128):
    blocks = [KVCacheBlock(block_id=i) for i in range(n_blocks)]
    return cls(blocks)


def _drive(mgr, recencies):
    """Feed synthetic recencies directly to the observation path."""
    for r in recencies:
        mgr._observe_readmission(r)


@pytest.mark.cpu_test
@pytest.mark.parametrize("cls", VARIANTS)
def test_denominator_stays_bounded(cls):
    mgr = _make(cls, n_blocks=128)
    cache = mgr._cache_size_blocks
    # Mix of "very early" (≪ cache), "borderline" (~cache), "very late" (>>cache).
    import random
    random.seed(0)
    recencies = []
    for _ in range(2000):
        r = random.choice([1, cache // 4, cache, 2 * cache, 10 * cache])
        recencies.append(r)
    _drive(mgr, recencies)
    assert mgr.MIN_DENOMINATOR <= mgr.denominator <= mgr.MAX_DENOMINATOR
    assert mgr.ONE_HIT_LEAF_PENALTY == pytest.approx(1.0 / mgr.denominator)


@pytest.mark.cpu_test
@pytest.mark.parametrize("cls", VARIANTS)
def test_denominator_moves_both_directions(cls):
    """Sweep from all-early to all-late — D must both decrease and increase."""
    mgr = _make(cls, n_blocks=128)
    cache = mgr._cache_size_blocks

    # Phase 1: all readmits arrive very early (recency ≪ cache). Demotion is
    # over-aggressive in the eyes of the controller => D should go down.
    _drive(mgr, [1] * 1000)
    d_after_early = mgr.denominator

    # Phase 2: all readmits arrive very late (recency ≫ cache). Controller
    # should now push D back up.
    _drive(mgr, [10 * cache] * 2000)
    d_after_late = mgr.denominator

    # Some controllers with a deadband (AIMD) need to cross clearly — sweep
    # with a wider range if equilibrium never tipped. We also accept strict
    # inequality in either observation.
    assert d_after_early < mgr.INIT_DENOMINATOR, (
        f"{cls.__name__}: D did not decrease after 'all-early' phase "
        f"(init={mgr.INIT_DENOMINATOR}, after={d_after_early})")
    assert d_after_late > d_after_early, (
        f"{cls.__name__}: D did not increase after 'all-late' phase "
        f"(after_early={d_after_early}, after_late={d_after_late})")


@pytest.mark.cpu_test
@pytest.mark.parametrize("cls", VARIANTS)
def test_warmup_delays_updates(cls):
    mgr = _make(cls, n_blocks=128)
    # Fewer events than WARMUP_EVENTS — D must stay at init.
    _drive(mgr, [1] * (mgr.WARMUP_EVENTS - 1))
    assert mgr.denominator == pytest.approx(mgr.INIT_DENOMINATOR)
