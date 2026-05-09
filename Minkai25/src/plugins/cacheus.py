"""
CACHEUS C3 — CACHEUS(SR-LRU, CR-LFU)
======================================
Implementation of the CACHEUS framework from:
  "Learning Cache Replacement with CACHEUS" (FAST '21)
  Rodriguez et al., Florida International University

This file is structured as a libcachesim PluginCache, mirroring the
StandaloneS3FIFO template. Implement the five hooks at the bottom.

═══════════════════════════════════════════════════════════════
KEY DESIGN DECISIONS  (search "DESIGN:" to find each one)
═══════════════════════════════════════════════════════════════

DESIGN-1  Expert choice
    C3 uses SR-LRU and CR-LFU (not LRU/LFU like LeCaR).
    LRU cannot handle scans; LFU cannot handle churn.
    Alternatives: C1=CACHEUS(ARC,LFU), C2=CACHEUS(LIRS,LFU).

DESIGN-2  History size = N/2 per expert  (total N)
    Each expert's ghost history is capped at N/2 so combined
    history equals N — same overhead as ARC/LIRS.

DESIGN-3  Window size = cache size N
    Learning-rate gradient is computed every N requests.
    Larger windows → smoother but slower adaptation.

DESIGN-4  Initial learning rate — random in [1e-3, 1.0]
    Randomised to avoid consistently bad starts.
    A fixed value such as 0.45 (LeCaR default) works too but
    performs inconsistently across workloads (paper Fig. 4).

DESIGN-5  Discount rate = 1.0  (i.e. eliminated)
    LeCaR used a discount rate γ < 1. The paper found removing
    it did not appreciably hurt performance, so CACHEUS drops it.

DESIGN-6  Weight update — multiplicative regret minimisation
    w_A *= exp(-λ) on a miss whose evicted item came from A's
    history (meaning A was wrong). Weights are re-normalised
    after each update.

DESIGN-7  Learning-rate adaptation — stochastic hill-climbing
    Gradient sign (ΔHR / Δλ) determines direction of change.
    Magnitude scales with the previous step size (momentum-like).
    After 10 consecutive non-improving windows → random restart.

DESIGN-8  SR-LRU partition adaptation
    SR size grows when a new item evicted to history is seen again
    (SR too small) and shrinks when a demoted item from R is
    re-accessed while in SR (R too small). Delta is computed as
    max(1, opposite_count / own_count) to bound wild swings.

DESIGN-9  CR-LFU tie-breaking — MRU wins
    When several items share the minimum frequency, LFU would
    pick arbitrarily. CR-LFU picks the MRU among them, which
    "locks" low-freq items in cache and raises hit rate ~8.7%
    over pure LFU on churn workloads (paper §5).

DESIGN-10 Two-expert constraint
    The paper tested 3+ experts and found it significantly worse
    unless experts are orthogonal. SR-LRU and CR-LFU are
    deliberately complementary: one is recency-based, one
    frequency-based.
"""

import random
import math
from collections import OrderedDict
from libcachesim import PluginCache, CommonCacheParams, Request, FIFO
import libcachesim as lcs


# ─────────────────────────────────────────────────────────────────────────────
# CR-LFU  (Churn-Resistant LFU)
# ─────────────────────────────────────────────────────────────────────────────

