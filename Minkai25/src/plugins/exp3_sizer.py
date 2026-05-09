"""
Attack idea #4 (sub-option ii) from ATTACK_PLAN.md:

  EXP3 multi-armed-bandit pick of |S| (S3-FIFO's small-queue size).

Yang et al. (SOSP '23) flag the adaptive-sizing problem as an open question:
"tuning the adaptive algorithm is very challenging." Their own adaptive
S3-FIFO variant uses a hand-rolled feedback loop. We replace it with a
classic adversarial-bandit algorithm (EXP3) so that on streams with
non-stationary workloads the policy converges to (or tracks) the best fixed
arm w.r.t. miss ratio.

Design choice — DYNAMIC RESIZE (single S3FIFO instance):
  Per the task spec we pick the simpler of the two design options. We keep a
  single S3FIFO and mutate its S_cap / M_cap between epochs. The alternative
  (parallel S3FIFO instances per arm) costs K × cache_size memory and forces
  shadow-state simulation; not worth it for this study.

Epoch loop:
  1. Sample arm k ~ EXP3 distribution p (or argmax for deterministic mode).
  2. Set S_cap = arms[k] * cache_size, M_cap = cache_size − S_cap.
  3. If S_cap shrank, drain the *tail* of S using the same promote-vs-ghost
     rule the S3FIFO eviction path uses (so the bookkeeping stays consistent
     and we don't violate the harness invariant _used_bytes ≤ cache_size).
  4. Run epoch_len requests, count misses.
  5. Reward = -miss_ratio_in_epoch ∈ [-1, 0]. Importance-weighted estimator
     r̂ = reward / p[k], clipped to [-1, 1] for stability. Exponentiated
     weights update.

Public registry:
  ALGOS = {"S3FIFO+EXP3": ..., "S3FIFO+S0.02": ..., ...}

NB: the harness drives the cache via on_hit/on_miss/on_evict; there's no
per-request "tick" callback. We piggyback the epoch counter on those hooks.
Since every request hits exactly one of {on_hit, on_miss}, that's an exact
1:1 with `process_trace`'s loop.
"""

from __future__ import annotations

import math
import random
from typing import Optional

from sim import Request, _add_member, admit
from baselines import S3FIFO


ARMS: tuple[float, ...] = (0.02, 0.05, 0.10, 0.20, 0.30)


