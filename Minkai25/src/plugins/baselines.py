"""
Baseline cache replacement policies wired to plugins/sim.py:

  - FIFO         : insert-order eviction
  - LRU          : recency
  - S3FIFO       : Yang et al. SOSP '23 (small/main/ghost queues; freq counter)
  - SIEVE        : Zhang et al. NSDI '24 (single FIFO + visited bit + hand)

These are reference implementations used by experiments/run_baselines.py and
imported by the attack-idea modules (admission_stump.py, learned_promotion.py,
exp3_sizer.py) for direct comparison.

NB: S3FIFO/SIEVE are deliberately rewritten here — the existing plugins/s3fifo.py
and plugins/sieve.py use libcachesim primitives that aren't available in this
environment. The behaviour is intended to match the published algorithm; we
sanity-check by ensuring miss-ratio ordering vs FIFO/LRU is consistent with the
results in the S3-FIFO paper (S3FIFO ≤ LRU ≤ FIFO at small cache fractions on
cluster52).
"""

from __future__ import annotations

from collections import OrderedDict, deque
from typing import Optional

from sim import Cache, Request, admit, discard


# ─── FIFO ────────────────────────────────────────────────────────────────────

class FIFO(Cache):
    def __init__(self, cache_size: int):
        super().__init__(cache_size)
        self.queue: deque[int] = deque()

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        self.queue.append(req.obj_id)
        admit(self, req)

    def on_evict(self, req: Request) -> int:
        return self.queue.popleft()

    def on_remove(self, obj_id: int) -> None:
        # Lazy: queue may still hold this id; we tolerate stale entries.
        pass


# ─── LRU ─────────────────────────────────────────────────────────────────────

class LRU(Cache):
    def __init__(self, cache_size: int):
        super().__init__(cache_size)
        self.order: OrderedDict[int, None] = OrderedDict()

    def on_hit(self, req: Request) -> None:
        self.order.move_to_end(req.obj_id)

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        self.order[req.obj_id] = None
        admit(self, req)

    def on_evict(self, req: Request) -> int:
        oid, _ = self.order.popitem(last=False)
        return oid

    def on_remove(self, obj_id: int) -> None:
        self.order.pop(obj_id, None)


# ─── S3FIFO ──────────────────────────────────────────────────────────────────
#
# Three queues:
#   S (small) — admission queue, default 10% of cache
#   M (main)  — popular-object queue, default 90% of cache
#   G (ghost) — id-only history of objects evicted from S without promotion,
#               sized to |M| so it captures a full main-queue "miss memory"
#
# Per-object 2-bit frequency counter ∈ {0,1,2,3}.
#   - On hit: counter = min(3, counter + 1)
#   - On S→evict: if counter ≥ 1 → promote to M (with counter reset)
#                 else            → evict; record id in G
#   - On M→evict: if counter ≥ 1 → reinsert at M head with counter--
#                 else            → evict outright
#   - On miss: if oid ∈ G → insert directly to M (treat as popular)
#              else        → insert to S
#
# This is the "quick-demotion + lazy-promotion" combo from Yang et al. and the
# template that attack ideas #1 and #4 modify.

