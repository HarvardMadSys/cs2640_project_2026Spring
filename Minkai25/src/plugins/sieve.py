import libcachesim as lcs
from collections import OrderedDict
from libcachesim import PluginCache, CommonCacheParams, Request


class StandaloneSIEVE:
    """
    SIEVE cache eviction algorithm (NSDI '24).

    Data structure: one OrderedDict (oldest→newest, i.e. tail→head) whose
    values hold the per-object visited bit, plus a single 'hand' pointer
    stored as an obj_id.

    On a cache hit  → set visited = True (lazy promotion, no move).
    On a cache miss → insert at head with visited = False.
    On eviction     → walk hand from its current position toward the head,
                      resetting visited bits along the way; evict the first
                      object whose visited bit is still False (quick demotion).
                      After eviction the hand advances one step toward the head.
                      When the hand falls off the head end it wraps to the tail.
    """

    def __init__(self, cache_size: int):
        self.cache_size = cache_size
        # OrderedDict: insertion order = oldest (tail) … newest (head).
        # Value = visited bit (bool).
        self.queue: OrderedDict[int, bool] = OrderedDict()
        # obj_id at the current hand position; None → start from tail on
        # next eviction (handles cold-start and post-remove invalidation).
        self.hand: int | None = None

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def cache_hit(self, req: Request) -> None:
        """Lazy promotion: just flip the visited bit, no queue reorder."""
        if req.obj_id in self.queue:
            self.queue[req.obj_id] = True

    def cache_miss(self, req: Request) -> None:
        """Insert new object at the head (newest end) with visited = False."""
        self.queue[req.obj_id] = False
        self.queue.move_to_end(req.obj_id)   # newest position

    def cache_evict(self) -> int | None:
        """
        Walk the hand from tail→head (index 0 → n-1 in key order).
        Reset visited bits on 'survived' objects; evict the first unvisited
        one. The hand then points to the slot just beyond (toward head) the
        evicted object, wrapping to the tail when it passes the head.

        Returns the evicted obj_id, or None if the cache is empty.
        """
        n = len(self.queue)
        if n == 0:
            return None

        # Snapshot of key order (oldest=0, newest=n-1).
        # We only delete one entry at the very end, so this list stays valid
        # throughout the scan.
        keys = list(self.queue.keys())

        # ── Locate the hand ──────────────────────────────────────────────
        if self.hand is None or self.hand not in self.queue:
            hand_idx = 0                        # cold start or invalidated → tail
        else:
            hand_idx = keys.index(self.hand)    # O(n) – acceptable for simulation

        # ── Scan toward head, resetting visited bits ─────────────────────
        # Worst case: all n objects are visited (one full wrap resets them
        # all), then the second pass evicts the tail → at most 2n steps.
        for _ in range(2 * n):
            obj_id = keys[hand_idx]
            if self.queue[obj_id]:              # visited → reset and skip
                self.queue[obj_id] = False
                hand_idx = (hand_idx + 1) % n   # wrap at head → tail
            else:                               # unvisited → evict
                # Advance hand one step past the evicted object toward head;
                # wrap to tail if we were already at the head.
                next_idx = (hand_idx + 1) % n
                # If n==1 the only slot is gone; reset hand to None.
                self.hand = keys[next_idx] if next_idx != hand_idx else None
                del self.queue[obj_id]
                return obj_id

        return None     # unreachable under correct usage

    def cache_remove(self, obj_id: int) -> bool:
        """Explicit removal (e.g. invalidation). Invalidates hand if needed."""
        if obj_id in self.queue:
            if self.hand == obj_id:
                self.hand = None    # will be re-anchored at tail on next evict
            del self.queue[obj_id]
            return True
        return False


# ──────────────────────────────────────────────────────────────────────────────
# libcachesim plugin hooks
# ──────────────────────────────────────────────────────────────────────────────

def init_hook(common_cache_params: CommonCacheParams) -> StandaloneSIEVE:
    return StandaloneSIEVE(cache_size=common_cache_params.cache_size)


def hit_hook(cache: StandaloneSIEVE, request: Request) -> None:
    cache.cache_hit(request)


def miss_hook(cache: StandaloneSIEVE, request: Request) -> None:
    cache.cache_miss(request)


def eviction_hook(cache: StandaloneSIEVE, request: Request) -> int:
    evicted_id = None
    while evicted_id is None:          # spin until a valid victim is found
        evicted_id = cache.cache_evict()
    return evicted_id


def remove_hook(cache: StandaloneSIEVE, obj_id: int) -> None:
    cache.cache_remove(obj_id)


def free_hook(cache: StandaloneSIEVE) -> None:
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