class CRLFU:
    """
    CR-LFU: LFU with MRU tie-breaking for churn resistance.

    Data structures:
      • freq_map : obj_id → frequency
      • order    : OrderedDict acting as an insertion-order MRU tracker
                   (most-recently used at the *end*)
      • min_freq : tracked lazily for O(1) min lookup

    DESIGN-9: On eviction, among all items at min_freq, evict the
    one most recently used (MRU). This keeps the least-recently-
    touched items in cache longer during churn, generating hits.
    """

    def __init__(self, capacity: int):
        # DESIGN-2: capacity passed in as N/2
        self.capacity = capacity
        self.freq_map: dict[int, int] = {}          # obj_id → freq
        self.order: OrderedDict[int, None] = OrderedDict()  # MRU order
        self.freq_buckets: dict[int, OrderedDict] = {}      # freq → {obj_id}
        self.min_freq = 0

    # ── internal helpers ──────────────────────────────────────────────────────

    def _add_to_bucket(self, obj_id: int, freq: int):
        if freq not in self.freq_buckets:
            self.freq_buckets[freq] = OrderedDict()
        self.freq_buckets[freq][obj_id] = None
        self.order.move_to_end(obj_id)   # mark as MRU

    def _remove_from_bucket(self, obj_id: int, freq: int):
        bucket = self.freq_buckets.get(freq)
        if bucket and obj_id in bucket:
            del bucket[obj_id]
            if not bucket:
                del self.freq_buckets[freq]

    # ── public API ────────────────────────────────────────────────────────────

    def contains(self, obj_id: int) -> bool:
        return obj_id in self.freq_map

    def access(self, obj_id: int):
        """Record a hit: increment frequency and refresh MRU order."""
        freq = self.freq_map[obj_id]
        self._remove_from_bucket(obj_id, freq)
        new_freq = freq + 1
        self.freq_map[obj_id] = new_freq
        self._add_to_bucket(obj_id, new_freq)
        if freq == self.min_freq and freq not in self.freq_buckets:
            self.min_freq = new_freq

    def insert(self, obj_id: int):
        """Insert a new object (freq=1). Caller must evict first if full."""
        self.freq_map[obj_id] = 1
        self.order[obj_id] = None          # add to MRU tracker
        self._add_to_bucket(obj_id, 1)
        self.min_freq = 1

    def evict(self) -> int | None:
        """
        DESIGN-9: Among all items at min_freq, evict the MRU one.
        Returns the evicted obj_id or None if empty.
        """
        if not self.freq_map:
            return None
        bucket = self.freq_buckets.get(self.min_freq)
        if not bucket:
            return None

        # MRU item = last in the global order that is also in this bucket
        # Walk order in reverse (MRU end) to find the first bucket member.
        victim = None
        for oid in reversed(self.order):
            if oid in bucket:
                victim = oid
                break
        if victim is None:
            victim = next(iter(bucket))   # fallback (shouldn't happen)

        self._remove_from_bucket(victim, self.min_freq)
        del self.freq_map[victim]
        del self.order[victim]
        return victim

    def remove(self, obj_id: int) -> bool:
        if obj_id not in self.freq_map:
            return False
        freq = self.freq_map.pop(obj_id)
        self._remove_from_bucket(obj_id, freq)
        if obj_id in self.order:
            del self.order[obj_id]
        return True

    def __len__(self):
        return len(self.freq_map)


# ─────────────────────────────────────────────────────────────────────────────
# SR-LRU  (Scan-Resistant LRU)
# ─────────────────────────────────────────────────────────────────────────────