class S3FIFO(Cache):
    def __init__(self, cache_size: int, small_ratio: float = 0.1,
                 promote_threshold: int = 1):
        super().__init__(cache_size)
        self.S_cap = max(1, int(small_ratio * cache_size))
        self.M_cap = cache_size - self.S_cap
        self.G_cap = self.M_cap  # ghost size per Yang et al.
        self.promote_threshold = promote_threshold

        self.S: deque[int] = deque()
        self.M: deque[int] = deque()
        self.G: deque[int] = deque()
        self.G_set: set[int] = set()
        self.in_S: set[int] = set()
        self.in_M: set[int] = set()
        self.freq: dict[int, int] = {}

    # ── helpers ─────────────────────────────────────────────────────────────

    def _ghost_push(self, oid: int) -> None:
        if oid in self.G_set:
            return
        if len(self.G) >= self.G_cap and self.G:
            old = self.G.popleft()
            self.G_set.discard(old)
        self.G.append(oid)
        self.G_set.add(oid)

    def _ghost_pop(self, oid: int) -> bool:
        if oid not in self.G_set:
            return False
        # leave deque entry; G_set tracks membership truth.
        self.G_set.discard(oid)
        return True

    def _evict_M_one(self) -> Optional[int]:
        # walk M from head, reinserting freq≥1 with decrement, until we find
        # a victim. Bounded by 2|M|.
        for _ in range(2 * len(self.M) + 1):
            if not self.M:
                return None
            oid = self.M.popleft()
            if oid not in self.in_M:
                continue  # stale
            f = self.freq.get(oid, 0)
            if f >= 1:
                self.freq[oid] = f - 1
                self.M.append(oid)
            else:
                self.in_M.discard(oid)
                return oid
        return None

    def _evict_S_one(self) -> Optional[int]:
        # walk S; if accessed (freq≥promote_threshold) promote to M, else evict.
        # promotions can fill M past M_cap; we'll trim M afterwards.
        while self.S:
            oid = self.S.popleft()
            if oid not in self.in_S:
                continue  # stale
            self.in_S.discard(oid)
            f = self.freq.get(oid, 0)
            if f >= self.promote_threshold:
                self.freq[oid] = 0
                self.in_M.add(oid)
                self.M.append(oid)
            else:
                self._ghost_push(oid)
                self.freq.pop(oid, None)
                return oid
        return None

    # ── hooks ───────────────────────────────────────────────────────────────

    def on_hit(self, req: Request) -> None:
        oid = req.obj_id
        self.freq[oid] = min(3, self.freq.get(oid, 0) + 1)

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        oid = req.obj_id
        if self._ghost_pop(oid):
            self.M.append(oid)
            self.in_M.add(oid)
        else:
            self.S.append(oid)
            self.in_S.add(oid)
        self.freq[oid] = 0
        admit(self, req)

    def on_evict(self, req: Request) -> int:
        # If M is over capacity, drain M; otherwise drain S.
        # In both cases, eviction may produce promotions which keep S non-empty
        # and push M over; we trim M after one S-eviction step.
        while True:
            m_over = len(self.in_M) > self.M_cap
            s_over = len(self.in_S) > self.S_cap

            if m_over:
                v = self._evict_M_one()
                if v is not None:
                    return v
            elif s_over:
                v = self._evict_S_one()
                if v is not None:
                    return v
                # if no evict (all promoted), loop and check M again
            else:
                # cache is over total but neither queue is over — fall back to
                # whichever has objects. Prefer S (quick-demotion).
                if self.in_S:
                    v = self._evict_S_one()
                    if v is not None:
                        return v
                if self.in_M:
                    v = self._evict_M_one()
                    if v is not None:
                        return v
                return None

    def on_remove(self, obj_id: int) -> None:
        self.in_S.discard(obj_id)
        self.in_M.discard(obj_id)
        self.freq.pop(obj_id, None)
        # deque cleanup is lazy via "stale" checks.


# ─── SIEVE ───────────────────────────────────────────────────────────────────
#
# Single FIFO; per-object visited bit; a "hand" pointer.
# On hit: visited = True (no reorder).
# On miss: insert at head with visited = False.
# On evict: walk hand from current position toward head, resetting visited bits
#   on objects with visited=True; first visited=False = victim. Hand advances
#   past the victim (wraps to tail).