class S3FIFOExp3(S3FIFO):
    """S3FIFO with EXP3-tuned small_ratio.

    The bandit picks one of `ARMS` at the start of each epoch. Reward = the
    negative miss ratio observed during that epoch. Standard EXP3 update.
    """

    def __init__(
        self,
        cache_size: int,
        arms: tuple[float, ...] = ARMS,
        epoch: Optional[int] = None,
        expected_epochs: int = 100,
        seed: int = 0,
        deterministic_best: bool = False,
    ):
        # Bootstrap S3FIFO with the median arm so we have a sane starting state.
        super().__init__(cache_size, small_ratio=arms[len(arms) // 2])

        self.arms: tuple[float, ...] = tuple(arms)
        self.K: int = len(self.arms)
        self.epoch_len: int = epoch if epoch is not None else max(10_000, cache_size)
        # eta tuned per Auer et al. for the expected horizon T=expected_epochs.
        # If we run longer, eta is conservative (mild over-exploration); if
        # shorter, we slightly over-exploit. Either way it's a well-known
        # vanilla setting.
        T = max(2, expected_epochs)
        self.eta: float = math.sqrt(math.log(self.K) / (T * self.K))
        self.deterministic_best: bool = deterministic_best
        self._rng = random.Random(seed)

        self.weights: list[float] = [1.0] * self.K
        self.current_arm: int = len(arms) // 2  # match super().__init__ choice
        self.current_p: float = 1.0 / self.K

        # Epoch bookkeeping
        self._epoch_reqs: int = 0
        self._epoch_misses: int = 0
        self._epoch_idx: int = 0

        # Diagnostics that the harness/report consumer can inspect.
        self.history: list[dict] = []  # one entry per finished epoch

        # Start the first epoch with whatever the bootstrap arm was.
        self._configure_arm(self.current_arm, initial=True)

    # ── EXP3 mechanics ──────────────────────────────────────────────────────

    def _probabilities(self) -> list[float]:
        # Numerical stability: subtract max log-weight before exponentiating
        # would be tidier, but weights stay bounded thanks to clipped r̂, so
        # plain renormalisation is fine here.
        Z = sum(self.weights)
        if Z <= 0 or not math.isfinite(Z):
            return [1.0 / self.K] * self.K
        return [w / Z for w in self.weights]

    def _sample_arm(self) -> int:
        p = self._probabilities()
        if self.deterministic_best:
            best_k = max(range(self.K), key=lambda k: self.weights[k])
            self.current_p = p[best_k]
            return best_k
        u = self._rng.random()
        acc = 0.0
        for k, pk in enumerate(p):
            acc += pk
            if u <= acc:
                self.current_p = pk
                return k
        self.current_p = p[-1]
        return self.K - 1

    def _exp3_update(self, arm: int, reward: float) -> None:
        # Importance-weighted estimator with stability clip.
        p_k = max(self.current_p, 1e-9)
        r_hat = reward / p_k
        # Standard EXP3 stabiliser — clip to a constant range so a single
        # rare-arm draw with p≈0 doesn't blow up the weight.
        if r_hat > 1.0:
            r_hat = 1.0
        elif r_hat < -1.0:
            r_hat = -1.0
        self.weights[arm] *= math.exp(self.eta * r_hat)
        # Renormalise weights so they don't drift to zero/infinity over long
        # horizons. Probabilities are scale-invariant.
        max_w = max(self.weights)
        if max_w > 1e6 or max_w < 1e-6:
            scale = 1.0 / max_w if max_w > 0 else 1.0
            self.weights = [w * scale for w in self.weights]

    # ── arm switching: resize S/M boundary ──────────────────────────────────

    def _configure_arm(self, k: int, *, initial: bool = False) -> None:
        """Set S_cap / M_cap to arm k. Drain S tail if shrinking."""
        target_S_cap = max(1, int(self.arms[k] * self.cache_size))
        target_M_cap = max(1, self.cache_size - target_S_cap)
        # Ghost queue sized to M (Yang et al.). When we resize we don't bother
        # trimming G — its capacity bound is checked lazily on push.
        self.S_cap = target_S_cap
        self.M_cap = target_M_cap
        self.G_cap = target_M_cap
        self.current_arm = k
        if initial:
            return

        # If S is now over capacity, drain its tail (oldest entries) using the
        # same promote-vs-ghost rule on_evict uses. This may transiently push
        # M over capacity (via promotion); we then drain M too. Net result: we
        # respect both per-queue caps and the global cache_size bound.
        #
        # NB: we pop from S's *head* (the oldest end), matching `_evict_S_one`,
        # which is the conventional FIFO eviction direction. Spec said "drain
        # excess off the tail of S, treating each as if eviction-time arrived"
        # — we interpret "tail" as the eviction tail (= head of the deque,
        # which is the oldest = next-to-evict end of the FIFO queue).
        while len(self.in_S) > self.S_cap:
            v = self._evict_S_one()
            if v is not None:
                # Mirror what process_trace does after on_evict: remove from
                # the harness members so _used_bytes stays correct.
                self._discard_from_members(v)
            else:
                break  # all remaining objects got promoted; drop out
        while len(self.in_M) > self.M_cap:
            v = self._evict_M_one()
            if v is not None:
                self._discard_from_members(v)
            else:
                break

    def _discard_from_members(self, oid: int) -> None:
        """Mirror sim._remove_member without re-firing on_remove (we already
        cleaned bookkeeping inside _evict_S_one / _evict_M_one)."""
        if oid in self._members:
            self._members.discard(oid)
            sz = self._sizes.pop(oid, 0)
            self._used_bytes -= sz

    # ── epoch boundary hook ─────────────────────────────────────────────────

    def _tick(self, was_miss: bool) -> None:
        self._epoch_reqs += 1
        if was_miss:
            self._epoch_misses += 1
        if self._epoch_reqs >= self.epoch_len:
            self._end_epoch()

    def _end_epoch(self) -> None:
        miss_ratio = self._epoch_misses / max(1, self._epoch_reqs)
        reward = -miss_ratio  # ∈ [-1, 0]
        self._exp3_update(self.current_arm, reward)
        self.history.append({
            "epoch": self._epoch_idx,
            "arm": self.current_arm,
            "small_ratio": self.arms[self.current_arm],
            "p": self.current_p,
            "miss_ratio": miss_ratio,
            "weights": list(self.weights),
        })
        self._epoch_idx += 1
        self._epoch_reqs = 0
        self._epoch_misses = 0

        # Pick & install next arm.
        next_k = self._sample_arm()
        self._configure_arm(next_k)

    # ── Cache hooks ────────────────────────────────────────────────────────

    def on_hit(self, req: Request) -> None:
        super().on_hit(req)
        self._tick(was_miss=False)

    def on_miss(self, req: Request) -> None:
        super().on_miss(req)
        self._tick(was_miss=True)


# ── Public registry ─────────────────────────────────────────────────────────

def _fixed(small_ratio: float):
    return lambda sz: S3FIFO(sz, small_ratio=small_ratio)


ALGOS = {
    "S3FIFO+EXP3": lambda sz: S3FIFOExp3(sz),
}
for _r in ARMS:
    ALGOS[f"S3FIFO+S{_r:.2f}"] = _fixed(_r)