class SRLRU:
    """
    SR-LRU: two-partition LRU with adaptive scan resistance.

    Partitions:
      R  — items accessed more than once (reuse list)
      SR — single-access or recently demoted items (scan buffer)

    Only SR is evicted from. R items are demoted to SR over time.

    DESIGN-8: Partition sizes adapt reactively:
      • Hit on item in SR that was previously demoted from R
        → R is too small; shrink SR by δ = max(1, H_new / C_demoted)
      • Hit in history on item that was new when evicted
        → SR was too small; grow SR by δ = max(1, C_demoted / H_new)

    History H mirrors LeCaR/ARC ghost lists and is used to
    penalise this expert's weight inside CACHEUS.
    """

    def __init__(self, capacity: int, history_size: int):
        # DESIGN-2: history_size = N/2
        self.capacity = capacity
        self.history_size = history_size

        # R and SR stored as OrderedDicts (LRU = leftmost, MRU = rightmost)
        self.R: OrderedDict[int, None] = OrderedDict()
        self.SR: OrderedDict[int, None] = OrderedDict()
        self.H: OrderedDict[int, bool] = OrderedDict()   # obj_id → was_new

        # DESIGN-8: adaptive partition sizes
        # SR starts at full capacity; R starts empty.
        self.size_SR = capacity         # current target size for SR
        self.size_R = 0                 # = capacity - size_SR

        # Tags for adaptation
        self.is_new: set[int] = set()       # items inserted for the 1st time
        self.is_demoted: set[int] = set()   # items demoted from R to SR

        self.c_demoted = 0   # count of demoted items currently in cache
        self.h_new = 0       # count of new items currently in history

    # ── helpers ───────────────────────────────────────────────────────────────

    def _cache_size(self) -> int:
        return len(self.R) + len(self.SR)

    def _ensure_history_space(self):
        if len(self.H) >= self.history_size:
            evicted_oid, was_new = self.H.popitem(last=False)
            if was_new:
                self.h_new = max(0, self.h_new - 1)

    def _update_sizes(self, direction: int, delta: int):
        """direction: +1 grow SR, -1 shrink SR."""
        new_sr = self.size_SR + direction * delta
        new_sr = max(1, min(self.capacity - 1, new_sr))
        self.size_SR = new_sr
        self.size_R = self.capacity - new_sr

    def _evict_lru_sr(self):
        """Evict LRU of SR → move to history."""
        if not self.SR:
            return None
        victim, _ = self.SR.popitem(last=False)   # LRU end
        was_new = victim in self.is_new
        if victim in self.is_new:
            self.is_new.discard(victim)
        if victim in self.is_demoted:
            self.is_demoted.discard(victim)
            self.c_demoted = max(0, self.c_demoted - 1)

        self._ensure_history_space()
        self.H[victim] = was_new
        if was_new:
            self.h_new += 1
        return victim

    def _demote_lru_r(self):
        """Demote LRU of R to MRU of SR."""
        if not self.R:
            return
        victim, _ = self.R.popitem(last=False)
        self.SR[victim] = None
        self.SR.move_to_end(victim)
        self.is_demoted.add(victim)
        self.c_demoted += 1

    # ── public API ────────────────────────────────────────────────────────────

    def contains(self, obj_id: int) -> bool:
        return obj_id in self.R or obj_id in self.SR

    def in_history(self, obj_id: int) -> bool:
        return obj_id in self.H

    def access(self, obj_id: int):
        """Handle a cache hit."""
        if obj_id in self.SR:
            if obj_id in self.is_demoted:
                # DESIGN-8: demoted item reused → R was too small
                denom = max(1, self.c_demoted)
                delta = max(1, self.h_new // denom)
                self._update_sizes(-1, delta)   # shrink SR
                self.is_demoted.discard(obj_id)
                self.c_demoted = max(0, self.c_demoted - 1)
            del self.SR[obj_id]
            self.R[obj_id] = None
            self.R.move_to_end(obj_id)
        elif obj_id in self.R:
            self.R.move_to_end(obj_id)   # refresh MRU

    def insert(self, obj_id: int, from_history: bool = False):
        """
        Insert a cache miss. If from_history, promote to R.
        Otherwise insert into SR as a new item.
        Caller must evict until there is room.
        """
        if from_history:
            was_new = self.H.pop(obj_id, False)
            if was_new:
                self.h_new = max(0, self.h_new - 1)
            if obj_id in self.H:
                del self.H[obj_id]
            # DESIGN-8: was new when evicted → SR was too small
            if was_new:
                denom = max(1, self.h_new + 1)
                delta = max(1, self.c_demoted // denom)
                self._update_sizes(+1, delta)
            self.R[obj_id] = None
            self.R.move_to_end(obj_id)
        else:
            self.SR[obj_id] = None
            self.SR.move_to_end(obj_id)
            self.is_new.add(obj_id)

    def evict(self) -> int | None:
        """
        Keep partitions within their target sizes, then evict from SR.
        Returns the evicted obj_id or None if empty.
        """
        # Rebalance: demote excess R items into SR
        while len(self.R) > max(0, self.size_R):
            self._demote_lru_r()

        return self._evict_lru_sr()

    def remove(self, obj_id: int) -> bool:
        for store in (self.R, self.SR):
            if obj_id in store:
                del store[obj_id]
                self.is_new.discard(obj_id)
                self.is_demoted.discard(obj_id)
                return True
        return False

    def __len__(self):
        return len(self.R) + len(self.SR)


# ─────────────────────────────────────────────────────────────────────────────
# CACHEUS C3 — top-level algorithm
# ─────────────────────────────────────────────────────────────────────────────

class StandaloneCACHEUS:
    """
    CACHEUS(SR-LRU, CR-LFU) — Algorithm 1 from the paper.

    Maintains:
      • Two experts A (SR-LRU) and B (CR-LFU) sharing the cache
      • Per-expert ghost histories H_A and H_B of size N/2 each
      • Expert weights w_A, w_B ∈ (0,1) with w_A + w_B = 1
      • A learning rate λ adapted by stochastic hill-climbing

    On every miss the algorithm:
      1. Checks histories to update weights (DESIGN-6)
      2. Randomly selects an expert proportional to weights
      3. Evicts using that expert's strategy

    Every N requests (one window) the learning rate is updated
    using a gradient sign rule (DESIGN-7).
    """

    def __init__(
        self,
        cache_size: int = 1024,
        # ── DESIGN-3 ──────────────────────────────────────────
        window_size: int | None = None,    # None → use cache_size
        # ── DESIGN-4 ──────────────────────────────────────────
        init_lr: float | None = None,      # None → random in [1e-3, 1.0]
        # ── DESIGN-1 ──────────────────────────────────────────
        # Expert split: how much of the cache each expert "owns".
        # The paper doesn't mandate a fixed split; we give each 50%.
        expert_split: float = 0.5,
    ):
        self.cache_size = cache_size
        # DESIGN-3
        self.window_size = window_size if window_size is not None else cache_size

        size_a = max(1, int(cache_size * expert_split))
        size_b = cache_size - size_a

        # DESIGN-2: history = N/2 per expert
        hist_size = max(1, cache_size * 10) # experiment with larger history

        # DESIGN-1: experts
        self.expert_a = SRLRU(capacity=size_a, history_size=hist_size)
        self.expert_b = CRLFU(capacity=size_b)

        # Ghost histories for weight updates (separate from expert internals)
        # These track *which expert made the eviction decision*, not the
        # expert's own internal history used for scan-detection.
        # DESIGN-2: size N/2 each
        self.H_A: OrderedDict[int, None] = OrderedDict()   # evicted by A
        self.H_B: OrderedDict[int, None] = OrderedDict()   # evicted by B
        self.ghost_hist_size = hist_size

        # DESIGN-6: weights initialised equally
        self.w_A = 0.5
        self.w_B = 0.5

        # DESIGN-4: learning rate
        self.lr = init_lr if init_lr is not None else random.uniform(1e-3, 1.0)
        self.prev_lr = self.lr

        # DESIGN-7: hill-climbing state
        self.request_count = 0
        self.hit_count_window = 0        # hits in current window
        self.hit_count_prev_window = 0   # hits in previous window
        self.hr_current = 0.0
        self.hr_prev = 0.0
        self.unlearn_count = 0           # consecutive non-improving windows

        # All cached items (for membership test without querying both experts)
        self.in_cache: set[int] = set()

    # ── weight update (Algorithm 2) ───────────────────────────────────────────

    def _update_weight(self, obj_id: int):
        """DESIGN-6: penalise the expert whose eviction was wrong."""
        if obj_id in self.H_A:
            self.w_A *= math.exp(-self.lr)
        elif obj_id in self.H_B:
            self.w_B *= math.exp(-self.lr)
        # Normalise
        total = self.w_A + self.w_B
        if total == 0:
            self.w_A = self.w_B = 0.5
        else:
            self.w_A /= total
            self.w_B = 1.0 - self.w_A

    def _add_to_ghost(self, history: OrderedDict, obj_id: int):
        if len(history) >= self.ghost_hist_size:
            history.popitem(last=False)   # drop LRU ghost
        history[obj_id] = None

    # ── learning-rate update (Algorithm 3) ───────────────────────────────────

    def _update_lr(self):
        """DESIGN-7: stochastic hill-climbing with random restart."""
        delta_hr = self.hr_current - self.hr_prev
        delta_lr = self.lr - self.prev_lr

        if delta_lr != 0:
            sign = +1 if (delta_hr / delta_lr) > 0 else -1
            new_lr = self.lr + sign * abs(self.lr * delta_lr)
            new_lr = max(new_lr, 1e-3)
            self.prev_lr = self.lr
            self.lr = new_lr
            self.unlearn_count = 0
        else:
            if self.hr_current == 0 or delta_hr <= 0:
                self.unlearn_count += 1
            if self.unlearn_count >= 10:   # DESIGN-7: restart threshold
                self.unlearn_count = 0
                self.prev_lr = self.lr
                self.lr = random.uniform(1e-3, 1.0)

    # ── choose expert proportionally ─────────────────────────────────────────

    def _choose_expert(self) -> str:
        """DESIGN-10: stochastically choose A or B by current weights."""
        return "A" if random.random() < self.w_A else "B"

    # ── eviction candidates from each expert ─────────────────────────────────

    def _candidate_a(self) -> int | None:
        """Peek at SR-LRU's eviction candidate without evicting."""
        # SR-LRU evicts from LRU of SR (after rebalancing R)
        while len(self.expert_a.R) > max(0, self.expert_a.size_R):
            self.expert_a._demote_lru_r()
        return next(iter(self.expert_a.SR), None) if self.expert_a.SR else None

    def _candidate_b(self) -> int | None:
        """Peek at CR-LFU's eviction candidate without evicting."""
        # CR-LFU evicts MRU among min-freq items
        min_f = self.expert_b.min_freq
        bucket = self.expert_b.freq_buckets.get(min_f)
        if not bucket:
            return None
        for oid in reversed(self.expert_b.order):
            if oid in bucket:
                return oid
        return next(iter(bucket), None)

    # ── main hooks ────────────────────────────────────────────────────────────

    def cache_hit(self, req: Request):
        self.hit_count_window += 1
        obj_id = req.obj_id
        if obj_id in self.expert_a.R or obj_id in self.expert_a.SR:
            self.expert_a.access(obj_id)
        if self.expert_b.contains(obj_id):
            self.expert_b.access(obj_id)

    def cache_miss(self, req: Request):
        obj_id = req.obj_id
        # DESIGN-6: update weights based on history membership
        self._update_weight(obj_id)

        # Remove from ghost histories now that the item is being admitted
        self.H_A.pop(obj_id, None)
        self.H_B.pop(obj_id, None)

        from_hist_a = self.expert_a.in_history(obj_id)
        # Admit to the appropriate expert
        if from_hist_a:
            self.expert_a.insert(obj_id, from_history=True)
        else:
            self.expert_a.insert(obj_id, from_history=False)

        if not self.expert_b.contains(obj_id):
            self.expert_b.insert(obj_id)

        self.in_cache.add(obj_id)

    def cache_evict(self, req: Request) -> int | None:
        """
        Algorithm 1: if both experts agree, evict that item.
        Otherwise, use weighted random choice.
        """
        cand_a = self._candidate_a()
        cand_b = self._candidate_b()

        if cand_a is None and cand_b is None:
            return None

        if cand_a == cand_b and cand_a is not None:
            # Both agree — evict without updating histories
            victim = cand_a
            self.expert_a.evict()
            self.expert_b.remove(victim)
        else:
            action = self._choose_expert()
            if action == "A" and cand_a is not None:
                victim = self.expert_a.evict()
                if victim is not None:
                    self._add_to_ghost(self.H_A, victim)
                    self.expert_b.remove(victim)
            elif cand_b is not None:
                victim = self.expert_b.evict()
                if victim is not None:
                    self._add_to_ghost(self.H_B, victim)
                    self.expert_a.remove(victim)
            else:
                # fallback: use whichever has a candidate
                if cand_a is not None:
                    victim = self.expert_a.evict()
                    if victim is not None:
                        self._add_to_ghost(self.H_A, victim)
                        self.expert_b.remove(victim)
                else:
                    victim = self.expert_b.evict()
                    if victim is not None:
                        self._add_to_ghost(self.H_B, victim)
                        self.expert_a.remove(victim)

        if victim is not None:
            self.in_cache.discard(victim)

        # DESIGN-3: end of window → update learning rate
        self.request_count += 1
        if self.request_count % self.window_size == 0:
            total = self.window_size
            self.hr_prev = self.hr_current
            self.hr_current = self.hit_count_window / total if total > 0 else 0
            self.hit_count_prev_window = self.hit_count_window
            self.hit_count_window = 0
            self._update_lr()

        return victim

    def cache_remove(self, obj_id: int) -> bool:
        removed = self.expert_a.remove(obj_id)
        removed |= self.expert_b.remove(obj_id)
        self.in_cache.discard(obj_id)
        return removed


# ─────────────────────────────────────────────────────────────────────────────
# libcachesim Plugin Hooks
# ─────────────────────────────────────────────────────────────────────────────

def init_hook(common_cache_params: CommonCacheParams):
    return StandaloneCACHEUS(cache_size=common_cache_params.cache_size)


def hit_hook(cache, request: Request):
    cache.cache_hit(request)


def miss_hook(cache, request: Request):
    cache.cache_miss(request)


def eviction_hook(cache, request: Request):
    evicted_id = None
    while evicted_id is None:
        evicted_id = cache.cache_evict(request)
    return evicted_id


def remove_hook(cache, obj_id: int):
    cache.cache_remove(obj_id)


def free_hook(cache):
    pass


cache = PluginCache(
    cache_size=1024,
    cache_init_hook=init_hook,
    cache_hit_hook=hit_hook,
    cache_miss_hook=miss_hook,
    cache_eviction_hook=eviction_hook,
    cache_remove_hook=remove_hook,
    cache_free_hook=free_hook,
)


URI = "s3://cache-datasets/cache_dataset_oracleGeneral/2007_msr/msr_hm_0.oracleGeneral.zst"

# Open trace
reader = lcs.TraceReader(
    trace=URI,
    trace_type=lcs.TraceType.ORACLE_GENERAL_TRACE,
    reader_init_params=lcs.ReaderInitParam(ignore_obj_size=True),
)


req_miss_ratio, byte_miss_ratio = cache.process_trace(reader)
print(f"Final Miss Ratio: {req_miss_ratio:.6f} (requests), {byte_miss_ratio:.6f} (bytes)")