class SIEVE(Cache):
    def __init__(self, cache_size: int):
        super().__init__(cache_size)
        self.queue: OrderedDict[int, bool] = OrderedDict()  # oldest→newest
        self.hand: Optional[int] = None

    def on_hit(self, req: Request) -> None:
        if req.obj_id in self.queue:
            self.queue[req.obj_id] = True

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        self.queue[req.obj_id] = False
        admit(self, req)

    def on_evict(self, req: Request) -> int:
        keys = list(self.queue.keys())
        n = len(keys)
        if n == 0:
            return None  # type: ignore[return-value]
        if self.hand is None or self.hand not in self.queue:
            idx = 0
        else:
            idx = keys.index(self.hand)
        for _ in range(2 * n + 1):
            oid = keys[idx]
            if self.queue[oid]:
                self.queue[oid] = False
                idx = (idx + 1) % n
            else:
                nxt = (idx + 1) % n
                self.hand = keys[nxt] if nxt != idx and n > 1 else None
                del self.queue[oid]
                return oid
        return keys[0]

    def on_remove(self, obj_id: int) -> None:
        self.queue.pop(obj_id, None)
        if self.hand == obj_id:
            self.hand = None


# ─── ARC ─────────────────────────────────────────────────────────────────────
#
# Megiddo & Modha FAST '03. Adaptive partition between recency (T1) and
# frequency (T2) using two ghost lists (B1, B2). Self-tuning |T1| via the
# parameter p ∈ [0, c].
#
#   - Hit in T1: move to T2 MRU.
#   - Hit in T2: move to T2 MRU.
#   - Miss with oid ∈ B1: p ← min(c, p + max(1, |B2|/|B1|));
#                         REPLACE; insert at T2 MRU; remove from B1.
#   - Miss with oid ∈ B2: p ← max(0, p - max(1, |B1|/|B2|));
#                         REPLACE; insert at T2 MRU; remove from B2.
#   - Pure miss: if |T1|+|B1| == c → drop LRU of B1 (or T1 if B1 empty)
#                else if |T1|+|T2|+|B1|+|B2| ≥ c → drop LRU of B2
#                REPLACE; insert at T1 MRU.
#   - REPLACE: if |T1| ≥ max(1, p) → evict LRU of T1 to MRU of B1
#              else                → evict LRU of T2 to MRU of B2.

class ARC(Cache):
    def __init__(self, cache_size: int):
        super().__init__(cache_size)
        self.c = cache_size
        self.p = 0  # target |T1|
        self.T1: OrderedDict[int, None] = OrderedDict()  # recency
        self.T2: OrderedDict[int, None] = OrderedDict()  # frequency
        self.B1: OrderedDict[int, None] = OrderedDict()  # ghost(T1)
        self.B2: OrderedDict[int, None] = OrderedDict()  # ghost(T2)
        # Pending eviction victim from REPLACE; consumed by on_evict.
        self._pending_victim: Optional[int] = None

    def _replace(self, oid_in_B2: bool) -> Optional[int]:
        """Evict from T1 → B1 or T2 → B2 per ARC's REPLACE rule. Return
        the evicted obj_id (the one leaving the *cache*; ghost transfer
        is internal)."""
        if self.T1 and (len(self.T1) > self.p or
                        (oid_in_B2 and len(self.T1) == self.p)):
            victim, _ = self.T1.popitem(last=False)
            self.B1[victim] = None
        elif self.T2:
            victim, _ = self.T2.popitem(last=False)
            self.B2[victim] = None
        else:
            return None
        return victim

    def on_hit(self, req: Request) -> None:
        oid = req.obj_id
        if oid in self.T1:
            del self.T1[oid]
            self.T2[oid] = None
        elif oid in self.T2:
            del self.T2[oid]
            self.T2[oid] = None  # MRU

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        oid = req.obj_id
        c = self.c
        in_B1 = oid in self.B1
        in_B2 = oid in self.B2

        if in_B1:
            # case II: hit in B1 → grow T1
            delta = max(1, len(self.B2) // max(1, len(self.B1)))
            self.p = min(c, self.p + delta)
            victim = self._replace(oid_in_B2=False)
            del self.B1[oid]
            self.T2[oid] = None
            if victim is not None:
                self._pending_victim = victim
            admit(self, req)
        elif in_B2:
            # case III: hit in B2 → shrink T1
            delta = max(1, len(self.B1) // max(1, len(self.B2)))
            self.p = max(0, self.p - delta)
            victim = self._replace(oid_in_B2=True)
            del self.B2[oid]
            self.T2[oid] = None
            if victim is not None:
                self._pending_victim = victim
            admit(self, req)
        else:
            # case IV: pure miss
            if len(self.T1) + len(self.B1) == c:
                if len(self.T1) < c and self.B1:
                    self.B1.popitem(last=False)
                    victim = self._replace(oid_in_B2=False)
                    if victim is not None:
                        self._pending_victim = victim
                elif self.T1:
                    self.T1.popitem(last=False)  # leaves cache without ghost
                    self._pending_victim = None  # evicted directly below
            elif (len(self.T1) + len(self.B1) + len(self.T2) + len(self.B2)) >= c:
                if (len(self.T1) + len(self.B1) + len(self.T2) + len(self.B2)) == 2 * c \
                        and self.B2:
                    self.B2.popitem(last=False)
                victim = self._replace(oid_in_B2=False)
                if victim is not None:
                    self._pending_victim = victim
            self.T1[oid] = None
            admit(self, req)

    def on_evict(self, req: Request) -> int:
        if self._pending_victim is not None:
            v = self._pending_victim
            self._pending_victim = None
            return v
        # Fallback: harness asks for an evict but we didn't queue one
        # (rare — usually means the cache was below capacity and grew past).
        # Evict per REPLACE rule.
        v = self._replace(oid_in_B2=False)
        return v if v is not None else next(iter(self.T1), 0)

    def on_remove(self, obj_id: int) -> None:
        for store in (self.T1, self.T2, self.B1, self.B2):
            store.pop(obj_id, None)


# ─── 2Q (simplified TwoQ) ────────────────────────────────────────────────────
#
# Johnson & Shasha VLDB '94. Two-queue admission filter:
#   - A1in: small recency FIFO (~25% of cache)
#   - Am:   main LRU (~75% of cache)
#   - A1out: ghost FIFO sized to ~50% of cache.
# On miss: if oid ∈ A1out, promote directly to Am MRU; else insert to A1in.
# On hit in A1in: leave (will eventually time out into A1out or Am).
#   (We use the simplified rule from the paper: hit in A1in stays in A1in.)
# On hit in Am: move to MRU.

class TwoQ(Cache):
    def __init__(self, cache_size: int, kin_ratio: float = 0.25,
                 kout_ratio: float = 0.5):
        super().__init__(cache_size)
        self.A1in_cap = max(1, int(kin_ratio * cache_size))
        self.Am_cap = cache_size - self.A1in_cap
        self.A1out_cap = max(1, int(kout_ratio * cache_size))
        self.A1in: OrderedDict[int, None] = OrderedDict()   # FIFO
        self.Am: OrderedDict[int, None] = OrderedDict()     # LRU
        self.A1out: OrderedDict[int, None] = OrderedDict()  # ghost
        self._pending_victim: Optional[int] = None

    def _push_ghost(self, oid: int) -> None:
        if oid in self.A1out:
            return
        if len(self.A1out) >= self.A1out_cap:
            self.A1out.popitem(last=False)
        self.A1out[oid] = None

    def on_hit(self, req: Request) -> None:
        oid = req.obj_id
        if oid in self.Am:
            del self.Am[oid]
            self.Am[oid] = None  # MRU
        # hit in A1in: leave it (per simplified 2Q)

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        oid = req.obj_id
        if oid in self.A1out:
            del self.A1out[oid]
            # Promote to Am MRU; evict from Am LRU if full.
            if len(self.Am) >= self.Am_cap:
                victim, _ = self.Am.popitem(last=False)
                self._pending_victim = victim
            self.Am[oid] = None
            admit(self, req)
        else:
            # Insert into A1in; evict A1in LRU into A1out if full.
            if len(self.A1in) >= self.A1in_cap:
                ghost, _ = self.A1in.popitem(last=False)
                self._push_ghost(ghost)
                self._pending_victim = ghost
            self.A1in[oid] = None
            admit(self, req)

    def on_evict(self, req: Request) -> int:
        if self._pending_victim is not None:
            v = self._pending_victim
            self._pending_victim = None
            return v
        # Fallback: prefer A1in tail.
        if self.A1in:
            v, _ = self.A1in.popitem(last=False)
            self._push_ghost(v)
            return v
        if self.Am:
            v, _ = self.Am.popitem(last=False)
            return v
        return 0

    def on_remove(self, obj_id: int) -> None:
        self.A1in.pop(obj_id, None)
        self.Am.pop(obj_id, None)


# ─── LFU ─────────────────────────────────────────────────────────────────────
#
# Classical least-frequently-used. Frequency counter per object, evict the
# minimum-frequency object on overflow. We use a simple freq dict + lazy-min;
# at eviction time, walk the in-cache set to find the min. O(N) per eviction,
# fine at our cache sizes. With ties broken by LRU.

class LFU(Cache):
    def __init__(self, cache_size: int):
        super().__init__(cache_size)
        self.freq: dict[int, int] = {}
        self.lru_order: OrderedDict[int, None] = OrderedDict()

    def on_hit(self, req: Request) -> None:
        oid = req.obj_id
        self.freq[oid] = self.freq.get(oid, 0) + 1
        self.lru_order.move_to_end(oid)

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        oid = req.obj_id
        self.freq[oid] = 1
        self.lru_order[oid] = None
        admit(self, req)

    def on_evict(self, req: Request) -> int:
        # Walk in LRU order, find min-freq item.
        min_freq = min(self.freq.values()) if self.freq else 0
        for oid in self.lru_order:
            if self.freq.get(oid, 0) == min_freq:
                del self.lru_order[oid]
                del self.freq[oid]
                return oid
        # Fallback shouldn't be reached.
        oid = next(iter(self.lru_order))
        del self.lru_order[oid]
        del self.freq[oid]
        return oid

    def on_remove(self, obj_id: int) -> None:
        self.freq.pop(obj_id, None)
        self.lru_order.pop(obj_id, None)


# ─── Belady (OPT) ────────────────────────────────────────────────────────────
#
# Optimal offline policy: on every eviction, evict the object whose next
# reference is *furthest in the future*. Requires `next_access_vtime` from the
# trace; we have it in oracleGeneral. This is the upper bound (= lower bound
# on miss ratio) any online policy can achieve. Useful as a ceiling reference
# in MR comparisons — "how close is V4 to the oracle?"
#
# Implementation: store (current next-access-vtime) per cached object,
# updated on each hit. On evict, scan in-cache for the maximum next-access-
# vtime and evict that one. O(N) per eviction; same asymptotic as LFU; fine.
# Sentinel handling: oracleGeneral uses INT64_MAX (or similar) for "no future
# reference" → those objects should be evicted first (next-access = ∞).

class Belady(Cache):
    NO_FUTURE_SENTINEL = 10**18

    def __init__(self, cache_size: int):
        super().__init__(cache_size)
        self.next_access: dict[int, int] = {}

    def on_hit(self, req: Request) -> None:
        # Update with this reference's next-access pointer.
        nxt = req.next_access_vtime
        if nxt is None or nxt < 0:
            nxt = self.NO_FUTURE_SENTINEL
        self.next_access[req.obj_id] = nxt

    def on_miss(self, req: Request) -> None:
        if req.obj_size > self.cache_size:
            return
        nxt = req.next_access_vtime
        if nxt is None or nxt < 0:
            nxt = self.NO_FUTURE_SENTINEL
        self.next_access[req.obj_id] = nxt
        admit(self, req)

    def on_evict(self, req: Request) -> int:
        # Evict the cached object with the furthest next-access time.
        if not self.next_access:
            return 0
        victim = max(self.next_access, key=self.next_access.get)
        del self.next_access[victim]
        return victim

    def on_remove(self, obj_id: int) -> None:
        self.next_access.pop(obj_id, None)


ALL_BASELINES = {
    "FIFO": FIFO,
    "LRU": LRU,
    "LFU": LFU,
    "S3FIFO": S3FIFO,
    "SIEVE": SIEVE,
    "ARC": ARC,
    "TwoQ": TwoQ,
    "Belady": Belady,
}
