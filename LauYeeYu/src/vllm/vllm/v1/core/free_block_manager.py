from abc import ABC, abstractmethod
from typing import List
from collections import deque, OrderedDict
import math
from dataclasses import dataclass

from vllm.v1.core.kv_cache_utils import (
    FreeKVCacheBlockQueue, KVCacheBlock, BlockHashWithGroupId,
    RadixTree, RadixTreeNode,
)


def get_compute_intensity(index: int) -> float:
    """
    Get compute intensity for a block at given index.
    This function will be monkey-patched during evaluation with realistic values.
    Default implementation returns index + 1.
    """
    return float(865.0 + 2.0 * index)



class FreeBlockManager(ABC):
    """
    Abstract base class for managing free KV cache blocks. This layer sits
    between the BlockPool and the underlying block storage/queue, allowing
    for different eviction policies (e.g. LRU, LFU, etc.).
    """

    @abstractmethod
    def get_free_blocks_n(self, n: int) -> List[KVCacheBlock]:
        """Get n free blocks."""
        pass

    @abstractmethod
    def get_all_free_blocks(self) -> List[KVCacheBlock]:
        """Get all free blocks."""
        pass

    @abstractmethod
    def remove(self, block: KVCacheBlock) -> None:
        """Remove a block from the manager."""
        pass

    @abstractmethod
    def add_n(self, blocks: List[KVCacheBlock]) -> None:
        """Add a list of blocks to the manager.

        Note: In some cases, say, LRU, the order is sensitive and should use
        the reversed order of the blocks.
        """
        pass

    @property
    @abstractmethod
    def num_free_blocks(self) -> int:
        """Get the current number of free blocks."""
        pass

    @abstractmethod
    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        """
        Record a request. Can be used by derived classes to track
        request metadata for informing their eviction policies.
        """
        pass


class LRUFreeBlockManager(FreeBlockManager):
    """
    LRU implementation of FreeBlockManager. Sits as a wrapper around
    FreeKVCacheBlockQueue to maintain exact behavior of previous
    LRU-based eviction.
    """

    def __init__(self, blocks: List[KVCacheBlock]):
        self.queue = FreeKVCacheBlockQueue(blocks)

    def get_free_blocks_n(self, n: int) -> List[KVCacheBlock]:
        return self.queue.popleft_n(n)

    def get_all_free_blocks(self) -> List[KVCacheBlock]:
        return self.queue.get_all_free_blocks()

    def remove(self, block: KVCacheBlock) -> None:
        self.queue.remove(block)

    def add_n(self, blocks: List[KVCacheBlock]) -> None:
        self.queue.append_n(blocks)

    @property
    def num_free_blocks(self) -> int:
        return self.queue.num_free_blocks

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # LRU doesn't necessarily need the request object at this point
        pass


class RadixTreeFreeBlockManager(FreeBlockManager):
    """
    The base class for different caching algorithms.

    In this class we have three data structures:
    1. A queue of free blocks that cannot be used in prefix cache (ie, empty
       or partial blocks).
    2. A radix tree of prefix cache block nodes
    3. A list of radix tree nodes that can be used for sampling

    Note: the blocks in the radix tree are not necessarily free blocks. They
    are just blocks that have been cached as prefix cache blocks.
    """
    CANONICAL_POSITION = 0.5

    def __init__(self, blocks: List[KVCacheBlock]):
        self.unhashed_blocks_queue = FreeKVCacheBlockQueue(blocks)
        self.radix_tree = RadixTree()
        # queue of cached blocks (with hashes) that are currently completely free (ref_cnt == 0)
        # We can evict these blocks and recycle them.
        self.free_blocks_queue_in_radix_tree: FreeKVCacheBlockQueue = FreeKVCacheBlockQueue([])
        self.hashed_free_block_map: dict[BlockHashWithGroupId, KVCacheBlock] = {}

        self.blocks_not_in_tree: dict[BlockHashWithGroupId, KVCacheBlock] = {}

        # initialize the number of free blocks
        self._num_free_blocks = self.unhashed_blocks_queue.num_free_blocks

    def _try_get_free_blocks_from_unhashed_blocks_queue(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from the free
        queue."""
        num = min(n, self.unhashed_blocks_queue.num_free_blocks)
        blocks = self.unhashed_blocks_queue.popleft_n(num)
        self._num_free_blocks -= len(blocks)
        return blocks

    def _try_get_free_blocks_from_blocks_not_in_tree(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from blocks not in tree."""
        blocks = []
        while self.blocks_not_in_tree and len(blocks) < n:
            _, b = self.blocks_not_in_tree.popitem()
            blocks.append(b)
            self.hashed_free_block_map.pop(b.block_hash, None)
        self._num_free_blocks -= len(blocks)
        return blocks

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from the free
        radix tree nodes."""
        raise NotImplementedError

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from the in-use
        radix tree nodes."""
        raise NotImplementedError

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Get free blocks from a radix tree node. This function is called after
        the underlying eviction policy have determined to prioritize evicting
        blocks in this node. This function will try to evict as many blocks as
        possible until max_blocks is reached and update the radix tree
        accordingly.

        This function will do the following:
            1. Evict as many evictable blocks (in-memory and
               ref_cnt == 0) as possible until max_blocks is reached;
            2. If the node is a leaf node, and all blocks are evicted,
               evict the node.
        """
        evicted_blocks = []
        evicted_block_hashes = set()

        # 1. Evict as many evictable blocks as possible
        for i, child_hash in enumerate(node.block_hashes):
            if len(evicted_blocks) == max_blocks:
                break

            block_to_evict = self.hashed_free_block_map.get(child_hash)
            # Only evict if it's currently free (ref_cnt == 0) and not currently being used
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                self.hashed_free_block_map.pop(block_to_evict.block_hash, None)
                self.free_blocks_queue_in_radix_tree.remove(block_to_evict)
                evicted_blocks.append(block_to_evict)
                evicted_block_hashes.add(child_hash)

        # 2. If the node is a leaf node, and all blocks are evicted, evict the node.
        if not node.children:
            all_evicted = True
            for i, child_hash in enumerate(node.block_hashes):
                # If a hash is still in the system (we haven't reset a block matching it)
                # Then it hasn't been completely evicted
                # In reality if any of its blocks aren't in evicted_blocks AND didn't get evicted previously:
                # We can check the node's hash against the block's current state.
                # Actually, an easier way is to just let the evict function handle the cleanup
                # if we have no children and all our blocks are no longer attached to active cache items.
                if child_hash not in evicted_block_hashes:
                    # A block is still valid in the node if it's either in the free map OR currently in-use
                    if self.hashed_free_block_map.get(child_hash) or node.in_use[i]:
                        all_evicted = False
                        break

            if all_evicted:
                self.radix_tree.evict(node)

        return evicted_blocks

    def _is_evictable(self, block: BlockHashWithGroupId) -> bool:
        """Check if a block is evictable."""
        return block in self.hashed_free_block_map

    def _get_canonical_block_for_radix_tree_node(
        self, node: RadixTreeNode, n: int | None = None,
    ) -> BlockHashWithGroupId | None:
        """Get the canonical block for a radix tree node.

        If n is provided, position the canonical search near the midpoint of
        the first min(n, node_size) blocks — roughly the blocks most likely
        to be actually evicted from this node in this call. Otherwise fall
        back to the whole-node CANONICAL_POSITION midpoint.
        """
        total = len(node.block_hashes)
        if total == 0:
            return None
        if n is not None:
            effective = min(n, total)
            start = int(effective * self.CANONICAL_POSITION)
        else:
            start = int(total * self.CANONICAL_POSITION)
        start = min(max(start, 0), total - 1)
        for i in sorted(range(total), key=lambda x: abs(x - start)):
            child_hash = node.block_hashes[i]
            if self._is_evictable(child_hash):
                return child_hash
        return None

    def _get_all_free_blocks_from_radix_tree(self) -> List[KVCacheBlock]:
        """Get all free blocks from the radix tree."""
        return self.free_blocks_queue_in_radix_tree.get_all_free_blocks()

    def get_free_blocks_n(self, n: int) -> List[KVCacheBlock]:
        """Get n free blocks.

        The order of preference for getting free blocks is:
            1. From the unhashed blocks queue
            2. From the blocks not in tree
            3. From the free radix tree nodes
            4. From the in-use radix tree nodes
        """
        # First, try to get free blocks from the free queue
        free_blocks = self._try_get_free_blocks_from_unhashed_blocks_queue(n)
        if len(free_blocks) == n:
            return free_blocks

        # Then, try to get free blocks from blocks not in tree
        free_blocks += self._try_get_free_blocks_from_blocks_not_in_tree(n - len(free_blocks))
        if len(free_blocks) == n:
            return free_blocks

        # Then, try to get free blocks from the free radix tree nodes
        free_blocks += self._try_get_free_blocks_from_free_radix_tree_nodes(n - len(free_blocks))
        if len(free_blocks) == n:
            return free_blocks

        # Finally, try to get free blocks from the in-use radix tree nodes
        free_blocks += self._try_get_free_blocks_from_in_use_radix_tree_nodes(n - len(free_blocks))
        return free_blocks

    def get_all_free_blocks(self) -> List[KVCacheBlock]:
        free_blocks = self.unhashed_blocks_queue.get_all_free_blocks()
        free_blocks += list(self.blocks_not_in_tree.values())
        free_blocks += self._get_all_free_blocks_from_radix_tree()
        return free_blocks

    def remove(self, block: KVCacheBlock) -> None:
        if block.block_hash is None:
            self.unhashed_blocks_queue.remove(block)
        else:
            if block.block_hash in self.blocks_not_in_tree:
                self.blocks_not_in_tree.pop(block.block_hash, None)
                self.hashed_free_block_map.pop(block.block_hash, None)
            else:
                self.free_blocks_queue_in_radix_tree.remove(block)
                self.hashed_free_block_map.pop(block.block_hash, None)
                self.radix_tree.touch(block.block_hash)
        self._num_free_blocks -= 1

    def add_n(self, blocks: List[KVCacheBlock]) -> None:
        unhashed = []
        hashed = []
        hashed_hashes = []
        for b in blocks:
            if b.block_hash is None:
                unhashed.append(b)
            else:
                self.hashed_free_block_map[b.block_hash] = b
                if b.block_hash not in self.radix_tree._node_map:
                    self.blocks_not_in_tree[b.block_hash] = b
                else:
                    hashed.append(b)
                    hashed_hashes.append(b.block_hash)

        self.unhashed_blocks_queue.append_n(unhashed)
        self.free_blocks_queue_in_radix_tree.append_n(hashed)
        if hashed_hashes:
            self.radix_tree.free(hashed_hashes)
        self._num_free_blocks += len(blocks)

    @property
    def num_free_blocks(self) -> int:
        return self._num_free_blocks

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        block_hashes = []
        finished = False
        newly_in_tree = []
        for b in blocks:
            if b.block_hash is None:
                finished = True
            else:
                if finished:
                    raise ValueError("Full blocks detected after unhashed blocks")
                block_hashes.append(b.block_hash)

                if hasattr(self, "blocks_not_in_tree") and b.block_hash in self.blocks_not_in_tree:
                    self.blocks_not_in_tree.pop(b.block_hash, None)
                    self.free_blocks_queue_in_radix_tree.append(b)
                    newly_in_tree.append(b.block_hash)

        if block_hashes:
            self.radix_tree.add_sequence(block_hashes)

        if newly_in_tree:
            self.radix_tree.free(newly_in_tree)

class LCDFreeBlockManager(RadixTreeFreeBlockManager):
    """Least Compute Density (LCD) Free Block Manager.

    This class implements an eviction policy inspired by LCD (Least Compute Density).
    LCD continuously models the probability that an object of a certain "age"
    will be accessed again before it is naturally evicted. It partitions objects
    into classes based on their past access intervals, calculates expected hit
    densities, and targets the objects with the lowest expected compute density
    for eviction.
    """
    MAX_AGE = 20000
    HIT_AGE_CLASSES = 16
    AGE_COARSENING_SHIFT = 10
    AGE_COARSENING_ERROR_TOLERANCE = 0.01
    ACCS_PER_RECONFIGURATION = 1 << 20
    EWMA_DECAY = 0.9

    EXPLORE_INVERSE_PROBABILITY = 32
    EXPLORE_BUDGET_FRACTION = 0.01

    @dataclass
    class Tag:
        timestamp: int
        last_hit_age: int
        last_last_hit_age: int
        explorer: bool
        index: int
        block_hash: BlockHashWithGroupId

    @dataclass
    class Class:
        hits: list[int]
        evictions: list[int]
        total_hits: int
        total_evictions: int
        hit_density: list[float]

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.tags: dict[BlockHashWithGroupId, LCDFreeBlockManager.Tag] = {}

        self.explorer_budget = len(blocks) * self.EXPLORE_BUDGET_FRACTION

        self.next_reconfiguration = self.ACCS_PER_RECONFIGURATION
        self.num_reconfigurations = 0
        self.ewma_num_objects = 0.0
        self.ewma_num_objects_mass = 0.0

        self.classes = [
            LCDFreeBlockManager.Class(
                hits=[0] * self.MAX_AGE,
                evictions=[0] * self.MAX_AGE,
                total_hits=0,
                total_evictions=0,
                hit_density=[1.0 * (c + 1) / (a + 1) for a in range(self.MAX_AGE)]
            )
            for c in range(self.HIT_AGE_CLASSES)
        ]
        self.timestamp = 0

    def reconfigure(self):
        """Periodically refresh the hit density models and adapt age coarse thresholds.

        This happens in the background every ACCS_PER_RECONFIGURATION block
        requests. It triggers EWMA decay on all accumulated hit/eviction counts,
        re-scales the age groupings, and finally recalculates expectations using
        `model_hit_density`.
        """
        for cl in self.classes:
            self.update_class(cl)
        self.adapt_age_coarsening()
        self.model_hit_density()

    def update_class(self, cl: 'LCDFreeBlockManager.Class'):
        """Apply Exponentially Weighted Moving Average (EWMA) decay to history."""
        cl.total_hits = 0
        cl.total_evictions = 0
        for age in range(self.MAX_AGE):
            cl.hits[age] = int(cl.hits[age] * self.EWMA_DECAY)
            cl.evictions[age] = int(cl.evictions[age] * self.EWMA_DECAY)
            cl.total_hits += cl.hits[age]
            cl.total_evictions += cl.evictions[age]

    def model_hit_density_probability(self):
        """Calculate the conditional hit probability for a block at each age.

        Walks backwards from MAX_AGE. `total_events` at age `a` counts all objects
        whose terminal event (hit or eviction) occurred at age >= a — i.e., objects
        that "survived" to at least age `a`. The hit density is then:

            hit_density[a] = P(future hit | survived to age a)
                           = cumulative_future_hits / survivors_at_age_a

        This is the preferred formula for uniform-size blocks (prefix cache), where
        the LHD density denominator (expected time in cache) is not needed and
        introduces a systematic bias against recently-accessed (young) objects.
        See `model_hit_density` for the original LHD formulation.
        """
        for cl in self.classes:
            total_events = cl.hits[self.MAX_AGE - 1] + cl.evictions[self.MAX_AGE - 1]
            total_hits = cl.hits[self.MAX_AGE - 1]

            for a in range(self.MAX_AGE - 2, -1, -1):
                total_hits += cl.hits[a]
                total_events += cl.hits[a] + cl.evictions[a]

                if total_events > 1e-5:
                    cl.hit_density[a] = total_hits / total_events
                else:
                    cl.hit_density[a] = 0.0

    def model_hit_density(self):
        """Calculate hit *density* for a block at each age (original LHD formula).

        This is a faithful port of lhd.cpp's `modelHitDensity()`. Instead of plain
        hit probability, it computes hits per unit of expected future cache residence
        time:

            hit_density[a] = future_hits / lifetime_unconditioned[a]

        where `lifetime_unconditioned[a]` = Σ_{a'>=a} events[a'] * (a' - a),
        i.e., the total future "slot-time" contributed by objects at age `a`.

        This maximizes hits-per-cache-slot-per-time and is the correct objective
        for variable-size object caches. For uniform-size prefix-cache blocks it
        introduces a bias that penalizes young (recently-accessed) objects because
        they have a larger expected future lifetime. Use `model_hit_density_probability`
        instead for the uniform-size case.
        """
        for cl in self.classes:
            total_events = cl.hits[self.MAX_AGE - 1] + cl.evictions[self.MAX_AGE - 1]
            total_hits = cl.hits[self.MAX_AGE - 1]
            lifetime_unconditioned = total_events

            for a in range(self.MAX_AGE - 2, -1, -1):
                total_hits += cl.hits[a]
                total_events += cl.hits[a] + cl.evictions[a]
                lifetime_unconditioned += total_events

                if total_events > 1e-5:
                    cl.hit_density[a] = total_hits / lifetime_unconditioned
                else:
                    cl.hit_density[a] = 0.0

    def adapt_age_coarsening(self):
        """Auto-tune the age coarse resolution shift dynamically.

        Age coarsening prevents age arrays from constantly overflowing `MAX_AGE`.
        This tracks the moving average of total objects cached and uses
        it to find an optimal power-of-two shift factor `delta`. If
        a shift is necessary, it maps the current probability distributions
        either tighter (compressing arrays) or wider (stretching arrays).
        """
        self.ewma_num_objects *= self.EWMA_DECAY
        self.ewma_num_objects_mass *= self.EWMA_DECAY

        self.ewma_num_objects += len(self.tags)
        self.ewma_num_objects_mass += 1.0

        num_objects = self.ewma_num_objects / self.ewma_num_objects_mass
        optimal_age_coarsening = num_objects / (self.AGE_COARSENING_ERROR_TOLERANCE * self.MAX_AGE)

        if self.num_reconfigurations == 5 or self.num_reconfigurations == 25:
            optimal_age_coarsening_log2 = 1
            while (1 << optimal_age_coarsening_log2) < optimal_age_coarsening:
                optimal_age_coarsening_log2 += 1

            delta = optimal_age_coarsening_log2 - self.AGE_COARSENING_SHIFT
            self.AGE_COARSENING_SHIFT = optimal_age_coarsening_log2

            self.ewma_num_objects *= 8
            self.ewma_num_objects_mass *= 8

            if delta < 0:
                for cl in self.classes:
                    for a in range(self.MAX_AGE >> (-delta), self.MAX_AGE - 1):
                        cl.hits[self.MAX_AGE - 1] += cl.hits[a]
                        cl.evictions[self.MAX_AGE - 1] += cl.evictions[a]
                    for a in range(self.MAX_AGE - 2, -1, -1):
                        idx = a >> (-delta)
                        cl.hits[a] = cl.hits[idx] // (1 << (-delta))
                        cl.evictions[a] = cl.evictions[idx] // (1 << (-delta))
            elif delta > 0:
                for cl in self.classes:
                    for a in range(self.MAX_AGE >> delta):
                        cl.hits[a] = cl.hits[a << delta]
                        cl.evictions[a] = cl.evictions[a << delta]
                        for i in range(1, 1 << delta):
                            idx = (a << delta) + i
                            if idx < self.MAX_AGE:
                                cl.hits[a] += cl.hits[idx]
                                cl.evictions[a] += cl.evictions[idx]
                    for a in range(self.MAX_AGE >> delta, self.MAX_AGE - 1):
                        cl.hits[a] = 0
                        cl.evictions[a] = 0

    def _hit_age_class(self, age: int) -> int:
        if age == 0:
            return self.HIT_AGE_CLASSES - 1
        log_val = 0
        while age < self.MAX_AGE and log_val < self.HIT_AGE_CLASSES - 1:
            age <<= 1
            log_val += 1
        return log_val

    def _get_age(self, tag: Tag) -> int:
        """Calculate the block's current logical age scaled by the coarsening factor.

        Age is measured logically by how many aggregate block requests
        have been handled since this block was last hit.
        """
        if not hasattr(self, 'timestamp'):
            self.timestamp = 0
        age = (self.timestamp - tag.timestamp) >> self.AGE_COARSENING_SHIFT
        if age >= self.MAX_AGE:
            return self.MAX_AGE - 1
        return age

    def _get_density(self, tag: Tag) -> float:
        """Fetch the expected hit density of a block from its assigned class bucket.

        If a block has reached its max age, it effectively has an unlimited probability
        of being evicted, dropping its expected density to -infinity.
        """
        age = self._get_age(tag)
        if age == self.MAX_AGE - 1:
            return float('-inf')

        hit_age_id = self._hit_age_class(tag.last_hit_age + tag.last_last_hit_age)
        cl = self.classes[hit_age_id]

        density = cl.hit_density[age]
        if tag.explorer:
            density += 1.0
        compute_intensity = get_compute_intensity(tag.index)
        return density * compute_intensity

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from the free
        radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_free_radix_tree_nodes)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from the in-use
        radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_in_use_radix_tree_nodes)

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        """Core eviction mechanism using candidate sampling.

        When space is required, we pull a randomized sample pool of 32 radix tree
        nodes. For each sampled node, we identify its core block, retrieve
        its Tag, compute its projected hit density score, and continually evict
        blocks belonging to the node with the lowest predicted hit utility
        until the target capacity `n` is reached.
        """
        evicted_blocks = []
        ASSOCIATIVITY = 128
        tried_nodes = set()
        score_cache = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    canonical_block = self._get_canonical_block_for_radix_tree_node(node, n)
                    score = float('-inf')

                    if canonical_block is not None and getattr(self, "tags", None) and canonical_block in self.tags:
                        tag = self.tags[canonical_block]
                        score = self._get_density(tag)
                    score_cache[id(node)] = score

                if score < victim_score:
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            blocks = self._get_free_blocks_from_radix_tree_nodes(victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        """Record hits against incoming active blocks and trigger lifecycle modeling.

        For every valid block hash encountered:
            1. If unseen, create a fresh tracking Tag for the block.
            2. If seen, deduce its `age`, hit its corresponding `last_hit_age`
               class array bucket, and push down its `last_last_hit_age`.
            3. Update the global logical timestamp per-hit.
            4. Count down to periodic density modeling reconfiguration.
        """
        super().record_request_blocks(blocks)

        for block_idx, b in enumerate(blocks):
            timestamp = self.timestamp
            self.timestamp += 1
            if b.block_hash is None:
                continue

            import random
            explore = random.randint(0, self.EXPLORE_INVERSE_PROBABILITY - 1) == 0

            block_hash = b.block_hash
            insert = block_hash not in self.tags
            if insert:
                tag = LCDFreeBlockManager.Tag(
                    timestamp=timestamp,
                    last_hit_age=0,
                    last_last_hit_age=self.MAX_AGE,
                    explorer=False,
                    index=block_idx,
                    block_hash=block_hash,
                )
                self.tags[block_hash] = tag
            else:
                tag = self.tags[block_hash]
                assert tag.index == block_idx
                assert block_hash == tag.block_hash
                age = self._get_age(tag)

                hit_age_id = self._hit_age_class(tag.last_hit_age + tag.last_last_hit_age)
                cl = self.classes[hit_age_id]
                cl.hits[age] += 1

                # Refund the budget if it was an active explorer getting cycled
                if tag.explorer:
                    self.explorer_budget += 1

                tag.last_last_hit_age = tag.last_hit_age
                tag.last_hit_age = age
                tag.timestamp = timestamp

            if explore and self.explorer_budget > 0 and self.num_reconfigurations < 50:
                tag.explorer = True
                self.explorer_budget -= 1
            else:
                tag.explorer = False

            self.next_reconfiguration -= 1
            if self.next_reconfiguration == 0:
                self.reconfigure()
                self.next_reconfiguration = self.ACCS_PER_RECONFIGURATION
                self.num_reconfigurations += 1


    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks from the radix tree and clean up their LHD metadata.

        This mimics LHD's `replaced(candidate_t id)` functionality. When a block is
        officially discarded from the radix tree, we calculate its final age, log
        the eviction against its assigned class bucket, and delete its tracking Tag
        to ensure the tags dictionary does not grow infinitely.
        """
        # Collect the hashes before super() potentially clears them out
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        # Perform the actual eviction
        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(node, max_blocks)

        # Clean up tags using the tracked hashes
        for child_hash in evictable_hashes:
            if child_hash in self.tags:
                tag = self.tags[child_hash]
                age = self._get_age(tag)
                hit_age_id = self._hit_age_class(tag.last_hit_age + tag.last_last_hit_age)
                cl = self.classes[hit_age_id]

                # Record the eviction stat
                cl.evictions[age] += 1

                # Refund budget if an explorer is forcefully evicted
                if tag.explorer:
                    self.explorer_budget += 1

                # Clean up tracking metadata
                del self.tags[child_hash]

        return evicted_blocks

class GDCFFreeBlockManager(RadixTreeFreeBlockManager):
    """Greedy Dual-Compute Frequency (GDCF) Free Block Manager.

    This class implements an eviction policy based on GDCF.
    It calculates priority as: L + (freq * cost),
    where L is the priority of the last evicted block.
    For evaluation, cost is represented by `index + 1`.
    """

    @dataclass
    class Tag:
        priority: float
        freq: int
        index: int  # Block position index for compute_intensity calculation

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.tags: dict[BlockHashWithGroupId, GDCFFreeBlockManager.Tag] = {}
        self.pri_last_evict = 0.0

    def _get_priority(self, tag: Tag) -> float:
        """Get the stored priority of a block."""
        return tag.priority

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from the free radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_free_radix_tree_nodes)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks or as much as possible from the in-use radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_in_use_radix_tree_nodes)

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        """Core eviction mechanism using candidate sampling."""
        evicted_blocks = []
        ASSOCIATIVITY = 32
        tried_nodes = set()
        score_cache = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    canonical_block = self._get_canonical_block_for_radix_tree_node(node, n)
                    score = float('inf')

                    if canonical_block is not None and getattr(self, "tags", None) and canonical_block in self.tags:
                        tag = self.tags[canonical_block]
                        score = self._get_priority(tag)
                    score_cache[id(node)] = score

                if score < victim_score:
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            blocks = self._get_free_blocks_from_radix_tree_nodes(victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                if victim_score != float('inf') and victim_score != float('-inf'):
                    self.pri_last_evict = victim_score
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        """Record frequency and update priorities on access.

        This mimics the reference GDSF_compute implementation where:
        - On insertion: priority = L + cost
        - On cache hit: priority = L + freq * sqrt(cost)
        """
        super().record_request_blocks(blocks)

        valid_block_idx = 0
        for _, b in enumerate(blocks):
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue

            block_idx = valid_block_idx
            valid_block_idx += 1

            block_hash = b.block_hash

            if block_hash not in self.tags:
                # New insertion: priority = L + cost (freq implicitly 1)
                compute_intensity = get_compute_intensity(block_idx)
                priority = self.pri_last_evict + compute_intensity
                self.tags[block_hash] = GDCFFreeBlockManager.Tag(
                    priority=priority,
                    freq=1,
                    index=block_idx,
                )
            else:
                # Cache hit: update index, increment freq and recalculate priority
                tag = self.tags[block_hash]
                tag.index = block_idx
                tag.freq += 1
                # Priority = L + freq * sqrt(cost)
                compute_intensity = get_compute_intensity(tag.index)
                tag.priority = self.pri_last_evict + tag.freq * math.sqrt(compute_intensity)

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks from the radix tree and clean up tags."""
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(node, max_blocks)

        for child_hash in evictable_hashes:
            if child_hash in self.tags:
                del self.tags[child_hash]

        return evicted_blocks

class LCDBlockFreeBlockManager(LCDFreeBlockManager):
    """Least Compute Density (LCD) Free Block Manager that samples blocks directly.

    Instead of sampling from RadixTree nodes, this implementation samples
    directly from all blocks uniformly to match simulator behavior more closely.
    """

    def _sample_and_evict_blocks_directly(self, n: int) -> List[KVCacheBlock]:
        import random
        evicted_blocks = []
        ASSOCIATIVITY = 32

        while n > 0:
            valid_hashes = []

            # Form candidate pool from hashed_free_block_map directly
            for h in self.hashed_free_block_map:
                node, idx = self.radix_tree._node_map.get(h, (None, -1))
                if node is not None and not node.in_use[idx]:
                    valid_hashes.append(h)

            if not valid_hashes:
                break

            num_to_sample = min(ASSOCIATIVITY, len(valid_hashes))
            sampled_hashes = random.sample(valid_hashes, num_to_sample)

            victim_hash = None
            victim_score = float('inf')

            for h in sampled_hashes:
                score = float('-inf')
                if h in self.tags:
                    tag = self.tags[h]
                    score = self._get_density(tag)

                if score < victim_score:
                    victim_score = score
                    victim_hash = h

            if victim_hash is None:
                victim_hash = sampled_hashes[0]

            node, idx = self.radix_tree._node_map[victim_hash]

            block_to_evict = self.hashed_free_block_map.pop(victim_hash)
            self.free_blocks_queue_in_radix_tree.remove(block_to_evict)

            if victim_hash in self.tags:
                tag = self.tags[victim_hash]
                age = self._get_age(tag)
                hit_age_id = self._hit_age_class(tag.last_hit_age + tag.last_last_hit_age)
                cl = self.classes[hit_age_id]
                cl.evictions[age] += 1
                if tag.explorer:
                    self.explorer_budget += 1
                del self.tags[victim_hash]

            if not node.children:
                all_evicted = True
                for child_hash in node.block_hashes:
                    if child_hash in self.hashed_free_block_map:
                        all_evicted = False
                        break
                if all_evicted:
                    self.radix_tree.evict(node)

            evicted_blocks.append(block_to_evict)
            n -= 1
            self._num_free_blocks -= 1

        return evicted_blocks

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        return self._sample_and_evict_blocks_directly(n)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        return []


class BeladyComputeFreeBlockManager(RadixTreeFreeBlockManager):
    """Belady Compute Free Block Manager.

    This class implements Belady's MIN algorithm for compute-aware caching.
    """

    @dataclass
    class Tag:
        index: int
        block_hash: BlockHashWithGroupId

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.tags: dict[BlockHashWithGroupId, BeladyComputeFreeBlockManager.Tag] = {}
        # Dictionary mapping block_hash to next access time (in block counts)
        # This will be updated by the evaluation script
        self.next_access_times: dict[BlockHashWithGroupId, int] = {}
        # Current time in block counts
        self.current_time = 0

    def _get_next_access_time(self, tag: Tag) -> int:
        """Get the next access time for a block.

        Returns:
            The next access time, or a very large number if never accessed again.
        """
        block_hash = tag.block_hash
        return self.next_access_times.get(block_hash, float('inf'))

    def _get_eviction_score(self, tag: Tag) -> float:
        """Calculate eviction score for a block.

        Higher score means higher priority for eviction.
        For BeladyCompute, we calculate the time until next access and divide by
        compute intensity. This favors evicting blocks that won't be accessed soon
        and have low recomputation cost.

        Score = (next_access_time - current_time) / compute_intensity

        This means:
        - Blocks accessed far in future = high time difference = high score (evict)
        - Blocks with low compute cost = low compute_intensity = high score (evict)
        - Blocks with high compute cost = high compute_intensity = low score (keep)
        """
        next_time = self._get_next_access_time(tag)

        # Calculate time until next access
        time_until_next_access = next_time - self.current_time

        # Get compute intensity based on block position
        compute_intensity = get_compute_intensity(tag.index)

        # Avoid division by zero
        if compute_intensity <= 0:
            compute_intensity = 1.0

        # Higher time / lower compute_intensity = higher eviction priority
        return float(time_until_next_access) / compute_intensity

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks from the free radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_free_radix_tree_nodes)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks from the in-use radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_in_use_radix_tree_nodes)

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        """Core eviction mechanism using candidate sampling.

        For Belady, we sample candidate nodes and evict blocks from the node
        containing the block with the furthest next access time.
        """
        evicted_blocks = []
        ASSOCIATIVITY = 32
        tried_nodes = set()
        score_cache = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('-inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    canonical_block = self._get_canonical_block_for_radix_tree_node(node, n)
                    score = float('-inf')

                    if canonical_block is not None and getattr(self, "tags", None) and canonical_block in self.tags:
                        tag = self.tags[canonical_block]
                        score = self._get_eviction_score(tag)
                    score_cache[id(node)] = score

                if score > victim_score:  # Higher score = evict first for Belady
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            blocks = self._get_free_blocks_from_radix_tree_nodes(victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        """Record blocks for a request and update current time."""
        super().record_request_blocks(blocks)

        valid_block_idx = 0
        for _, b in enumerate(blocks):
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue

            block_idx = valid_block_idx
            valid_block_idx += 1

            block_hash = b.block_hash
            if block_hash not in self.tags:
                self.tags[block_hash] = BeladyComputeFreeBlockManager.Tag(
                    index=block_idx,
                    block_hash=block_hash,
                )
            else:
                tag = self.tags[block_hash]
                tag.index = block_idx

            # Increment time for each block processed
            self.current_time += 1

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks from the radix tree and clean up tags."""
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(node, max_blocks)

        for child_hash in evictable_hashes:
            if child_hash in self.tags:
                del self.tags[child_hash]
            # Clean up next_access_times to avoid memory bloat
            if child_hash in self.next_access_times:
                del self.next_access_times[child_hash]

        return evicted_blocks


class RandomDecayFreeBlockManager(RadixTreeFreeBlockManager):
    """Random (online heuristic) Free Block Manager.

    This class implements an online eviction policy that approximates recency and frequency.
    It calculates an eviction score as: (number of accesses) / (blocks since last access) * compute_intensity

    This is designed to compare against LCD to evaluate its effectiveness. Unlike LCD which uses
    sophisticated age-based class modeling, this uses a simple access count / recency heuristic.
    """

    # Decay constant for temporal decay of access counts
    # Higher value = slower decay (more weight on history)
    # Lower value = faster decay (more weight on recent behavior)
    DECAY_CONSTANT = 20000

    @dataclass
    class Tag:
        access_count: float  # Number of times this block has been accessed (with decay)
        last_access_time: int  # Time (in block counts) when last accessed
        index: int  # Position index for compute intensity
        block_hash: BlockHashWithGroupId

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.tags: dict[BlockHashWithGroupId, RandomDecayFreeBlockManager.Tag] = {}
        # Global time counter in block counts
        self.current_time = 0

    def _get_eviction_score(self, tag: Tag) -> float:
        """Calculate eviction score for a block.

        Improvement: Temporal decay applied to access counts to prevent old
        popular blocks from staying forever. Decay is applied both during
        access and during eviction scoring for consistency.

        Lower score means higher priority for eviction.
        Score = (effective_access_count / recency) * compute_intensity
        """
        recency = max(1, self.current_time - tag.last_access_time)

        # Apply decay to access count based on time since last access
        decay_factor = math.exp(-recency / self.DECAY_CONSTANT)
        score = tag.access_count * decay_factor

        # Weight by compute intensity
        compute_intensity = get_compute_intensity(tag.index)

        return score * compute_intensity

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks from the free radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_free_radix_tree_nodes)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks from the in-use radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_in_use_radix_tree_nodes)

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        """Core eviction mechanism using candidate sampling.

        Sample radix tree nodes and evict blocks from the node with the lowest score.
        """
        evicted_blocks = []
        ASSOCIATIVITY = 128
        tried_nodes = set()
        score_cache = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    canonical_block = self._get_canonical_block_for_radix_tree_node(node, n)
                    score = float('inf')

                    if canonical_block is not None and getattr(self, "tags", None) and canonical_block in self.tags:
                        tag = self.tags[canonical_block]
                        score = self._get_eviction_score(tag)
                    score_cache[id(node)] = score

                if score < victim_score:  # Lower score = evict first
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            blocks = self._get_free_blocks_from_radix_tree_nodes(victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        """Record blocks for a request and update access statistics."""
        super().record_request_blocks(blocks)

        valid_block_idx = 0
        for _, b in enumerate(blocks):
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue

            block_idx = valid_block_idx
            valid_block_idx += 1

            block_hash = b.block_hash
            if block_hash not in self.tags:
                # First access to this block
                self.tags[block_hash] = RandomFreeBlockManager.Tag(
                    access_count=1,
                    last_access_time=self.current_time,
                    index=block_idx,
                    block_hash=block_hash,
                )
            else:
                # Subsequent access - update statistics with decay
                tag = self.tags[block_hash]

                # Apply decay to existing access count before incrementing
                recency = max(1, self.current_time - tag.last_access_time)
                decay_factor = math.exp(-recency / self.DECAY_CONSTANT)
                tag.access_count = tag.access_count * decay_factor + 1

                tag.last_access_time = self.current_time
                tag.index = block_idx

            # Increment time for each block processed
            self.current_time += 1

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks from the radix tree and clean up tags."""
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(node, max_blocks)

        for child_hash in evictable_hashes:
            if child_hash in self.tags:
                del self.tags[child_hash]

        return evicted_blocks

class RandomFreeBlockManager(RadixTreeFreeBlockManager):
    """Random (online heuristic) Free Block Manager.

    This class implements an online eviction policy that approximates recency and frequency.
    It calculates an eviction score as: (number of accesses) / (blocks since last access) * compute_intensity

    This is designed to compare against LCD to evaluate its effectiveness. Unlike LCD which uses
    sophisticated age-based class modeling, this uses a simple access count / recency heuristic.
    """

    @dataclass
    class Tag:
        access_count: int  # Number of times this block has been accessed (with decay)
        last_access_time: int  # Time (in block counts) when last accessed
        index: int  # Position index for compute intensity
        block_hash: BlockHashWithGroupId
        # Optional history fields used by formula-based managers. Defaults
        # keep them backwards-compatible for existing kwargs constructors.
        first_access_time: int = -1
        prev_access_time: int = -1

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.tags: dict[BlockHashWithGroupId, RandomFreeBlockManager.Tag] = {}
        # Global time counter in block counts
        self.current_time = 0

    def _get_eviction_score(self, tag: Tag) -> float:
        """Calculate eviction score for a block.

        Improvement: Temporal decay applied to access counts to prevent old
        popular blocks from staying forever. Decay is applied both during
        access and during eviction scoring for consistency.

        Lower score means higher priority for eviction.
        Score = (effective_access_count / recency) * compute_intensity
        """
        recency = max(1, self.current_time - tag.last_access_time)
        score = tag.access_count / recency

        # Weight by compute intensity
        compute_intensity = get_compute_intensity(tag.index)

        return score * compute_intensity

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks from the free radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_free_radix_tree_nodes)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        """Try to get n free blocks from the in-use radix tree nodes."""
        return self._sample_and_evict(n, self.radix_tree.sample_in_use_radix_tree_nodes)

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        """Core eviction mechanism using candidate sampling.

        Sample radix tree nodes and evict blocks from the node with the lowest score.
        """
        evicted_blocks = []
        ASSOCIATIVITY = 128
        tried_nodes = set()
        score_cache = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    canonical_block = self._get_canonical_block_for_radix_tree_node(node, n)
                    score = float('inf')

                    if canonical_block is not None and getattr(self, "tags", None) and canonical_block in self.tags:
                        tag = self.tags[canonical_block]
                        score = self._get_eviction_score(tag)
                    score_cache[id(node)] = score

                if score < victim_score:  # Lower score = evict first
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            blocks = self._get_free_blocks_from_radix_tree_nodes(victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        """Record blocks for a request and update access statistics."""
        super().record_request_blocks(blocks)

        valid_block_idx = 0
        for _, b in enumerate(blocks):
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue

            block_idx = valid_block_idx
            valid_block_idx += 1

            block_hash = b.block_hash
            if block_hash not in self.tags:
                # First access to this block
                self.tags[block_hash] = RandomFreeBlockManager.Tag(
                    access_count=1,
                    last_access_time=self.current_time,
                    index=block_idx,
                    block_hash=block_hash,
                )
            else:
                # Subsequent access - update statistics with decay
                tag = self.tags[block_hash]

                # Apply decay to existing access count before incrementing
                tag.access_count = tag.access_count + 1

                tag.last_access_time = self.current_time
                tag.index = block_idx

            # Increment time for each block processed
            self.current_time += 1

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks from the radix tree and clean up tags."""
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(node, max_blocks)

        for child_hash in evictable_hashes:
            if child_hash in self.tags:
                del self.tags[child_hash]

        return evicted_blocks


class RandomBeladyCompareFreeBlockManager(RandomFreeBlockManager):
    """RandomFreeBlockManager that also computes the Belady score for each
    sampled candidate so the two rankings can be compared offline.

    NOTE: This class is an instrumentation tool for sensitivity analysis,
    not a production eviction policy. Eviction decisions are driven by the
    Random score (unchanged); the Belady score is only recorded. Do not
    wire it into serving paths.

    When ``score_dump_file`` is set, each sampling event's
    ``(random_score, belady_score)`` pairs and the chosen victim index are
    written as a JSON line.
    """

    # Sentinel used to encode +inf in the JSON dump so the file stays as plain
    # JSON (json.dumps(inf) emits "Infinity" which is not valid JSON).
    _INF_SENTINEL = 1e18

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.next_access_times: dict[BlockHashWithGroupId, int] = {}
        self.score_dump_file = None

    def _belady_score(self, tag: "RandomFreeBlockManager.Tag") -> float:
        next_time = self.next_access_times.get(tag.block_hash, float("inf"))
        time_until = next_time - self.current_time
        compute_intensity = get_compute_intensity(tag.index)
        if compute_intensity <= 0:
            compute_intensity = 1.0
        return float(time_until) / compute_intensity

    @classmethod
    def _encode_score(cls, v: float) -> float:
        if v == float("inf"):
            return cls._INF_SENTINEL
        if v == float("-inf"):
            return -cls._INF_SENTINEL
        return v

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        evicted_blocks: List[KVCacheBlock] = []
        ASSOCIATIVITY = 128
        tried_nodes: set[int] = set()
        # id(node) -> (random_score, belady_score, recency, frequency)
        score_cache: dict[int, tuple[float, float, float, float]] = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            r_scores: List[float] = []
            b_scores: List[float] = []
            rec_values: List[float] = []
            freq_values: List[float] = []
            for node in valid_nodes:
                if id(node) in score_cache:
                    r, b, rec, freq = score_cache[id(node)]
                else:
                    canonical_block = self._get_canonical_block_for_radix_tree_node(node, n)
                    r = float("inf")
                    b = float("-inf")
                    rec = float("nan")
                    freq = float("nan")
                    if (
                        canonical_block is not None
                        and getattr(self, "tags", None)
                        and canonical_block in self.tags
                    ):
                        tag = self.tags[canonical_block]
                        r = self._get_eviction_score(tag)
                        b = self._belady_score(tag)
                        rec = float(self.current_time - tag.last_access_time)
                        freq = float(tag.access_count)
                    score_cache[id(node)] = (r, b, rec, freq)
                r_scores.append(r)
                b_scores.append(b)
                rec_values.append(rec)
                freq_values.append(freq)

            # Victim by Random rule (lowest random score).
            victim_idx = 0
            victim_score = r_scores[0]
            for i in range(1, len(r_scores)):
                if r_scores[i] < victim_score:
                    victim_score = r_scores[i]
                    victim_idx = i
            victim_node = valid_nodes[victim_idx]

            if self.score_dump_file is not None:
                import json as _json
                def _enc(v):
                    if v != v:  # NaN
                        return None
                    if v == float("inf"):
                        return self._INF_SENTINEL
                    if v == float("-inf"):
                        return -self._INF_SENTINEL
                    return v
                event = {
                    "r": [_enc(v) for v in r_scores],
                    "b": [_enc(v) for v in b_scores],
                    "rec": [_enc(v) for v in rec_values],
                    "freq": [_enc(v) for v in freq_values],
                    "victim": victim_idx,
                    "n_req": n,
                    "t": self.current_time,
                }
                self.score_dump_file.write(_json.dumps(event) + "\n")

            blocks = self._get_free_blocks_from_radix_tree_nodes(victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(node, max_blocks)

        for child_hash in evictable_hashes:
            if child_hash in self.next_access_times:
                del self.next_access_times[child_hash]

        return evicted_blocks


class RandomQuickDemotionFreeBlockManager(RandomFreeBlockManager):
    """Random eviction with quick demotion of one-hit-wonder leaf nodes.

    Extends RandomFreeBlockManager by additionally sampling from leaf nodes
    (nodes with no children in the radix tree) during eviction. One-hit-wonder
    blocks in leaf nodes receive a score penalty, making them more likely to
    be evicted first. This acts as an implicit admission filter: blocks that
    are only accessed once tend to sit in leaf nodes and get evicted quickly,
    while blocks that prove popular survive.
    """

    # Score mode for one-hit leaf blocks:
    #   "multiplier" — score *= ONE_HIT_LEAF_PENALTY
    #   "p10_floor"  — recency = max(P10_RECENCY, recency)
    SCORE_MODE = "multiplier"
    ONE_HIT_LEAF_PENALTY = 0.1
    P10_RECENCY = 243

    LEAF_ASSOCIATIVITY = 32

    def _get_leaf_eviction_score(self, tag: RandomFreeBlockManager.Tag,
                                 is_leaf: bool) -> float:
        """Score with quick-demotion penalty for one-hit leaf blocks."""
        if is_leaf and tag.access_count <= 1:
            if self.SCORE_MODE == "p10_floor":
                recency = max(self.P10_RECENCY,
                              self.current_time - tag.last_access_time)
                return (tag.access_count / recency) * get_compute_intensity(
                    tag.index)
            base_score = self._get_eviction_score(tag)
            return base_score * self.ONE_HIT_LEAF_PENALTY
        return self._get_eviction_score(tag)

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        return self._sample_and_evict_with_leaf_bias(
            n, self.radix_tree.sample_free_radix_tree_nodes)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        return self._sample_and_evict(
            n, self.radix_tree.sample_in_use_radix_tree_nodes)

    def _sample_and_evict_with_leaf_bias(self, n: int,
                                         sample_fn) -> List[KVCacheBlock]:
        """Sample-based eviction with extra sampling from leaf nodes.

        In addition to the normal node sampling, also samples from free leaf
        nodes. One-hit-wonder leaf blocks get a reduced score so they are
        evicted preferentially.
        """
        evicted_blocks: List[KVCacheBlock] = []
        ASSOCIATIVITY = 128
        tried_nodes: set[int] = set()
        score_cache: dict[int, float] = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)

            # Additionally sample from free leaf nodes
            leaf_sampled = self.radix_tree.sample_free_leaf_nodes(
                self.LEAF_ASSOCIATIVITY)
            seen_ids = set(id(node) for node in sampled_nodes)
            for ln in leaf_sampled:
                if id(ln) not in seen_ids:
                    sampled_nodes.append(ln)
                    seen_ids.add(id(ln))

            valid_nodes = [node for node in sampled_nodes
                           if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    canonical_block = \
                        self._get_canonical_block_for_radix_tree_node(node, n)
                    score = float('inf')

                    if (canonical_block is not None
                            and canonical_block in self.tags):
                        tag = self.tags[canonical_block]
                        is_leaf = not node.children
                        score = self._get_leaf_eviction_score(tag, is_leaf)

                    score_cache[id(node)] = score

                if score < victim_score:
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            blocks = self._get_free_blocks_from_radix_tree_nodes(
                victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks


class RandomQuickDemotionBeladyCompareFreeBlockManager(
        RandomQuickDemotionFreeBlockManager):
    """Instrumented RandomQuickDemotion manager for sensitivity analysis.

    NOTE: This class is an instrumentation tool for ranking-correlation
    analysis, not a production eviction policy. Eviction decisions are
    driven by the QuickDemotion rule (unchanged), identical to
    RandomQuickDemotionFreeBlockManager; the Belady score is only observed.

    Mirrors both eviction paths (leaf-biased for free radix nodes, plain for
    in-use nodes) and for each sampled candidate records:
      r[i] — QuickDemotion score (with one-hit-leaf penalty applied where
             relevant), same quantity that drives the actual pick.
      b[i] — Belady score ``(next_access_time - current_time) /
             compute_intensity(index)``; higher = evict.
      victim — index chosen by the QuickDemotion rule.

    Consumed by ``evaluate/vllm/analyze_random_vs_belady.py``.
    """

    _INF_SENTINEL = 1e18

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.next_access_times: dict[BlockHashWithGroupId, int] = {}
        self.score_dump_file = None

    def _belady_score(self, tag: "RandomFreeBlockManager.Tag") -> float:
        next_time = self.next_access_times.get(tag.block_hash, float("inf"))
        time_until = next_time - self.current_time
        compute_intensity = get_compute_intensity(tag.index)
        if compute_intensity <= 0:
            compute_intensity = 1.0
        return float(time_until) / compute_intensity

    @classmethod
    def _encode_score(cls, v: float) -> float:
        if v != v:  # NaN
            return None
        if v == float("inf"):
            return cls._INF_SENTINEL
        if v == float("-inf"):
            return -cls._INF_SENTINEL
        return v

    def _dump_event(self, r_scores, b_scores, victim_idx, n, extra=None):
        if self.score_dump_file is None:
            return
        import json as _json
        ev = {
            "r": [self._encode_score(v) for v in r_scores],
            "b": [self._encode_score(v) for v in b_scores],
            "victim": victim_idx,
            "n_req": n,
            "t": self.current_time,
        }
        if extra:
            for k, v in extra.items():
                if isinstance(v, list) and v and isinstance(v[0], float):
                    ev[k] = [self._encode_score(x) for x in v]
                else:
                    ev[k] = v
        self.score_dump_file.write(_json.dumps(ev) + "\n")

    def _sample_and_evict_with_leaf_bias(
            self, n: int, sample_fn) -> List[KVCacheBlock]:
        evicted_blocks: List[KVCacheBlock] = []
        ASSOCIATIVITY = 128
        tried_nodes: set[int] = set()
        score_cache: dict[int, tuple[float, float, float, float]] = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)

            leaf_sampled = self.radix_tree.sample_free_leaf_nodes(
                self.LEAF_ASSOCIATIVITY)
            seen_ids = set(id(node) for node in sampled_nodes)
            for ln in leaf_sampled:
                if id(ln) not in seen_ids:
                    sampled_nodes.append(ln)
                    seen_ids.add(id(ln))

            valid_nodes = [node for node in sampled_nodes
                           if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            r_scores: List[float] = []
            b_scores: List[float] = []
            rec_values: List[float] = []
            freq_values: List[float] = []
            leaf_flags: List[int] = []
            for node in valid_nodes:
                if id(node) in score_cache:
                    r, b, rec, freq = score_cache[id(node)]
                else:
                    canonical_block = \
                        self._get_canonical_block_for_radix_tree_node(node, n)
                    r = float("inf")
                    b = float("-inf")
                    rec = float("nan")
                    freq = float("nan")
                    if (canonical_block is not None
                            and canonical_block in self.tags):
                        tag = self.tags[canonical_block]
                        is_leaf = not node.children
                        r = self._get_leaf_eviction_score(tag, is_leaf)
                        b = self._belady_score(tag)
                        rec = float(self.current_time - tag.last_access_time)
                        freq = float(tag.access_count)
                    score_cache[id(node)] = (r, b, rec, freq)
                r_scores.append(r)
                b_scores.append(b)
                rec_values.append(rec)
                freq_values.append(freq)
                leaf_flags.append(1 if not node.children else 0)

            victim_idx = 0
            victim_score = r_scores[0]
            for i in range(1, len(r_scores)):
                if r_scores[i] < victim_score:
                    victim_score = r_scores[i]
                    victim_idx = i
            victim_node = valid_nodes[victim_idx]

            self._dump_event(r_scores, b_scores, victim_idx, n,
                             extra={"path": "leaf_bias",
                                    "is_leaf": leaf_flags,
                                    "rec": rec_values,
                                    "freq": freq_values})

            blocks = self._get_free_blocks_from_radix_tree_nodes(
                victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        evicted_blocks: List[KVCacheBlock] = []
        ASSOCIATIVITY = 128
        tried_nodes: set[int] = set()
        score_cache: dict[int, tuple[float, float, float, float]] = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes
                           if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            r_scores: List[float] = []
            b_scores: List[float] = []
            rec_values: List[float] = []
            freq_values: List[float] = []
            for node in valid_nodes:
                if id(node) in score_cache:
                    r, b, rec, freq = score_cache[id(node)]
                else:
                    canonical_block = \
                        self._get_canonical_block_for_radix_tree_node(node, n)
                    r = float("inf")
                    b = float("-inf")
                    rec = float("nan")
                    freq = float("nan")
                    if (canonical_block is not None
                            and canonical_block in self.tags):
                        tag = self.tags[canonical_block]
                        # In-use path — plain score, no leaf bias.
                        r = self._get_eviction_score(tag)
                        b = self._belady_score(tag)
                        rec = float(self.current_time - tag.last_access_time)
                        freq = float(tag.access_count)
                    score_cache[id(node)] = (r, b, rec, freq)
                r_scores.append(r)
                b_scores.append(b)
                rec_values.append(rec)
                freq_values.append(freq)

            victim_idx = 0
            victim_score = r_scores[0]
            for i in range(1, len(r_scores)):
                if r_scores[i] < victim_score:
                    victim_score = r_scores[i]
                    victim_idx = i
            victim_node = valid_nodes[victim_idx]

            self._dump_event(r_scores, b_scores, victim_idx, n,
                             extra={"path": "in_use",
                                    "rec": rec_values,
                                    "freq": freq_values})

            blocks = self._get_free_blocks_from_radix_tree_nodes(
                victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def _get_free_blocks_from_radix_tree_nodes(
            self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if (block_to_evict and block_to_evict.ref_cnt == 0
                    and not node.in_use[i]):
                evictable_hashes.append(child_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(
            node, max_blocks)

        for child_hash in evictable_hashes:
            if child_hash in self.next_access_times:
                del self.next_access_times[child_hash]

        return evicted_blocks


class RandomGhostFreeBlockManager(RandomFreeBlockManager):
    """Random eviction + ghost queue.

    Extends RandomFreeBlockManager with a FIFO ghost queue (sized to cache
    capacity) that preserves the tags of recently evicted blocks. If a block
    with the same hash is admitted again, its prior tag (access_count,
    last_access_time) is recalled so its accumulated history survives the
    eviction round.
    """

    # Which evicted tags get preserved in the ghost queue:
    #   "all"             — every evicted tag
    #   "newcomer_count"  — only tags with access_count <= 1
    #   "newcomer_leaf"   — only tags evicted from a leaf node
    GHOST_FILTER = "all"

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.ghost_capacity = len(blocks)
        self.ghost: "OrderedDict[BlockHashWithGroupId, RandomFreeBlockManager.Tag]" = OrderedDict()

    def _add_to_ghost(self, block_hash: BlockHashWithGroupId,
                      tag: RandomFreeBlockManager.Tag) -> None:
        if block_hash in self.ghost:
            del self.ghost[block_hash]
        while len(self.ghost) >= self.ghost_capacity:
            self.ghost.popitem(last=False)
        self.ghost[block_hash] = tag

    def _should_ghost(self, tag: RandomFreeBlockManager.Tag,
                      was_leaf: bool) -> bool:
        if self.GHOST_FILTER == "newcomer_count":
            return tag.access_count <= 1
        if self.GHOST_FILTER == "newcomer_leaf":
            return was_leaf
        return True

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Recall tags from ghost before super() creates new ones. Super's
        # "not in self.tags" branch creates a fresh access_count=1 tag; by
        # pre-populating self.tags with the ghost tag, super() takes the
        # "subsequent access" branch and bumps the preserved access_count.
        for b in blocks:
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue
            if b.block_hash in self.ghost and b.block_hash not in self.tags:
                self.tags[b.block_hash] = self.ghost.pop(b.block_hash)
        super().record_request_blocks(blocks)

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks and stash their tags in the ghost queue."""
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        was_leaf = not node.children

        # Skip RandomFreeBlockManager's tag deletion; we relocate instead.
        evicted_blocks = RadixTreeFreeBlockManager._get_free_blocks_from_radix_tree_nodes(
            self, node, max_blocks)

        for child_hash in evictable_hashes:
            if child_hash in self.tags:
                tag = self.tags.pop(child_hash)
                if self._should_ghost(tag, was_leaf):
                    self._add_to_ghost(child_hash, tag)

        return evicted_blocks


class RandomGhostPowFreqFreeBlockManager(RandomGhostFreeBlockManager):
    """RandomGhost with score = freq^FREQ_POWER / recency * compute_intensity.

    score_function_sweep.md showed PowFreq2 (freq^2) beats RandomGhost on
    16/20 cells. The hypothesis here: higher powers (3, 4) on freq might
    discriminate one-hit-wonders from multi-hit popular blocks strongly
    enough to subsume RQDG's hard-coded `0.1×` one-hit-leaf penalty —
    making the "QuickDemotion" mechanism (and its associated leaf-biased
    sampling) unnecessary.

    Mechanic: a single-hit block scores `1` (= 1^N for any N), a
    two-hit block scores `2^N`. At N=3, 2-hit is 8× more valuable than
    1-hit; at N=4, 16×. Compare to RQDG's 10× penalty on 1-hit-leaves.

    No leaf-biased sampling. No tier bit. Just a richer freq exponent
    over the base ghost machinery.
    """

    FREQ_POWER = 2  # subclass to set 3 or 4

    def _get_eviction_score(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        recency = max(1, self.current_time - tag.last_access_time)
        score = (tag.access_count ** self.FREQ_POWER) / recency
        return score * get_compute_intensity(tag.index)


class RandomGhostPowFreq2FreeBlockManager(RandomGhostPowFreqFreeBlockManager):
    FREQ_POWER = 2


class RandomGhostPowFreq3FreeBlockManager(RandomGhostPowFreqFreeBlockManager):
    FREQ_POWER = 3


class RandomGhostPowFreq4FreeBlockManager(RandomGhostPowFreqFreeBlockManager):
    FREQ_POWER = 4


class RandomGhostPowFreq6FreeBlockManager(RandomGhostPowFreqFreeBlockManager):
    """Headroom check — if the freq-only signal saturates somewhere
    between 4 and 6 we'll see it in the ablation."""
    FREQ_POWER = 6


class RandomQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        RandomGhostFreeBlockManager):
    """RandomQuickDemotion + ghost queue.

    Combines the leaf-biased sampling and one-hit penalty from
    RandomQuickDemotion with the tag-preserving ghost queue from
    RandomGhost via multiple inheritance.

    MRO: RandomQuickDemotionGhost → RandomQuickDemotion → RandomGhost →
    RandomFreeBlockManager → RadixTreeFreeBlockManager. Ghost methods
    (__init__, record_request_blocks, _get_free_blocks_from_radix_tree_nodes)
    resolve to RandomGhost; _get_leaf_eviction_score resolves to
    RandomQuickDemotion.
    """


class TreeAwareProbGhostBaseFreeBlockManager(RandomGhostFreeBlockManager):
    """Tree-aware eviction with an ancestor-derived likelihood factor.

    Score formula:

        score = p * (freq_X / recency_X) * compute_intensity(X.index)

    where p is a weighted average of ancestor rates with closer ancestors
    contributing more:

        p = Σ_d w_d · r_A_d / Σ_d w_d
        w_d = ALPHA ** d   for d = 1..MAX_ANCESTOR_DEPTH
        r_A_d = ancestor.access_count / max(1, t - ancestor.last_access_time)

    Intuition: p is the candidate's per-step "likelihood of reuse" inferred
    from how busy the prefix above it has been. Higher p ⇒ ancestors are
    hot ⇒ this candidate is more likely to be reused soon ⇒ keep.
    Lower p ⇒ ancestors are cold ⇒ evict.

    This differs from the earlier TreeAwareGhost which blended r_X and r_A
    in log space; here r_X is preserved and only multiplied by the ancestor
    factor p.

    If no ancestors with tags are reachable (root-children, fresh tree),
    p falls back to r_X — the score then reduces to r_X^2 · CI, preserving
    the freq/recency ordering for that population.
    """

    ALPHA = 0.5
    MAX_ANCESTOR_DEPTH = 32

    def _ancestor_factor(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        node_info = self.radix_tree._node_map.get(tag.block_hash)
        if node_info is None:
            recency = max(1, self.current_time - tag.last_access_time)
            return tag.access_count / recency

        node, idx = node_info

        weighted_rate_sum = 0.0
        weight_sum = 0.0

        cur_node = node
        cur_idx = idx - 1
        d = 1

        while d <= self.MAX_ANCESTOR_DEPTH:
            # Cross to parent when we run out of prior blocks in this node.
            while cur_idx < 0:
                cur_node = cur_node.parent
                if cur_node is None or len(cur_node.block_hashes) == 0:
                    cur_node = None
                    break
                cur_idx = len(cur_node.block_hashes) - 1
            if cur_node is None:
                break

            ancestor_hash = cur_node.block_hashes[cur_idx]
            anc = self.tags.get(ancestor_hash)
            if anc is not None:
                anc_recency = max(1, self.current_time - anc.last_access_time)
                anc_rate = anc.access_count / anc_recency
                w = self.ALPHA ** d
                weighted_rate_sum += w * anc_rate
                weight_sum += w

            cur_idx -= 1
            d += 1

        if weight_sum == 0.0:
            recency = max(1, self.current_time - tag.last_access_time)
            return tag.access_count / recency

        return weighted_rate_sum / weight_sum

    def _get_eviction_score(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        recency = max(1, self.current_time - tag.last_access_time)
        own_rate = tag.access_count / recency
        ci = get_compute_intensity(tag.index)
        p = self._ancestor_factor(tag)
        return p * own_rate * ci


class TreeAwareProbGhostFreeBlockManager(
        TreeAwareProbGhostBaseFreeBlockManager):
    """Default tuning: ALPHA = 0.5 (parent dominates the ancestor signal)."""
    ALPHA = 0.5


class TreeAwareProbGhost_a03FreeBlockManager(
        TreeAwareProbGhostBaseFreeBlockManager):
    ALPHA = 0.3


class TreeAwareProbGhost_a07FreeBlockManager(
        TreeAwareProbGhostBaseFreeBlockManager):
    ALPHA = 0.7


class TreeAwareProbGhost_a09FreeBlockManager(
        TreeAwareProbGhostBaseFreeBlockManager):
    ALPHA = 0.9


class TreeAwareProbQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareProbGhostBaseFreeBlockManager):
    """TreeAwareProbGhost stacked on top of QuickDemotion's leaf bias.

    MRO: TreeAwareProbQuickDemotionGhost → RandomQuickDemotion →
    TreeAwareProbGhostBase → RandomGhost → RandomFreeBlockManager →
    RadixTreeFreeBlockManager. The leaf-biased sampling and one-hit penalty
    come from RandomQuickDemotion; the ancestor-factor _get_eviction_score
    resolves to TreeAwareProbGhostBase via MRO; ghost __init__ /
    record_request_blocks / eviction stash come from RandomGhost.
    """


class TreeAwareRecProbGhostBaseFreeBlockManager(RandomGhostFreeBlockManager):
    """Tree-aware eviction with a *recursively* defined ancestor-blended
    frequency score.

    Score formula:

        p(X) = LAMBDA · access_count(X) + (1 − LAMBDA) · p(parent(X))
        p(root) = 0
        score(X) = p(X) · compute_intensity(X.index)

    Expanded down the recursion (geometric series over the prefix path):

        p(X) = LAMBDA · Σ_{d=0..} (1 − LAMBDA)^d · access_count(A_d)

    where ``A_0 = X`` and ``A_d`` is the ancestor at distance d in the
    radix tree (immediate predecessor at d=1, grandparent at d=2, …).
    Weights sum to 1 (true convex combination), and an ancestor's
    impact decays exponentially with distance — closer ancestors
    weight more, the root contributes essentially nothing.

    Why this design (vs `TreeAwareProbGhost`):

    The earlier `TreeAwareProbGhost` used a multiplier ``p =
    weighted_avg(r_A_d)`` based on ancestor *rates* (freq/recency). The
    rate construction had a property — ``r_A ≥ r_X`` always — that
    made the multiplier flip wildly with depth and dominated the
    candidate's own freq/recency signal. Diagnosis showed Spearman(p,
    depth) = +0.37 while Spearman(r_X, depth) = −0.55: the tree
    boost was anti-correlated with the trustworthy r_X signal and was
    over-protecting deep blocks.

    This formulation drops r_A entirely. p is a pure damped sum of
    ancestor *frequencies* (access counts). Recency is dropped from
    the score, so this behaves like a tree-aware LFU with exponential
    ancestor influence.

    Edge case: ancestors whose tags are not in ``self.tags`` (e.g.
    fully evicted, no ghost recall yet) contribute 0 to the
    accumulation but the recursion continues — i.e. their parents
    still get their normal weight. This matches the literal
    ``p_0 = λ · 0 + (1 − λ) · p_1`` substitution.
    """

    LAMBDA = 0.5
    MAX_ANCESTOR_DEPTH = 64

    def _compute_p(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        node_info = self.radix_tree._node_map.get(tag.block_hash)
        if node_info is None:
            return self.LAMBDA * tag.access_count

        node, idx = node_info

        result = 0.0
        weight = self.LAMBDA  # weight on d=0 (self)
        d = 0
        cur_node = node
        cur_idx = idx
        decay = 1.0 - self.LAMBDA

        while d <= self.MAX_ANCESTOR_DEPTH:
            while cur_idx < 0:
                cur_node = cur_node.parent
                if cur_node is None or len(cur_node.block_hashes) == 0:
                    cur_node = None
                    break
                cur_idx = len(cur_node.block_hashes) - 1
            if cur_node is None:
                break

            cur_hash = cur_node.block_hashes[cur_idx]
            tag_at = self.tags.get(cur_hash)
            freq_at = tag_at.access_count if tag_at is not None else 0
            result += weight * freq_at

            cur_idx -= 1
            d += 1
            weight *= decay

        return result

    def _get_eviction_score(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        p = self._compute_p(tag)
        ci = get_compute_intensity(tag.index)
        return p * ci


class TreeAwareRecProbGhostFreeBlockManager(
        TreeAwareRecProbGhostBaseFreeBlockManager):
    """Default tuning: λ = 0.5 (self and ancestor contributions balanced)."""
    LAMBDA = 0.5


class TreeAwareRecProbGhost_l01FreeBlockManager(
        TreeAwareRecProbGhostBaseFreeBlockManager):
    LAMBDA = 0.1


class TreeAwareRecProbGhost_l03FreeBlockManager(
        TreeAwareRecProbGhostBaseFreeBlockManager):
    LAMBDA = 0.3


class TreeAwareRecProbGhost_l07FreeBlockManager(
        TreeAwareRecProbGhostBaseFreeBlockManager):
    LAMBDA = 0.7


class TreeAwareRecProbGhost_l09FreeBlockManager(
        TreeAwareRecProbGhostBaseFreeBlockManager):
    LAMBDA = 0.9


class TreeAwareRecProbQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbGhostBaseFreeBlockManager):
    """TreeAwareRecProbGhost stacked on top of QuickDemotion's leaf bias.

    MRO: TreeAwareRecProbQuickDemotionGhost → RandomQuickDemotion →
    TreeAwareRecProbGhostBase → RandomGhost → RandomFreeBlockManager →
    RadixTreeFreeBlockManager. The leaf-biased sampling and one-hit
    penalty come from RandomQuickDemotion; the recursive p score
    resolves to TreeAwareRecProbGhostBase via MRO.
    """


class TreeAwareRecProbRecencyGhostBaseFreeBlockManager(
        TreeAwareRecProbGhostBaseFreeBlockManager):
    """Like TreeAwareRecProbGhost, but recency-aware:

        score(X) = p(X) / recency(X) · compute_intensity(X.index)

    where p is the same recursive blend of access counts:

        p(X) = LAMBDA · access_count(X) + (1 − LAMBDA) · p(parent(X))

    The first sweep of `TreeAwareRecProbGhost` (no recency) fixed the
    depth-anti-correlation problem of the previous TreeAwareProb but
    introduced a new failure mode at loose caches: without recency, an
    old block with a high access_count stays cached forever, even
    after it stops being requested. By dividing by recency we get back
    the LRU-ish forgetting behaviour while retaining the tree-aware
    frequency blend.
    """

    def _get_eviction_score(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        p = self._compute_p(tag)
        recency = max(1, self.current_time - tag.last_access_time)
        ci = get_compute_intensity(tag.index)
        return (p / recency) * ci


class TreeAwareRecProbRecencyGhostFreeBlockManager(
        TreeAwareRecProbRecencyGhostBaseFreeBlockManager):
    """Default tuning: λ = 0.5."""
    LAMBDA = 0.5


class TreeAwareRecProbRecencyGhost_l03FreeBlockManager(
        TreeAwareRecProbRecencyGhostBaseFreeBlockManager):
    LAMBDA = 0.3


class TreeAwareRecProbRecencyGhost_l07FreeBlockManager(
        TreeAwareRecProbRecencyGhostBaseFreeBlockManager):
    LAMBDA = 0.7


class TreeAwareRecProbRecencyGhost_l09FreeBlockManager(
        TreeAwareRecProbRecencyGhostBaseFreeBlockManager):
    LAMBDA = 0.9


class TreeAwareRecProbRecencyQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbRecencyGhostBaseFreeBlockManager):
    """Recency-aware recursive p, stacked on QuickDemotion."""


class TreeAwareRecProbBoostGhostBaseFreeBlockManager(
        TreeAwareRecProbGhostBaseFreeBlockManager):
    """Recursive ancestor-frequency blend used as a boost factor on the
    full Random score:

        score(X) = p(X) · (access_count(X) / recency(X)) · CI(X)

    where p is the same recursive blend as `TreeAwareRecProbGhost`:

        p(X) = LAMBDA · access_count(X) + (1 − LAMBDA) · p(parent(X))

    Compared with `TreeAwareRecProbRecencyGhost` (which uses
    ``score = p / recency · CI``), this variant retains the Random
    score (``freq/recency · CI``) intact and multiplies it by the
    ancestor-derived likelihood factor p. The candidate's own freq
    therefore affects the score *twice*: once embedded inside p
    (with weight LAMBDA at d=0) and once explicitly via the freq
    factor. The intent: keep the proven freq/recency ranking and
    let p nudge it.

    Unlike the original `TreeAwareProbGhost` (rate-based multiplier
    that backfired due to ``r_A ≥ r_X`` asymmetry), the recursive p
    is bounded, has weights that sum to 1, and uses raw access
    counts — none of the structural issues that drove the previous
    failure.
    """

    def _get_eviction_score(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        p = self._compute_p(tag)
        recency = max(1, self.current_time - tag.last_access_time)
        rate = tag.access_count / recency
        ci = get_compute_intensity(tag.index)
        return p * rate * ci


class TreeAwareRecProbBoostGhostFreeBlockManager(
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    """Default tuning: λ = 0.5."""
    LAMBDA = 0.5


class TreeAwareRecProbBoostGhost_l03FreeBlockManager(
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.3


class TreeAwareRecProbBoostGhost_l07FreeBlockManager(
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.7


class TreeAwareRecProbBoostGhost_l09FreeBlockManager(
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.9


class TreeAwareRecProbBoostQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    """TreeAwareRecProbBoostGhost (λ=0.5) stacked on QuickDemotion."""


class TreeAwareRecProbBoostQuickDemotionGhost_l07FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbBoostGhost_l07FreeBlockManager):
    """λ=0.7 variant stacked on QuickDemotion."""


class TreeAwareRecProbBoostQuickDemotionGhost_l09FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbBoostGhost_l09FreeBlockManager):
    """λ=0.9 variant stacked on QuickDemotion."""


class TreeAwareRecProbBoostQuickDemotionGuardGhostBaseFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    """Boost form + QD with the *one-hit-leaf path bypassed* for the tree
    multiplier.

    Plain BoostQuickDemotion uses ``score = p · freq/recency · CI`` for
    every candidate (including one-hit leaves), then QD multiplies the
    one-hit-leaf score by 0.1. Problem: a one-hit leaf with hot
    ancestors gets a large p — its score is ``p · rate · 0.1 · CI``
    which can still be much higher than a cold-ancestor one-hit's
    ``rate · 0.1 · CI``. The tree-boost partially un-demotes the leaf
    QD correctly identifies as a one-hit-wonder, and QD ends up
    evicting cold-ancestor one-hits while protecting hot-ancestor
    one-hits. That mis-ranks the very population QD is best at
    handling.

    This variant guards the one-hit-leaf branch: for those candidates,
    score = ``rate · CI · ONE_HIT_LEAF_PENALTY`` (no tree boost),
    matching plain RandomQuickDemotionGhost. For everyone else,
    score = ``p · rate · CI`` (boost as usual).
    """

    def _get_leaf_eviction_score(self,
                                 tag: 'RandomFreeBlockManager.Tag',
                                 is_leaf: bool) -> float:
        if is_leaf and tag.access_count <= 1:
            recency = max(1, self.current_time - tag.last_access_time)
            rate = tag.access_count / recency
            ci = get_compute_intensity(tag.index)
            return rate * ci * self.ONE_HIT_LEAF_PENALTY
        return self._get_eviction_score(tag)


class TreeAwareRecProbBoostQuickDemotionGuardGhostFreeBlockManager(
        TreeAwareRecProbBoostQuickDemotionGuardGhostBaseFreeBlockManager):
    """Default tuning: λ = 0.5."""
    LAMBDA = 0.5


class TreeAwareRecProbBoostQuickDemotionGuardGhost_l07FreeBlockManager(
        TreeAwareRecProbBoostQuickDemotionGuardGhostBaseFreeBlockManager):
    LAMBDA = 0.7


class TreeAwareRecProbBoostQuickDemotionGuardGhost_l09FreeBlockManager(
        TreeAwareRecProbBoostQuickDemotionGuardGhostBaseFreeBlockManager):
    LAMBDA = 0.9


class TreeAwareRecProbBoostLogFreqGhostBaseFreeBlockManager(
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    """Boost form gated by log(freq_X) — continuous one-hit bypass.

        score(X) = rate(X) · CI(X) · (1 + log(freq_X) · (p(X) / freq_X − 1)_+)

    where ``(·)_+ = max(0, ·)`` clips to non-negative, ``rate =
    freq/recency``, and p is the recursive ancestor blend (same as
    `TreeAwareRecProbGhost`).

    Semantics:
    - **freq_X = 1**: ``log(1) = 0`` → boost factor = 1 → score =
      plain Random (``rate · CI``). QD's one-hit-leaf demotion runs
      uncorrupted; the tree never re-protects what QD wants to evict.
    - **freq_X > 1, ancestors no hotter than self** (``p / freq ≤ 1``):
      clip to zero → score = plain Random. The tree only fires when
      it has positive evidence of an asymmetric ancestor signal.
    - **freq_X > 1, ancestors hotter** (``p / freq > 1``): boost grows
      with ``log(freq)`` (more multi-hit history = more confidence
      this candidate is worth protecting) and with ancestor-to-self
      heat ratio.

    The intuition: only blocks with both (a) demonstrated multi-hit
    history and (b) currently-hot prefix above them deserve a tree
    boost. Cold one-hits get out of QD's way; cold-prefix candidates
    get plain freq/recency ranking.
    """

    def _get_eviction_score(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        recency = max(1, self.current_time - tag.last_access_time)
        rate = tag.access_count / recency
        ci = get_compute_intensity(tag.index)
        if tag.access_count <= 1:
            return rate * ci
        p = self._compute_p(tag)
        rel_anc = p / tag.access_count - 1.0
        if rel_anc <= 0:
            return rate * ci
        boost = 1.0 + math.log(tag.access_count) * rel_anc
        return rate * ci * boost


class TreeAwareRecProbBoostLogFreqGhostFreeBlockManager(
        TreeAwareRecProbBoostLogFreqGhostBaseFreeBlockManager):
    """Default: λ = 0.5."""
    LAMBDA = 0.5


class TreeAwareRecProbBoostLogFreqGhost_l07FreeBlockManager(
        TreeAwareRecProbBoostLogFreqGhostBaseFreeBlockManager):
    LAMBDA = 0.7


class TreeAwareRecProbBoostLogFreqGhost_l09FreeBlockManager(
        TreeAwareRecProbBoostLogFreqGhostBaseFreeBlockManager):
    LAMBDA = 0.9


class TreeAwareRecProbBoostLogFreqQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbBoostLogFreqGhostBaseFreeBlockManager):
    """Log(freq)-gated boost stacked on QuickDemotion. Because the gate
    yields plain Random score for one-hit candidates, QD's one-hit-leaf
    × 0.1 penalty applies to a clean baseline — no un-demotion."""


class TreeAwareRecProbBoostLogFreqQuickDemotionGhost_l07FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbBoostLogFreqGhost_l07FreeBlockManager):
    pass


class TreeAwareRecProbBoostLogFreqQuickDemotionGhost_l09FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareRecProbBoostLogFreqGhost_l09FreeBlockManager):
    pass


class TreeAwareRecProbBoostQDInspectFreeBlockManager(
        TreeAwareRecProbBoostQuickDemotionGhost_l09FreeBlockManager):
    """Diagnostic-only. Records per-eviction:
      - For each candidate: depth, freq, recency, p, rate, leaf,
        rqdg_score (= rate · CI · 0.1 if one_hit_leaf else rate · CI),
        boostqd_score (= our actual score, post-QD penalty).
      - Identifies the chosen victim (lowest boostqd_score) and the
        rqdg-only victim (lowest rqdg_score).
      - Logs to JSONL at $TREEAWARE_INSPECT_LOG.
    Behaviour matches BoostQDGhost_l09 when env var is unset.
    """

    def _depth_of_block(self, block_hash) -> int:
        ni = self.radix_tree._node_map.get(block_hash)
        if ni is None:
            return -1
        node, idx = ni
        depth = idx
        cur = node.parent
        while cur is not None and len(cur.block_hashes) > 0:
            depth += len(cur.block_hashes)
            cur = cur.parent
        return depth

    def _sample_and_evict_with_leaf_bias(self, n, sample_fn):
        import os, json
        log_path = os.environ.get("TREEAWARE_INSPECT_LOG")
        if not log_path:
            return super()._sample_and_evict_with_leaf_bias(n, sample_fn)

        evicted = []
        ASSOCIATIVITY = 128
        tried = set()
        score_cache = {}
        log_f = open(log_path, "a")

        while n > 0:
            sampled = sample_fn(ASSOCIATIVITY)
            leaf_sampled = self.radix_tree.sample_free_leaf_nodes(
                self.LEAF_ASSOCIATIVITY)
            seen = set(id(x) for x in sampled)
            for ln in leaf_sampled:
                if id(ln) not in seen:
                    sampled.append(ln); seen.add(id(ln))
            valid = [x for x in sampled if id(x) not in tried]
            if not valid:
                break

            victim = None
            best_score = float('inf')
            cand = []
            for node in valid:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                    cand.append(None)
                else:
                    cb = self._get_canonical_block_for_radix_tree_node(node, n)
                    if cb is not None and cb in self.tags:
                        tag = self.tags[cb]
                        is_leaf = not node.children
                        score = self._get_leaf_eviction_score(tag, is_leaf)
                        recency = max(1, self.current_time - tag.last_access_time)
                        rate = tag.access_count / recency
                        ci = get_compute_intensity(tag.index)
                        rqdg_score = rate * ci
                        if is_leaf and tag.access_count <= 1:
                            rqdg_score *= self.ONE_HIT_LEAF_PENALTY
                        cand.append({
                            "depth": self._depth_of_block(cb),
                            "freq": tag.access_count,
                            "rec": recency,
                            "p": self._compute_p(tag),
                            "leaf": bool(is_leaf),
                            "boostqd_score": score,
                            "rqdg_score": rqdg_score,
                        })
                    else:
                        score = float('inf')
                        cand.append(None)
                    score_cache[id(node)] = score
                if score < best_score:
                    best_score = score
                    victim = node

            if victim is None:
                victim = valid[0]

            chosen_idx_cand = None
            best_rqdg = float('inf')
            rqdg_idx_cand = None
            cand_pos = 0
            for i, (node, rec) in enumerate(zip(valid, cand)):
                if rec is not None:
                    if node is victim:
                        chosen_idx_cand = cand_pos
                    if rec["rqdg_score"] < best_rqdg:
                        best_rqdg = rec["rqdg_score"]
                        rqdg_idx_cand = cand_pos
                    cand_pos += 1
                elif node is victim:
                    chosen_idx_cand = -1

            non_null = [r for r in cand if r is not None]
            if non_null:
                log_f.write(json.dumps({
                    "t": self.current_time,
                    "n_cand": len(non_null),
                    "candidates": non_null,
                    "boostqd_idx": chosen_idx_cand,
                    "rqdg_idx": rqdg_idx_cand,
                }) + "\n")

            blocks = self._get_free_blocks_from_radix_tree_nodes(victim, n)
            if not blocks:
                tried.add(id(victim))
            else:
                evicted.extend(blocks)
                n -= len(blocks)
                self._num_free_blocks -= len(blocks)
                score_cache.pop(id(victim), None)
        log_f.close()
        return evicted


class TreeAwareBranchProbBoostGhostBaseFreeBlockManager(
        TreeAwareRecProbBoostGhostBaseFreeBlockManager):
    """Boost form with *branching-based* p:

        p(node) = LAMBDA · q + (1 − LAMBDA) · p(next-higher-branching-ancestor)
        q       = freq(branching_ancestor) / num_children(branching_ancestor)

    where a "branching ancestor" is an ancestor (going up the
    prefix path from the candidate's node) whose number of children
    exceeds ``MIN_BRANCH_CHILDREN`` (default 3, so ≥ 4 children
    required — three or fewer can have accuracy issues at low
    counts). If no such ancestor exists anywhere on the path,
    ``p = 1`` (neutral; score collapses to plain Random for that
    candidate).

    Score formula (inherited from `TreeAwareRecProbBoostGhost`):

        score(X) = p · (freq_X / recency_X) · CI(X)

    Why ``freq / num_children``? At a branching point, the parent
    node's accesses fan out across its children. ``freq(parent) /
    num_children`` is the *expected per-child frequency* — a
    plausible per-branch reuse-probability estimate that's
    automatically attenuated when the prefix forks widely (system
    prompt with hundreds of distinct continuations) versus stays
    narrow (a unique long preamble).

    Compared to the recursive ``p = λ·freq + (1-λ)·p_parent`` from
    `TreeAwareRecProbGhost`:
    - Old p was dominated by raw access counts that scale with
      prefix popularity. New q is *normalized* by branch count.
    - Old p put 90% weight on the candidate's own freq at λ=0.9,
      so p ≈ freq for 99.9% of candidates and the boost was
      mostly a no-op. New p has no candidate-self term: it's
      purely an ancestor-derived quantity, so it always carries
      independent information.
    - Old p applied to every candidate uniformly. New p is 1
      (no boost) when the path doesn't pass through any meaningful
      branching point — a clean fallback.
    """

    MIN_BRANCH_CHILDREN = 4  # strictly more than 3
    MAX_RECURSION_DEPTH = 64

    def _compute_p(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        node_info = self.radix_tree._node_map.get(tag.block_hash)
        if node_info is None:
            return 1.0
        node, _ = node_info
        return self._node_branch_p(node, self.MAX_RECURSION_DEPTH)

    def _node_branch_p(self, node, depth_remaining: int) -> float:
        if depth_remaining <= 0:
            return 1.0
        cur = node.parent
        while cur is not None and len(cur.block_hashes) > 0:
            if len(cur.children) >= self.MIN_BRANCH_CHILDREN:
                first_hash = cur.block_hashes[0]
                tag_at = self.tags.get(first_hash)
                freq_node = (tag_at.access_count
                             if tag_at is not None else 0)
                num_kids = len(cur.children)
                q = freq_node / num_kids
                p_higher = self._node_branch_p(cur, depth_remaining - 1)
                return self.LAMBDA * q + (1.0 - self.LAMBDA) * p_higher
            cur = cur.parent
        return 1.0


class TreeAwareBranchProbBoostGhostFreeBlockManager(
        TreeAwareBranchProbBoostGhostBaseFreeBlockManager):
    """Default tuning: λ = 0.5."""
    LAMBDA = 0.5


class TreeAwareBranchProbBoostGhost_l03FreeBlockManager(
        TreeAwareBranchProbBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.3


class TreeAwareBranchProbBoostGhost_l07FreeBlockManager(
        TreeAwareBranchProbBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.7


class TreeAwareBranchProbBoostGhost_l09FreeBlockManager(
        TreeAwareBranchProbBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.9


class TreeAwareBranchProbBoostQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbBoostGhostBaseFreeBlockManager):
    """Branch-prob boost stacked on QuickDemotion (default λ=0.5)."""


class TreeAwareBranchProbBoostQuickDemotionGhost_l03FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbBoostGhost_l03FreeBlockManager):
    pass


class TreeAwareBranchProbBoostQuickDemotionGhost_l07FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbBoostGhost_l07FreeBlockManager):
    pass


class TreeAwareBranchProbBoostQuickDemotionGhost_l09FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbBoostGhost_l09FreeBlockManager):
    pass


class TreeAwareBranchProbLogBoostGhostBaseFreeBlockManager(
        TreeAwareBranchProbBoostGhostBaseFreeBlockManager):
    """Log-compressed branching p (smoothed boost form).

    Same recursive structure as `TreeAwareBranchProbBoostGhost`, but
    log-compress q at each branching point to keep the cross-group
    dynamic range bounded:

        p_log(node) = LAMBDA · log(1 + q)
                    + (1 − LAMBDA) · p_log(next-higher branching ancestor)
        p_log(no branching ancestor on path) = 0
        p(node) = 1 + p_log(node)

    The plain branch form had p ranging 1–50+ across candidates,
    big enough to flip ranks across groups (post-branching descendants
    were over-protected vs pre-branching shared blocks). Log-compression
    pulls the spread down to ~1–5, the same order as the freq/recency
    rate signal — the boost now *nudges* the rank instead of dominating
    it across groups.

    score(X) = p(X) · (freq_X / recency_X) · CI(X)
    """

    def _compute_p(self, tag: 'RandomFreeBlockManager.Tag') -> float:
        node_info = self.radix_tree._node_map.get(tag.block_hash)
        if node_info is None:
            return 1.0
        node, _ = node_info
        log_p = self._node_log_branch_p(node, self.MAX_RECURSION_DEPTH)
        return 1.0 + log_p

    def _node_log_branch_p(self, node, depth_remaining: int) -> float:
        if depth_remaining <= 0:
            return 0.0
        cur = node.parent
        while cur is not None and len(cur.block_hashes) > 0:
            if len(cur.children) >= self.MIN_BRANCH_CHILDREN:
                first_hash = cur.block_hashes[0]
                tag_at = self.tags.get(first_hash)
                freq_node = (tag_at.access_count
                             if tag_at is not None else 0)
                num_kids = len(cur.children)
                q = freq_node / num_kids
                log_q = math.log(1.0 + q)
                p_higher = self._node_log_branch_p(cur, depth_remaining - 1)
                return self.LAMBDA * log_q + (1.0 - self.LAMBDA) * p_higher
            cur = cur.parent
        return 0.0


class TreeAwareBranchProbLogBoostGhostFreeBlockManager(
        TreeAwareBranchProbLogBoostGhostBaseFreeBlockManager):
    """Default: λ = 0.5."""
    LAMBDA = 0.5


class TreeAwareBranchProbLogBoostGhost_l03FreeBlockManager(
        TreeAwareBranchProbLogBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.3


class TreeAwareBranchProbLogBoostGhost_l07FreeBlockManager(
        TreeAwareBranchProbLogBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.7


class TreeAwareBranchProbLogBoostGhost_l09FreeBlockManager(
        TreeAwareBranchProbLogBoostGhostBaseFreeBlockManager):
    LAMBDA = 0.9


class TreeAwareBranchProbLogBoostQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbLogBoostGhostBaseFreeBlockManager):
    """Smoothed branch-prob boost stacked on QuickDemotion (default λ=0.5)."""


class TreeAwareBranchProbLogBoostQuickDemotionGhost_l03FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbLogBoostGhost_l03FreeBlockManager):
    pass


class TreeAwareBranchProbLogBoostQuickDemotionGhost_l07FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbLogBoostGhost_l07FreeBlockManager):
    pass


class TreeAwareBranchProbLogBoostQuickDemotionGhost_l09FreeBlockManager(
        RandomQuickDemotionFreeBlockManager,
        TreeAwareBranchProbLogBoostGhost_l09FreeBlockManager):
    pass


class RandomAdaptive2FreeBlockManager(RandomFreeBlockManager):
    """Port of cachesim's `random_adaptive2` policy.

    Score formula (per-block, on the canonical block of a sampled radix-tree
    node):

        age_exp = clamp(0.25 + (log2(mean_len) - LOG_PIVOT) / LOG_SCALE,
                        0.25, 1.5)
        score   = tier_factor * prefix_boost * freq * cost / recency^age_exp

    where:
      * `tier_factor` is 1.0 for promoted tags, `PROBATION_DEMOTION` (=0.7)
        for probationary ones — quick-demotion of unproven content.
      * `prefix_boost` = 1 + log2(count) when the request that admitted this
        block carried a leading-prefix hash that has been seen at least
        `PREFIX_BOOST_THRESHOLD` times across the request stream.
      * `mean_len` is an EMA of request lengths.

    Promotion rules:
      * The tag is promoted on its 2nd+ access (`access_count >= 2`).
      * On creation, if the request's leading-prefix hash matches an entry
        in the ghost set, the tag is promoted immediately and the ghost
        entry is consumed.

    Ghost set: a bounded set of leading-prefix hashes from recently-evicted
    blocks whose tags had `access_count >= 2`. Single-use miss tails are
    skipped to avoid polluting the set.

    No admission control — see the cachesim ablation that shows the cold
    cap contributed only +0.04pp.
    """

    EMA_WINDOW = 64.0
    LOG_PIVOT = 6.0
    LOG_SCALE = 2.0
    HASH_WINDOW = 8
    HASH_COUNT_CAP = 1023
    PREFIX_BOOST_THRESHOLD = 3
    PROBATION_DEMOTION = 0.7
    GHOST_MAX_ENTRIES = 32768

    @dataclass
    class Tag:
        access_count: int
        last_access_time: int
        index: int
        block_hash: BlockHashWithGroupId
        leading_prefix_hash: int = 0
        promoted: bool = False

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.mean_len: float = 0.0
        self.prefix_history: dict[int, int] = {}
        self.ghost_set: set[int] = set()

    @staticmethod
    def _mix64(x: int) -> int:
        x &= 0xFFFFFFFFFFFFFFFF
        x ^= x >> 30
        x = (x * 0xbf58476d1ce4e5b9) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 27
        x = (x * 0x94d049bb133111eb) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 31
        return x

    @classmethod
    def _hash_prefix(cls, block_hashes, take: int) -> int:
        """Position-independent FNV-style hash of the first `take` block
        hashes. Mirrors random_adaptive2.cpp::hash_prefix."""
        h = 0xcbf29ce484222325
        n = min(take, len(block_hashes))
        for i in range(n):
            bh = block_hashes[i]
            # block_hash is bytes (BlockHashWithGroupId). Fold to a 64-bit int.
            x = int.from_bytes(bh[:8], "big", signed=False) if len(bh) >= 8 \
                else int.from_bytes(bh, "big", signed=False)
            h ^= cls._mix64(x)
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
        return cls._mix64(h ^ n)

    def _add_to_ghost(self, h: int) -> None:
        if h in self.ghost_set:
            return
        if len(self.ghost_set) >= self.GHOST_MAX_ENTRIES:
            # Drop ~half by arbitrary iteration order. Faithful to the
            # cachesim implementation (also a no-order halving on overflow).
            target = len(self.ghost_set) // 2
            for _ in range(target):
                self.ghost_set.pop()
        self.ghost_set.add(h)

    def _get_eviction_score(self, tag: 'RandomAdaptive2FreeBlockManager.Tag') -> float:
        recency = max(1, self.current_time - tag.last_access_time)
        cost = get_compute_intensity(tag.index)
        freq = tag.access_count

        log_len = math.log2(max(self.mean_len, 1.0))
        age_exp = max(
            0.25,
            min(1.5, 0.25 + (log_len - self.LOG_PIVOT) / self.LOG_SCALE),
        )

        tier_factor = 1.0 if getattr(tag, 'promoted', False) \
            else self.PROBATION_DEMOTION

        prefix_boost = 1.0
        h = getattr(tag, 'leading_prefix_hash', 0)
        count = self.prefix_history.get(h, 0)
        if count >= self.PREFIX_BOOST_THRESHOLD:
            prefix_boost = 1.0 + math.log2(count)

        return tier_factor * prefix_boost * freq * cost / (recency ** age_exp)

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Bypass RandomFreeBlockManager.record_request_blocks — it would
        # create RandomFreeBlockManager.Tag instances without our extra
        # fields. Instead replicate its body with our Tag class and the
        # ghost / probation hooks.
        # First, run the radix-tree-level update via the RadixTree base.
        RadixTreeFreeBlockManager.record_request_blocks(self, blocks)

        valid_block_hashes = []
        for b in blocks:
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue
            valid_block_hashes.append(b.block_hash)

        if not valid_block_hashes:
            return

        # Per-block leading-prefix hash: each block i is identified by the
        # hash of the kHashWindow window starting at position i in the
        # request. This mirrors `node->block_ids[:8]` in cachesim — every
        # node has its own per-position leading-prefix hash, distinct from
        # the request-prefix hash.
        n_valid = len(valid_block_hashes)
        per_block_prefix_hash = [
            self._hash_prefix(valid_block_hashes[i:i + self.HASH_WINDOW],
                              self.HASH_WINDOW)
            for i in range(n_valid)
        ]
        # Request-prefix hash for the prefix_history sketch (matches
        # cachesim's hash_prefix(sequence, kHashWindow) on the request
        # side). It equals per_block_prefix_hash[0].
        request_prefix_hash = per_block_prefix_hash[0]

        valid_block_idx = 0
        for b in blocks:
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue

            block_idx = valid_block_idx
            block_prefix_hash = per_block_prefix_hash[block_idx]
            valid_block_idx += 1
            block_hash = b.block_hash

            existing = self.tags.get(block_hash)
            if existing is None:
                # First admission of this block_hash. Decide tier:
                #   - ghost-set hit on this block's leading prefix
                #     => promote.
                #   - else probationary.
                promoted = False
                if block_prefix_hash in self.ghost_set:
                    promoted = True
                    self.ghost_set.discard(block_prefix_hash)
                self.tags[block_hash] = RandomAdaptive2FreeBlockManager.Tag(
                    access_count=1,
                    last_access_time=self.current_time,
                    index=block_idx,
                    block_hash=block_hash,
                    leading_prefix_hash=block_prefix_hash,
                    promoted=promoted,
                )
            else:
                existing.access_count += 1
                existing.last_access_time = self.current_time
                existing.index = block_idx
                # Refresh prefix association with the current request.
                existing.leading_prefix_hash = block_prefix_hash
                # 2nd+ access => promote.
                if existing.access_count >= 2:
                    existing.promoted = True

            self.current_time += 1

        # Update online statistics on request completion.
        seq_len = float(n_valid)
        if self.mean_len == 0.0:
            self.mean_len = seq_len
        else:
            alpha = 1.0 / self.EMA_WINDOW
            self.mean_len = self.mean_len + alpha * (seq_len - self.mean_len)

        count = self.prefix_history.get(request_prefix_hash, 0)
        if count < self.HASH_COUNT_CAP:
            self.prefix_history[request_prefix_hash] = count + 1

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks and stash leading-prefix hashes of evicted tags
        (with >=2 accesses) into the ghost set."""
        # Identify which child hashes will actually be evicted before the
        # parent removes their tags.
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if block_to_evict and block_to_evict.ref_cnt == 0 and not node.in_use[i]:
                evictable_hashes.append(child_hash)

        # Capture ghost entries before super() drops the tags.
        ghost_adds = []
        for child_hash in evictable_hashes:
            tag = self.tags.get(child_hash)
            if tag is not None and tag.access_count >= 2:
                ghost_adds.append(tag.leading_prefix_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(
            node, max_blocks)

        for h in ghost_adds:
            self._add_to_ghost(h)

        return evicted_blocks


class RandomQuickDemotionGhostAdaptive2FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    """`random_adaptive2`'s adaptations layered on RQDG's strong machinery.

    The faithful `RandomAdaptive2FreeBlockManager` port doesn't beat vLLM
    Random because mylessons.md's gains over C++ `random` come mostly
    from canceling C++ Random's per-node size bias — a bias vLLM Random
    never had. The remaining mechanisms (length-driven recency, prob
    tier, ghost, prefix boost) are individually too gentle on vLLM's
    per-block baseline to compete with RQDG.

    This class keeps RQDG's strong pieces intact:
      * leaf-biased sampling (extra leaf-node samples each round)
      * one-hit-leaf 0.1× score multiplier (10× demotion)
      * tag-preserving FIFO ghost (full access_count restored on
        readmission)

    and adds the two `random_adaptive2` adaptations that complement
    them:
      * **length-driven recency exponent**: `age_exp = clamp(0.25 +
        (log2(mean_len) - 6) / 2, 0.25, 1.5)`. On chat-like workloads
        (small `mean_len`) this weakens the recency penalty so
        frequency stickiness wins; on long-context workloads it
        strengthens it.
      * **recurring-prefix score boost**: a saturating sketch counts
        request-prefix hashes; at score time, a tag whose own per-
        position window matches a request prefix seen >= 3 times gets
        `score *= (1 + log2(count))`. Per-position window is hashed at
        admission so deeper blocks won't false-match the leading
        prefix.

    Drops:
      * `random_adaptive2`'s 0.7× probation tier — RQDG's 0.1× one-hit-
        leaf penalty is strictly stronger and serves the same purpose.
      * `random_adaptive2`'s hash-set ghost — RQDG's tag-FIFO ghost is
        information-richer (preserves the access_count, not just a
        promotion bit).
    """

    EMA_WINDOW = 64.0
    LOG_PIVOT = 6.0
    LOG_SCALE = 2.0
    HASH_WINDOW = 8
    HASH_COUNT_CAP = 1023
    PREFIX_BOOST_THRESHOLD = 3

    @dataclass
    class Tag:
        access_count: int
        last_access_time: int
        index: int
        block_hash: BlockHashWithGroupId
        leading_prefix_hash: int = 0

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.mean_len: float = 0.0
        self.prefix_history: dict[int, int] = {}

    @staticmethod
    def _mix64(x: int) -> int:
        x &= 0xFFFFFFFFFFFFFFFF
        x ^= x >> 30
        x = (x * 0xbf58476d1ce4e5b9) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 27
        x = (x * 0x94d049bb133111eb) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 31
        return x

    @classmethod
    def _hash_prefix(cls, block_hashes, take: int) -> int:
        h = 0xcbf29ce484222325
        n = min(take, len(block_hashes))
        for i in range(n):
            bh = block_hashes[i]
            x = int.from_bytes(bh[:8], "big", signed=False) if len(bh) >= 8 \
                else int.from_bytes(bh, "big", signed=False)
            h ^= cls._mix64(x)
            h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
        return cls._mix64(h ^ n)

    def _age_exp(self) -> float:
        log_len = math.log2(max(self.mean_len, 1.0))
        return max(
            0.25,
            min(1.5, 0.25 + (log_len - self.LOG_PIVOT) / self.LOG_SCALE),
        )

    def _prefix_boost(self, tag) -> float:
        h = getattr(tag, "leading_prefix_hash", 0)
        count = self.prefix_history.get(h, 0)
        if count >= self.PREFIX_BOOST_THRESHOLD:
            return 1.0 + math.log2(count)
        return 1.0

    def _get_eviction_score(self, tag) -> float:
        """Override the base score to use length-driven recency + boost.

        Note: this path is hit by the in-use sample loop and (via
        _get_leaf_eviction_score below) by the leaf-biased free sample
        loop too. RQDG's one-hit-leaf 0.1× multiplier is applied in
        _get_leaf_eviction_score.
        """
        recency = max(1, self.current_time - tag.last_access_time)
        cost = get_compute_intensity(tag.index)
        freq = tag.access_count
        return self._prefix_boost(tag) * freq * cost / (recency ** self._age_exp())

    def _get_leaf_eviction_score(self, tag, is_leaf: bool) -> float:
        """RQDG's leaf one-hit penalty layered on our adaptive base score."""
        base = self._get_eviction_score(tag)
        if is_leaf and tag.access_count <= 1:
            return base * self.ONE_HIT_LEAF_PENALTY
        return base

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Need our own Tag (with leading_prefix_hash). Replicate the
        # parent chain's per-block bookkeeping with the extra field.
        # Ghost-set recall (RQDG's tag-FIFO) happens here too, before
        # we touch any tags.
        for b in blocks:
            if b.block_hash is None or getattr(b, "is_null", False):
                continue
            if b.block_hash in self.ghost and b.block_hash not in self.tags:
                # RQDG path: pull preserved tag back. It has the parent
                # Tag's fields; we'll upgrade it to our subclass Tag
                # below so the leading_prefix_hash gets refreshed.
                self.tags[b.block_hash] = self.ghost.pop(b.block_hash)

        # RadixTree-level update (delegate directly to the grandparent
        # of RandomFreeBlockManager — we replicate everything else).
        RadixTreeFreeBlockManager.record_request_blocks(self, blocks)

        valid_block_hashes = []
        for b in blocks:
            if b.block_hash is None or getattr(b, "is_null", False):
                continue
            valid_block_hashes.append(b.block_hash)

        if not valid_block_hashes:
            return

        n_valid = len(valid_block_hashes)
        per_block_prefix_hash = [
            self._hash_prefix(valid_block_hashes[i:i + self.HASH_WINDOW],
                              self.HASH_WINDOW)
            for i in range(n_valid)
        ]
        request_prefix_hash = per_block_prefix_hash[0]

        valid_block_idx = 0
        for b in blocks:
            if b.block_hash is None or getattr(b, "is_null", False):
                continue
            block_idx = valid_block_idx
            block_prefix_hash = per_block_prefix_hash[block_idx]
            valid_block_idx += 1
            block_hash = b.block_hash

            existing = self.tags.get(block_hash)
            if existing is None:
                self.tags[block_hash] = (
                    RandomQuickDemotionGhostAdaptive2FreeBlockManager.Tag(
                        access_count=1,
                        last_access_time=self.current_time,
                        index=block_idx,
                        block_hash=block_hash,
                        leading_prefix_hash=block_prefix_hash,
                    )
                )
            else:
                # Recalled-from-ghost tags inherit the FIFO's preserved
                # access_count; we refresh recency/index/prefix.
                existing.access_count += 1
                existing.last_access_time = self.current_time
                existing.index = block_idx
                # If recalled from RQDG ghost, the field may be missing.
                if not hasattr(existing, "leading_prefix_hash"):
                    existing.leading_prefix_hash = block_prefix_hash
                else:
                    existing.leading_prefix_hash = block_prefix_hash

            self.current_time += 1

        # Update mean_len EMA + bump request-prefix sketch.
        seq_len = float(n_valid)
        if self.mean_len == 0.0:
            self.mean_len = seq_len
        else:
            alpha = 1.0 / self.EMA_WINDOW
            self.mean_len = self.mean_len + alpha * (seq_len - self.mean_len)

        c = self.prefix_history.get(request_prefix_hash, 0)
        if c < self.HASH_COUNT_CAP:
            self.prefix_history[request_prefix_hash] = c + 1


class RandomQuickDemotionGhostBeladyCompareFreeBlockManager(
        RandomQuickDemotionBeladyCompareFreeBlockManager,
        RandomGhostFreeBlockManager):
    """Instrumented RandomQuickDemotionGhost for ranking-correlation analysis.

    Adds the ghost queue (tag-preserving FIFO) to the BeladyCompare-
    instrumented QuickDemotion manager. Eviction decisions are unchanged
    from QuickDemotionGhost; we also record per-candidate (r, b, rec, freq)
    for offline comparison against Belady.
    """


# Ablation subclasses over score mode (multiplier vs P10 floor) and ghost
# filter (all / newcomer-by-count / newcomer-by-leaf).
class RandomQuickDemotionP10FreeBlockManager(
        RandomQuickDemotionFreeBlockManager):
    SCORE_MODE = "p10_floor"


class RandomQuickDemotionGhostCountFreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    GHOST_FILTER = "newcomer_count"


class RandomQuickDemotionGhostLeafFreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    GHOST_FILTER = "newcomer_leaf"


class RandomQuickDemotionGhostP10FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    SCORE_MODE = "p10_floor"


class RandomQuickDemotionGhostP10CountFreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    SCORE_MODE = "p10_floor"
    GHOST_FILTER = "newcomer_count"


class RandomQuickDemotionGhostP10LeafFreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    SCORE_MODE = "p10_floor"
    GHOST_FILTER = "newcomer_leaf"


# ---- Fixed-D ablation variants: scan the static denominator space ----
class RandomQuickDemotionGhostD1FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    ONE_HIT_LEAF_PENALTY = 1.0  # D = 1 (no demotion)


class RandomQuickDemotionGhostD4FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    ONE_HIT_LEAF_PENALTY = 0.25  # D = 4


class RandomQuickDemotionGhostD20FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    ONE_HIT_LEAF_PENALTY = 0.05  # D = 20


class RandomQuickDemotionGhostD40FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    ONE_HIT_LEAF_PENALTY = 0.025  # D = 40


# CANONICAL_POSITION sweep variants: shifts where in a sampled radix tree
# node the canonical block is picked from. 0.0 = first block, 0.5 = midpoint
# (default), 1.0 = last block. The canonical block's tag is what scores the
# whole node during eviction sampling.
class RandomQuickDemotionGhostCP00FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    CANONICAL_POSITION = 0.0


class RandomQuickDemotionGhostCP10FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    CANONICAL_POSITION = 0.1


class RandomQuickDemotionGhostCP25FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    CANONICAL_POSITION = 0.25


class RandomQuickDemotionGhostCP50FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    CANONICAL_POSITION = 0.5


class RandomQuickDemotionGhostCP75FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    CANONICAL_POSITION = 0.75


class RandomQuickDemotionGhostCP90FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    CANONICAL_POSITION = 0.9


class RandomQuickDemotionGhostCP100FreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    CANONICAL_POSITION = 1.0


class RandomQuickDemotionGhostAdaptiveFreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    """Adaptive one-hit leaf penalty driven by ghost readmission recency.

    Feedback loop: every time a block is recalled from the ghost queue,
    measure `recency = current_time - ghost_tag.last_access_time`. If
    `recency < cache_size`, an LRU-sized cache would have kept that block,
    so the demotion was premature. Too many such events => demotion is
    too aggressive => shrink denominator (reduce penalty strength).
    Too few => increase denominator (push harder).

    `ONE_HIT_LEAF_PENALTY` is exposed as a property so the inherited
    `_get_leaf_eviction_score` picks up the live denominator without
    any change to the eviction path.

    Subclasses select SIGNAL_MODE and CONTROL_MODE.
    """

    # "binary" | "normalized" | "histogram"
    SIGNAL_MODE = "binary"
    # "proportional" | "aimd" | "direct"
    CONTROL_MODE = "proportional"

    INIT_DENOMINATOR = 10.0
    MIN_DENOMINATOR = 1.5
    MAX_DENOMINATOR = 100.0

    UPDATE_INTERVAL = 64
    WARMUP_EVENTS = 32

    # Signal smoothing
    EMA_ALPHA = 0.05  # used by binary + normalized
    HIST_DECAY = 0.99  # used by histogram

    # Mode-specific targets (what a well-tuned demotion should produce)
    BIN_TARGET = 0.15
    NORM_TARGET = 1.0
    HIST_TARGET = 0.30

    # Control parameters
    PROP_STEP = 5.0
    AIMD_HIGH_OFFSET = 0.05  # signal > target + this => MD
    AIMD_LOW_OFFSET = 0.05   # signal < target - this => AI
    AIMD_MD_FACTOR = 0.15
    AIMD_AI_STEP = 1.0
    DIRECT_GAIN = 3.0

    # Histogram bin edges over recency / cache_size
    HIST_BIN_EDGES = (0.1, 0.25, 0.5, 1.0, 2.0)

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.denominator = float(self.INIT_DENOMINATOR)
        self._cache_size_blocks = max(1, len(blocks))
        self._events_total = 0
        self._events_since_update = 0
        self._denominator_trace: list[tuple[int, float]] = [
            (0, self.denominator)
        ]

        if self.SIGNAL_MODE == "binary":
            self._signal_ema = self.BIN_TARGET  # neutral init
        elif self.SIGNAL_MODE == "normalized":
            self._signal_ema = self.NORM_TARGET  # neutral init
        elif self.SIGNAL_MODE == "histogram":
            # len(edges) + 1 bins. Initialize uniformly so readout == ~neutral.
            n_bins = len(self.HIST_BIN_EDGES) + 1
            self._hist = [1.0 / n_bins] * n_bins
        else:
            raise ValueError(f"Unknown SIGNAL_MODE: {self.SIGNAL_MODE}")

    # Live denominator read by inherited _get_leaf_eviction_score.
    @property
    def ONE_HIT_LEAF_PENALTY(self) -> float:
        return 1.0 / self.denominator

    def _observe_readmission(self, recency: int) -> None:
        self._events_total += 1
        self._events_since_update += 1

        if self.SIGNAL_MODE == "binary":
            x = 1.0 if recency < self._cache_size_blocks else 0.0
            self._signal_ema = (self.EMA_ALPHA * x
                                + (1.0 - self.EMA_ALPHA) * self._signal_ema)
        elif self.SIGNAL_MODE == "normalized":
            x = min(recency / self._cache_size_blocks, 2.0)
            self._signal_ema = (self.EMA_ALPHA * x
                                + (1.0 - self.EMA_ALPHA) * self._signal_ema)
        else:  # histogram
            ratio = recency / self._cache_size_blocks
            bin_idx = 0
            for edge in self.HIST_BIN_EDGES:
                if ratio < edge:
                    break
                bin_idx += 1
            # Decay-all, then add mass to the hit bin.
            self._hist = [b * self.HIST_DECAY for b in self._hist]
            self._hist[bin_idx] += (1.0 - self.HIST_DECAY)

        if (self._events_total >= self.WARMUP_EVENTS
                and self._events_since_update >= self.UPDATE_INTERVAL):
            self._update_denominator()
            self._events_since_update = 0

    def _current_signal_and_target(self) -> tuple[float, float]:
        if self.SIGNAL_MODE == "binary":
            return self._signal_ema, self.BIN_TARGET
        if self.SIGNAL_MODE == "normalized":
            # Remap so higher signal = "safer to demote more" uniformly with
            # the other modes. In normalized mode, *low* EMA means blocks
            # come back early => demotion too aggressive. Flip into the
            # same convention as binary/histogram: higher = more aggressive.
            flipped = 1.0 - min(self._signal_ema / self.NORM_TARGET, 2.0)
            # target_flipped == 0 when signal == target.
            return flipped, 0.0
        # histogram: early-mass = sum of bins with ratio < 1.0
        total = sum(self._hist) or 1.0
        early_idx = 0
        for edge in self.HIST_BIN_EDGES:
            if edge >= 1.0:
                break
            early_idx += 1
        # early_idx now counts bins whose right-edge <= 1.0
        early_mass = sum(self._hist[:early_idx + 1]) / total
        return early_mass, self.HIST_TARGET

    def _update_denominator(self) -> None:
        signal, target = self._current_signal_and_target()
        error = signal - target  # >0 => too aggressive => decrease D

        if self.CONTROL_MODE == "proportional":
            self.denominator -= self.PROP_STEP * error
        elif self.CONTROL_MODE == "aimd":
            if signal > target + self.AIMD_HIGH_OFFSET:
                self.denominator *= (1.0 - self.AIMD_MD_FACTOR)
            elif signal < target - self.AIMD_LOW_OFFSET:
                self.denominator += self.AIMD_AI_STEP
            # else: deadband, no-op
        elif self.CONTROL_MODE == "direct":
            self.denominator = (self.INIT_DENOMINATOR
                                * (1.0 + self.DIRECT_GAIN * (target - signal)))
        else:
            raise ValueError(f"Unknown CONTROL_MODE: {self.CONTROL_MODE}")

        if self.denominator < self.MIN_DENOMINATOR:
            self.denominator = self.MIN_DENOMINATOR
        elif self.denominator > self.MAX_DENOMINATOR:
            self.denominator = self.MAX_DENOMINATOR

        self._denominator_trace.append(
            (self.current_time, self.denominator))

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Observe recency BEFORE super() overwrites last_access_time.
        for b in blocks:
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue
            if b.block_hash in self.ghost and b.block_hash not in self.tags:
                ghost_tag = self.ghost[b.block_hash]
                recency = self.current_time - ghost_tag.last_access_time
                self._observe_readmission(recency)
        super().record_request_blocks(blocks)


# ---- Nine ablation variants (SIGNAL_MODE x CONTROL_MODE) ---------------
class RandomQuickDemotionGhostAdaptiveBinPropFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "binary"
    CONTROL_MODE = "proportional"


class RandomQuickDemotionGhostAdaptiveBinAimdFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "binary"
    CONTROL_MODE = "aimd"


class RandomQuickDemotionGhostAdaptiveBinDirectFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "binary"
    CONTROL_MODE = "direct"


class RandomQuickDemotionGhostAdaptiveNormPropFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "normalized"
    CONTROL_MODE = "proportional"


class RandomQuickDemotionGhostAdaptiveNormAimdFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "normalized"
    CONTROL_MODE = "aimd"


class RandomQuickDemotionGhostAdaptiveNormDirectFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "normalized"
    CONTROL_MODE = "direct"


class RandomQuickDemotionGhostAdaptiveHistPropFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "histogram"
    CONTROL_MODE = "proportional"


class RandomQuickDemotionGhostAdaptiveHistAimdFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "histogram"
    CONTROL_MODE = "aimd"


class RandomQuickDemotionGhostAdaptiveHistDirectFreeBlockManager(
        RandomQuickDemotionGhostAdaptiveFreeBlockManager):
    SIGNAL_MODE = "histogram"
    CONTROL_MODE = "direct"


class RandomSmallQueueFreeBlockManager(RadixTreeFreeBlockManager):
    """RandomSmallQueue-admission Free Block Manager with sample-based main eviction.

    Uses the RandomSmallQueue admission logic (small FIFO queue + ghost queue) to
    decide which tier a block belongs to. The main tier uses sample-based
    eviction with the same score function as RandomFreeBlockManager
    (access_count / recency * compute_intensity).

    Admission:
        - If block hash is in ghost: admit to main tier
        - If small is full and cache hasn't evicted yet: admit to main tier
        - Otherwise: admit to small FIFO queue

    Evict from small FIFO:
        if freq >= move_to_main_threshold: promote to main tier
        else: evict and insert hash into ghost

    Evict from main tier:
        Sample radix tree nodes, score by access_count / recency *
        compute_intensity, evict from the lowest-scoring node.

    On eviction from the small queue, affected leaf nodes are collected and
    batch-pruned after all evictions to reduce tree pruning overhead.
    """

    SMALL_SIZE_RATIO = 0.10
    GHOST_SIZE_RATIO = 0.90
    MOVE_TO_MAIN_THRESHOLD = 2

    @dataclass
    class Tag:
        access_count: int
        last_access_time: int
        index: int
        block_hash: BlockHashWithGroupId

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self.tags: dict[BlockHashWithGroupId, RandomSmallQueueFreeBlockManager.Tag] = {}
        self.current_time = 0

        self.small_queue: deque = deque()
        self.ghost: OrderedDict = OrderedDict()
        self.freq: dict[BlockHashWithGroupId, int] = {}
        self.queue_membership: dict[BlockHashWithGroupId, str] = {}

        total_capacity = len(blocks)
        self.small_capacity = max(1, int(total_capacity * self.SMALL_SIZE_RATIO))
        self.main_capacity = total_capacity - self.small_capacity
        self.ghost_capacity = max(1, int(total_capacity * self.GHOST_SIZE_RATIO))
        self._small_count = 0
        self._main_count = 0
        self._was_main: set[BlockHashWithGroupId] = set()
        self.has_evicted = False

    def _get_eviction_score(self, tag: Tag) -> float:
        """Score for main-tier eviction. Lower score = evict first."""
        recency = max(1, self.current_time - tag.last_access_time)
        score = tag.access_count / recency
        compute_intensity = get_compute_intensity(tag.index)
        return score * compute_intensity

    def _admit_to_queue(self, block_hash: BlockHashWithGroupId):
        """Admit a block hash to small or main tier based on RandomSmallQueue policy."""
        if block_hash in self.queue_membership:
            return
        if block_hash in self._was_main:
            # Block was in main before being temporarily in use; restore it.
            self._was_main.discard(block_hash)
            self.queue_membership[block_hash] = 'main'
            self._main_count += 1
        elif block_hash in self.ghost:
            del self.ghost[block_hash]
            self.queue_membership[block_hash] = 'main'
            self._main_count += 1
        else:
            if (not self.has_evicted
                    and self._small_count >= self.small_capacity):
                self.queue_membership[block_hash] = 'main'
                self._main_count += 1
            else:
                self.small_queue.append(block_hash)
                self.queue_membership[block_hash] = 'small'
                self._small_count += 1
        self.freq.setdefault(block_hash, 0)

    def _add_to_ghost(self, block_hash: BlockHashWithGroupId):
        """Add a block hash to the ghost FIFO, evicting old entries if full."""
        if block_hash in self.ghost:
            return
        while len(self.ghost) >= self.ghost_capacity:
            dropped_hash, _ = self.ghost.popitem(last=False)
            self._ghost_drop_cleanup(dropped_hash)
        self.ghost[block_hash] = True

    def _ghost_drop_cleanup(self,
                            block_hash: BlockHashWithGroupId) -> None:
        """Hook called when a block falls off the ghost FIFO tail.
        Default behavior cleans persisted tag and freq state. Subclasses
        can extend (call super first) to add tracking like drop counters
        or signal observation.
        """
        self.tags.pop(block_hash, None)
        self.freq.pop(block_hash, None)

    def add_n(self, blocks: List[KVCacheBlock]) -> None:
        super().add_n(blocks)
        for b in blocks:
            if b.block_hash is None:
                continue
            if b.block_hash in self.blocks_not_in_tree:
                continue
            self._admit_to_queue(b.block_hash)

    def remove(self, block: KVCacheBlock) -> None:
        if block.block_hash is not None:
            membership = self.queue_membership.pop(block.block_hash, None)
            if membership == 'small':
                self._small_count -= 1
            elif membership == 'main':
                self._main_count -= 1
                self._was_main.add(block.block_hash)
        super().remove(block)

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Track blocks that might move from blocks_not_in_tree to tree
        pre_not_in_tree = set()
        for b in blocks:
            if (b.block_hash is not None
                    and b.block_hash in self.blocks_not_in_tree):
                pre_not_in_tree.add(b.block_hash)

        super().record_request_blocks(blocks)

        # Admit blocks that moved from blocks_not_in_tree to radix tree
        for bh in pre_not_in_tree:
            if bh not in self.blocks_not_in_tree:
                self._admit_to_queue(bh)

        # Update tags (for main-tier scoring) and freq (for small-tier promotion)
        valid_block_idx = 0
        for b in blocks:
            if b.block_hash is None:
                continue

            block_idx = valid_block_idx
            valid_block_idx += 1
            block_hash = b.block_hash

            if block_hash not in self.tags:
                self.tags[block_hash] = RandomSmallQueueFreeBlockManager.Tag(
                    access_count=1,
                    last_access_time=self.current_time,
                    index=block_idx,
                    block_hash=block_hash,
                )
            else:
                tag = self.tags[block_hash]
                tag.access_count += 1
                tag.last_access_time = self.current_time
                tag.index = block_idx

            if block_hash in self.freq:
                self.freq[block_hash] += 1

            self.current_time += 1

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        return self._evict_with_admission(n)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        # Last-resort fallback: when admission-based eviction leaves us
        # short (e.g. admission-tier counters fall out of sync with the
        # actual free pool, or sampling fails to surface evictable nodes),
        # scan `hashed_free_block_map` directly and evict any block whose
        # radix-tree slot is not in use. This guarantees get_free_blocks_n
        # never silently returns fewer blocks than requested.
        if n <= 0:
            return []
        evicted_blocks: List[KVCacheBlock] = []
        for block_hash in list(self.hashed_free_block_map.keys()):
            if len(evicted_blocks) >= n:
                break
            block = self.hashed_free_block_map.get(block_hash)
            if block is None or block.ref_cnt != 0:
                continue
            node_info = self.radix_tree._node_map.get(block_hash)
            if node_info is None:
                continue
            node, idx = node_info
            if node.in_use[idx]:
                continue

            evicted_block, leaf = self._evict_single_block(block_hash)
            if evicted_block is None:
                continue

            membership = self.queue_membership.pop(block_hash, None)
            if membership == 'small':
                self._small_count -= 1
            elif membership == 'main':
                self._main_count -= 1
            self.freq.pop(block_hash, None)
            self._was_main.discard(block_hash)
            self.tags.pop(block_hash, None)
            self._num_free_blocks -= 1
            evicted_blocks.append(evicted_block)

            if leaf is not None and not leaf.children:
                all_evicted = True
                for i, child_hash in enumerate(leaf.block_hashes):
                    if (self.hashed_free_block_map.get(child_hash)
                            or leaf.in_use[i]):
                        all_evicted = False
                        break
                if all_evicted:
                    self.radix_tree.evict(leaf)
        return evicted_blocks

    def _is_block_evictable(self, block_hash: BlockHashWithGroupId) -> bool:
        """Check if a block can be evicted from the cache."""
        block = self.hashed_free_block_map.get(block_hash)
        if block is None or block.ref_cnt != 0:
            return False
        node_info = self.radix_tree._node_map.get(block_hash)
        if node_info is None:
            return False
        node, idx = node_info
        return not node.in_use[idx]

    def _evict_single_block(self, block_hash: BlockHashWithGroupId):
        """Remove a single block from radix tree tracking structures.

        Returns (block, node) or (None, None) if not found.
        """
        block = self.hashed_free_block_map.pop(block_hash, None)
        if block is None:
            return None, None
        self.free_blocks_queue_in_radix_tree.remove(block)
        node_info = self.radix_tree._node_map.get(block_hash)
        node = node_info[0] if node_info else None
        return block, node

    def _should_evict_from_small(self) -> bool:
        # In-flight blocks are temporarily detached via remove() — they
        # can't be evicted while ref-counted, and their tier counter is
        # decremented. Routing on `_small_count > small_capacity` (instead
        # of the prior `_main_count > main_capacity or _small_count == 0`)
        # avoids draining the small queue prematurely when in-flight
        # requests have transiently deflated _small_count below capacity.
        return self._small_count > self.small_capacity

    def _evict_with_admission(self, n: int) -> List[KVCacheBlock]:
        """Evict n blocks using RandomSmallQueue admission with sample-based main.

        Leaf nodes from small queue evictions are collected and batch-pruned
        at the end to reduce overhead.
        """
        self.has_evicted = True
        evicted_blocks: List[KVCacheBlock] = []
        leaves_from_small: set[RadixTreeNode] = set()

        while len(evicted_blocks) < n:
            if self._small_count == 0 and self._main_count == 0:
                break

            remaining = n - len(evicted_blocks)
            if self._should_evict_from_small():
                single = self._evict_one_from_small(leaves_from_small)
                if single:
                    result = [single]
                else:
                    result = self._evict_from_main(remaining)
            else:
                result = self._evict_from_main(remaining)
                if not result:
                    single = self._evict_one_from_small(leaves_from_small)
                    result = [single] if single else []

            if not result:
                break
            for block in result:
                evicted_blocks.append(block)
                self._num_free_blocks -= 1

        # Batch prune leaf nodes from small queue evictions.
        # Some leaves may have already been evicted by _evict_from_main
        # or by parent merging during a prior evict() call in this loop,
        # so verify the node is still in the tree before pruning.
        for leaf in leaves_from_small:
            if leaf.block_hashes[0] not in self.radix_tree._node_map:
                continue
            if not leaf.children:
                all_evicted = True
                for i, child_hash in enumerate(leaf.block_hashes):
                    if (self.hashed_free_block_map.get(child_hash)
                            or leaf.in_use[i]):
                        all_evicted = False
                        break
                if all_evicted:
                    self.radix_tree.evict(leaf)

        return evicted_blocks

    def _evict_one_from_small(
        self, leaves_collector: set,
    ) -> KVCacheBlock | None:
        """Try to evict one block from the small FIFO queue.

        Blocks with freq >= threshold are promoted to main tier.
        Evicted block hashes are added to the ghost queue.
        Affected leaf nodes are appended to leaves_collector for batch pruning.
        """
        while self.small_queue:
            block_hash = self.small_queue.popleft()

            if self.queue_membership.get(block_hash) != 'small':
                continue

            if not self._is_block_evictable(block_hash):
                self.queue_membership.pop(block_hash, None)
                self._small_count -= 1
                continue

            f = self.freq.get(block_hash, 0)
            if f >= self.MOVE_TO_MAIN_THRESHOLD:
                # Promote to main tier. freq is NOT reset on promotion;
                # the lifetime access count persists so subclasses that
                # read freq for main-tier decisions (e.g. BlockEvent's
                # low-freq main-eviction signal) see the true total.
                self.queue_membership[block_hash] = 'main'
                self._small_count -= 1
                self._main_count += 1
                continue

            # Truly evict from small. Tag and freq are preserved while
            # the block sits in the ghost FIFO; cleanup happens when the
            # block falls off the ghost tail (see _ghost_drop_cleanup).
            self._small_count -= 1
            del self.queue_membership[block_hash]
            self._add_to_ghost(block_hash)

            block, node = self._evict_single_block(block_hash)
            if block is None:
                continue

            if node is not None and not node.children:
                leaves_collector.add(node)

            return block

        return None

    def _get_canonical_block_for_main(
        self, node: RadixTreeNode, n: int | None = None,
    ) -> BlockHashWithGroupId | None:
        """Get canonical block for a node, restricted to main-tier blocks.

        If n is provided, position the canonical search near the midpoint of
        the first min(n, node_size) blocks.
        """
        total = len(node.block_hashes)
        if total == 0:
            return None
        if n is not None:
            effective = min(n, total)
            start = effective // 2
        else:
            start = int(total * self.CANONICAL_POSITION)
        start = min(max(start, 0), total - 1)
        for i in sorted(range(total), key=lambda x: abs(x - start)):
            child_hash = node.block_hashes[i]
            if (self._is_evictable(child_hash)
                    and self.queue_membership.get(child_hash) == 'main'):
                return child_hash
        return None

    def _evict_from_main(self, n: int) -> List[KVCacheBlock]:
        """Sample-based eviction from main tier.

        Samples radix tree nodes, scores main-tier blocks using
        access_count / recency * compute_intensity, and evicts from
        the lowest-scoring node.
        """
        evicted_blocks: List[KVCacheBlock] = []
        ASSOCIATIVITY = 128
        tried_nodes: set[int] = set()
        score_cache: dict[int, float] = {}

        while n > 0:
            sampled_nodes = self.radix_tree.sample_free_radix_tree_nodes(
                ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes
                           if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    canonical_block = self._get_canonical_block_for_main(node, n)
                    score = float('inf')
                    if (canonical_block is not None
                            and canonical_block in self.tags):
                        tag = self.tags[canonical_block]
                        score = self._get_eviction_score(tag)
                    score_cache[id(node)] = score

                if score < victim_score:
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            blocks = self._get_free_blocks_from_radix_tree_nodes(
                victim_node, n)

            if not blocks:
                tried_nodes.add(id(victim_node))
            else:
                evicted_blocks.extend(blocks)
                n -= len(blocks)
                score_cache.pop(id(victim_node), None)

        return evicted_blocks

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        """Evict blocks from a radix tree node and clean up RandomSmallQueue state."""
        evictable_hashes = []
        for i, child_hash in enumerate(node.block_hashes):
            if len(evictable_hashes) == max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if (block_to_evict and block_to_evict.ref_cnt == 0
                    and not node.in_use[i]):
                evictable_hashes.append(child_hash)

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(
            node, max_blocks)

        for child_hash in evictable_hashes:
            membership = self.queue_membership.pop(child_hash, None)
            if membership == 'small':
                self._small_count -= 1
            elif membership == 'main':
                self._main_count -= 1
            self.freq.pop(child_hash, None)
            self._was_main.discard(child_hash)
            if child_hash in self.tags:
                del self.tags[child_hash]

        return evicted_blocks


class RandomSmallQueue02FreeBlockManager(RandomSmallQueueFreeBlockManager):
    SMALL_SIZE_RATIO = 0.02


class RandomSmallQueue05FreeBlockManager(RandomSmallQueueFreeBlockManager):
    SMALL_SIZE_RATIO = 0.05


class RandomSmallQueue15FreeBlockManager(RandomSmallQueueFreeBlockManager):
    SMALL_SIZE_RATIO = 0.15


class RandomSmallQueue20FreeBlockManager(RandomSmallQueueFreeBlockManager):
    SMALL_SIZE_RATIO = 0.20


class RandomSmallQueue30FreeBlockManager(RandomSmallQueueFreeBlockManager):
    SMALL_SIZE_RATIO = 0.30


class RandomSmallQueue40FreeBlockManager(RandomSmallQueueFreeBlockManager):
    SMALL_SIZE_RATIO = 0.40


class RandomSmallQueue50FreeBlockManager(RandomSmallQueueFreeBlockManager):
    SMALL_SIZE_RATIO = 0.50


class RandomSmallQueueAdaptiveFreeBlockManager(RandomSmallQueueFreeBlockManager):
    """Adaptive RandomSmallQueue: online-tunes SMALL_SIZE_RATIO from
    ghost-readmit recency feedback. Mirrors the design of
    RandomQuickDemotionGhostAdaptive (see
    docs/algorithms/RandomQuickDemotionGhostAdaptive.md).

    Hook: when a block hash is admitted from the ghost FIFO, observe
    recency = current_time - eviction_time. The ghost OrderedDict stores
    eviction time (instead of the parent's True placeholder) so the
    recency is recoverable.

    Signal modes feed the recency stream into a per-mode estimator:
        - binary:     EMA[1[recency < cache_size]]; aligned target = high
        - normalized: EMA[min(recency/cs, 2.0)]; aligned via 2.0-signal
        - histogram:  EWMA histogram over recency/cs; aligned = early mass

    Sign convention (after alignment): error = signal - target. error > 0
    means too many premature evictions, so SMALL_SIZE_RATIO should grow
    (more audition slots). error < 0 means readmits arrive late, so
    SMALL_SIZE_RATIO can shrink (less waste on audition).

    Control modes update _small_ratio every UPDATE_INTERVAL readmissions:
        - proportional: ratio += STEP * error
        - aimd:         signal>target+ε → +AI_STEP; <target-ε → ×(1-MD)
        - direct:       ratio = INIT * (1 + GAIN * error) (re-anchored)

    GHOST_SIZE_RATIO and MOVE_TO_MAIN_THRESHOLD are inherited from the
    parent (0.90 and 2 respectively) — only the small-queue gate is
    adaptive.
    """

    INIT_RATIO = 0.10
    MIN_RATIO = 0.02
    MAX_RATIO = 0.50
    UPDATE_INTERVAL = 64
    WARMUP_EVENTS = 32
    EMA_ALPHA = 0.05
    HIST_DECAY = 0.99

    SIGNAL_MODE = "binary"
    CONTROL_MODE = "proportional"

    BIN_TARGET = 0.30
    NORM_TARGET = 1.0
    HIST_TARGET = 0.30

    PROP_STEP = 0.05
    AIMD_HIGH_OFFSET = 0.05
    AIMD_LOW_OFFSET = 0.05
    AIMD_MD_FACTOR = 0.10
    AIMD_AI_STEP = 0.02
    DIRECT_GAIN = 5.0

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self._total_capacity = len(blocks)
        self._small_ratio = self.INIT_RATIO
        self._refresh_capacities()

        self._readmit_count = 0
        self._signal_ema = self._initial_signal()
        self._hist = [0.0] * 6
        self._ratio_trace: list[tuple[int, float]] = []

    def _initial_signal(self) -> float:
        if self.SIGNAL_MODE == "binary":
            return self.BIN_TARGET
        if self.SIGNAL_MODE == "normalized":
            # Stored raw (un-aligned). Aligned = 2.0 - raw.
            return 2.0 - self.NORM_TARGET
        return 0.0

    def _refresh_capacities(self) -> None:
        new_cap = max(1, int(self._total_capacity * self._small_ratio))
        self.small_capacity = new_cap
        self.main_capacity = self._total_capacity - new_cap

    def _add_to_ghost(self, block_hash: BlockHashWithGroupId) -> None:
        if block_hash in self.ghost:
            return
        while len(self.ghost) >= self.ghost_capacity:
            dropped_hash, _ = self.ghost.popitem(last=False)
            self._ghost_drop_cleanup(dropped_hash)
        self.ghost[block_hash] = self.current_time

    def _admit_to_queue(self, block_hash: BlockHashWithGroupId) -> None:
        # Observe ghost-readmission *before* super deletes the entry.
        if (block_hash not in self.queue_membership
                and block_hash not in self._was_main
                and block_hash in self.ghost):
            evict_time = self.ghost[block_hash]
            if not isinstance(evict_time, bool):
                recency = self.current_time - evict_time
                self._observe_readmission(recency)
        super()._admit_to_queue(block_hash)

    def _observe_readmission(self, recency: int) -> None:
        self._readmit_count += 1
        cs = max(1, self._total_capacity)

        if self.SIGNAL_MODE == "binary":
            is_early = 1.0 if recency < self._total_capacity else 0.0
            self._signal_ema = ((1 - self.EMA_ALPHA) * self._signal_ema
                                + self.EMA_ALPHA * is_early)
        elif self.SIGNAL_MODE == "normalized":
            normalized = min(recency / cs, 2.0)
            self._signal_ema = ((1 - self.EMA_ALPHA) * self._signal_ema
                                + self.EMA_ALPHA * normalized)
        elif self.SIGNAL_MODE == "histogram":
            normalized = recency / cs
            edges = [0.1, 0.25, 0.5, 1.0, 2.0, float('inf')]
            idx = 0
            for i, edge in enumerate(edges):
                if normalized < edge:
                    idx = i
                    break
            for i in range(len(self._hist)):
                self._hist[i] *= self.HIST_DECAY
            self._hist[idx] += 1.0

        if (self._readmit_count >= self.WARMUP_EVENTS
                and self._readmit_count % self.UPDATE_INTERVAL == 0):
            self._update_ratio()

    def _aligned_signal_and_target(self) -> tuple[float, float]:
        """Return (signal, target) where error = signal - target satisfies
        error > 0 ⇒ grow SMALL_SIZE_RATIO."""
        if self.SIGNAL_MODE == "binary":
            return self._signal_ema, self.BIN_TARGET
        if self.SIGNAL_MODE == "normalized":
            # Raw signal grows with late recency (= "shrink"). Flip so
            # higher aligned signal = earlier readmits = "grow".
            return (2.0 - self._signal_ema), self.NORM_TARGET
        if self.SIGNAL_MODE == "histogram":
            total = sum(self._hist) or 1.0
            # Bins 0..3 cover normalized recency in [0, 1) — "early mass".
            early_mass = sum(self._hist[:4]) / total
            return early_mass, self.HIST_TARGET
        return 0.0, 0.0

    def _update_ratio(self) -> None:
        signal, target = self._aligned_signal_and_target()
        error = signal - target

        if self.CONTROL_MODE == "proportional":
            self._small_ratio = self._small_ratio + self.PROP_STEP * error
        elif self.CONTROL_MODE == "aimd":
            if signal > target + self.AIMD_HIGH_OFFSET:
                self._small_ratio += self.AIMD_AI_STEP
            elif signal < target - self.AIMD_LOW_OFFSET:
                self._small_ratio *= (1.0 - self.AIMD_MD_FACTOR)
        elif self.CONTROL_MODE == "direct":
            self._small_ratio = self.INIT_RATIO * (1.0 + self.DIRECT_GAIN * error)

        self._small_ratio = max(self.MIN_RATIO,
                                min(self.MAX_RATIO, self._small_ratio))
        self._refresh_capacities()
        self._ratio_trace.append((self.current_time, self._small_ratio))


class RandomSmallQueueAdaptiveBinPropFreeBlockManager(
        RandomSmallQueueAdaptiveFreeBlockManager):
    SIGNAL_MODE = "binary"
    CONTROL_MODE = "proportional"


class RandomSmallQueueAdaptiveBinAimdFreeBlockManager(
        RandomSmallQueueAdaptiveFreeBlockManager):
    SIGNAL_MODE = "binary"
    CONTROL_MODE = "aimd"


class RandomSmallQueueAdaptiveNormPropFreeBlockManager(
        RandomSmallQueueAdaptiveFreeBlockManager):
    SIGNAL_MODE = "normalized"
    CONTROL_MODE = "proportional"


class RandomSmallQueueAdaptiveHistDirectFreeBlockManager(
        RandomSmallQueueAdaptiveFreeBlockManager):
    SIGNAL_MODE = "histogram"
    CONTROL_MODE = "direct"


class RandomSmallQueueAdaptiveHitRateFreeBlockManager(
        RandomSmallQueueFreeBlockManager):
    """Adaptive RandomSmallQueue using **per-slot hit density** as the
    feedback signal.

    Motivation: ghost-readmit recency (used by the BinProp/NormProp/Hist
    variants) is biased upward on high-reuse workloads (e.g. qwen_traceB),
    because *any* readmit on such a workload tends to be 'early' simply
    because the workload itself is reuse-heavy — not because the small
    queue is too small. This pushed the ratio to MAX on traceB and gave up
    1.6–3.1 pp vs fixed `RandomSmallQueue` (0.10).

    Per-slot hit density compares whether the small tier or the main tier
    is paying off *per cache slot it occupies*:

        s_rate = hits_on_small_blocks / small_capacity
        m_rate = hits_on_main_blocks  / main_capacity
        error  = (s_rate - m_rate) / (s_rate + m_rate)

        error > 0 → small tier denser → grow ratio
        error < 0 → main tier denser  → shrink ratio

    On qwen_traceB, hits land overwhelmingly on main-tier (promoted)
    blocks, so error < 0 → ratio shrinks toward MIN, matching the static
    optimum (~0.10). On qwen_thinking, hits split more evenly so error
    can be positive → ratio grows. The signal is workload-correct in
    *both* directions without a recency-related bias.

    Hit accounting: snapshot pre-record `queue_membership` for each block
    in the request; after `super().record_request_blocks()` updates tags,
    increment `_small_hits` / `_main_hits` based on the snapshot. A new
    block (no prior membership) was a miss and isn't counted on either
    side. Counters decay by `DECAY` at each control tick so old data
    doesn't dominate.
    """

    INIT_RATIO = 0.10
    # MIN_RATIO is anchored at INIT_RATIO so adaptive can only *grow* the
    # ratio above the parent's fixed default, never shrink below it. This
    # is the "≥ fixed RandomSmallQueue" guarantee: on workloads where the
    # signal would otherwise push toward MIN (e.g. qwen_traceB or
    # GLM-5-TEE 16k, where main-tier hit density dominates), the ratio
    # holds at the static optimum instead of collapsing.
    MIN_RATIO = 0.10
    MAX_RATIO = 0.50
    UPDATE_INTERVAL = 1024
    WARMUP_EVENTS = 1024
    PROP_STEP = 0.02
    DECAY = 0.5
    GROWTH_MARGIN = 3.0

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self._total_capacity = len(blocks)
        self._small_ratio = self.INIT_RATIO
        self._refresh_capacities()
        self._small_hits = 0.0
        self._main_hits = 0.0
        self._access_count = 0
        self._last_tick_at = 0
        # Parent tracks _was_main but not _was_small (parent's design discards
        # small-tier blocks fully on remove). Track here for hit accounting.
        self._was_small: set[BlockHashWithGroupId] = set()
        self._ratio_trace: list[tuple[int, float]] = []

    def _refresh_capacities(self) -> None:
        new_cap = max(1, int(self._total_capacity * self._small_ratio))
        self.small_capacity = new_cap
        self.main_capacity = self._total_capacity - new_cap

    def remove(self, block: KVCacheBlock) -> None:
        # Track small-tier removals so record_request_blocks can recognize
        # small-tier hits (parent removes the queue_membership entry on
        # remove and only retains _was_main).
        if (block.block_hash is not None
                and self.queue_membership.get(block.block_hash) == 'small'):
            self._was_small.add(block.block_hash)
        super().remove(block)

    def _admit_to_queue(self, block_hash: BlockHashWithGroupId) -> None:
        # Mirror _was_main's discard pattern when re-admitting from
        # the small-removed-but-not-evicted state.
        self._was_small.discard(block_hash)
        super()._admit_to_queue(block_hash)

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Snapshot pre-record tier-of-hit for each block. After pool.touch
        # removed the block, queue_membership[h] is gone but _was_main /
        # _was_small flag the prior tier.
        pre_small: set[BlockHashWithGroupId] = set()
        pre_main: set[BlockHashWithGroupId] = set()
        for b in blocks:
            if b.block_hash is None:
                continue
            bh = b.block_hash
            m = self.queue_membership.get(bh)
            if m == 'small' or bh in self._was_small:
                pre_small.add(bh)
            elif m == 'main' or bh in self._was_main:
                pre_main.add(bh)

        super().record_request_blocks(blocks)

        for b in blocks:
            if b.block_hash is None:
                continue
            bh = b.block_hash
            if bh in pre_small:
                self._small_hits += 1
            elif bh in pre_main:
                self._main_hits += 1
            self._access_count += 1

        if (self._access_count >= self.WARMUP_EVENTS
                and self._access_count - self._last_tick_at
                    >= self.UPDATE_INTERVAL):
            self._last_tick_at = self._access_count
            # Skip during cold-start: main is empty during cache fill, so
            # m_rate would be 0 and the controller would grow unhelpfully.
            # Wait until both tiers have non-trivial occupancy.
            if (self._small_count >= 0.5 * self.small_capacity
                    and self._main_count >= 0.5 * self.main_capacity):
                self._update_ratio_hit_density()

    def _update_ratio_hit_density(self) -> None:
        s_rate = self._small_hits / max(1, self.small_capacity)
        m_rate = self._main_hits / max(1, self.main_capacity)
        total = s_rate + m_rate
        if total > 0:
            # Asymmetric control:
            # - Grow only when small density exceeds main by GROWTH_MARGIN
            #   (suppresses false-positive growth on chutes-style traces).
            # - Always allow shrinking back toward MIN_RATIO=INIT_RATIO so
            #   any earlier overshoot above 0.10 can decay back to 0.10
            #   when the workload signal flips.
            if s_rate > self.GROWTH_MARGIN * m_rate:
                error = (s_rate - self.GROWTH_MARGIN * m_rate) / total
                self._small_ratio = min(
                    self.MAX_RATIO,
                    self._small_ratio + self.PROP_STEP * error)
            elif m_rate > s_rate:
                error = (s_rate - m_rate) / total  # negative
                self._small_ratio = max(
                    self.MIN_RATIO,
                    self._small_ratio + self.PROP_STEP * error)
            self._refresh_capacities()
        # Decay so the signal tracks recent behavior, not the whole trace.
        self._small_hits *= self.DECAY
        self._main_hits *= self.DECAY
        self._ratio_trace.append((self.current_time, self._small_ratio))


class RandomSmallQueueAdaptiveGhostRatioFreeBlockManager(
        RandomSmallQueueFreeBlockManager):
    """Adaptive RandomSmallQueue using **ghost-utilization fraction** as
    feedback.

    Signal: of all ghost-FIFO entries that *terminated* (either readmitted
    to main, or dropped from the tail without being reused), what
    fraction was readmitted?

        readmit_frac = readmits / (readmits + drops)

    BinProp's flaw was using only readmit *recency*: any block that
    happens to come back gets recorded as evidence to grow, even when
    most other ghost entries are simultaneously aging out as
    confirmed-dead one-hits. Including drops as the denominator lets the
    controller see the *full* distribution — readmits relative to true
    one-hit volume.

    Asymmetric controller with deadband (the "tolerance" the user asked
    for):
        readmit_frac > GROWTH_TARGET  → grow
        readmit_frac < SHRINK_TARGET  → shrink (toward MIN)
        otherwise                      → no change

    `MIN_RATIO = INIT_RATIO = 0.10` so the algorithm never falls below
    the static `RandomSmallQueue` default. The deadband means small
    fluctuations are tolerated — only sustained, clear evidence moves
    the ratio.

    Hooks:
      - `_add_to_ghost` increments drop count for every ghost-FIFO pop.
      - `_admit_to_queue` increments readmit count whenever a block
        arrives via the `block_hash in self.ghost` branch.
    """

    INIT_RATIO = 0.10
    MIN_RATIO = 0.10
    MAX_RATIO = 0.50
    UPDATE_INTERVAL = 256
    WARMUP_EVENTS = 256
    EMA_ALPHA = 0.05
    PROP_STEP = 0.20

    GROWTH_TARGET = 0.30
    SHRINK_TARGET = 0.10

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self._total_capacity = len(blocks)
        self._small_ratio = self.INIT_RATIO
        self._refresh_capacities()
        # EMA of "this ghost event was a readmit" (1.0/0.0). Initialize at
        # the upper deadband boundary so the system idles at "no change"
        # before the controller has data.
        self._readmit_frac_ema = self.GROWTH_TARGET
        self._event_count = 0
        self._last_tick_at = 0
        self._ghost_readmits = 0
        self._ghost_drops = 0
        self._ratio_trace: list[tuple[int, float]] = []

    def _refresh_capacities(self) -> None:
        new_cap = max(1, int(self._total_capacity * self._small_ratio))
        self.small_capacity = new_cap
        self.main_capacity = self._total_capacity - new_cap

    def _add_to_ghost(self, block_hash: BlockHashWithGroupId) -> None:
        if block_hash in self.ghost:
            return
        while len(self.ghost) >= self.ghost_capacity:
            dropped_hash, _ = self.ghost.popitem(last=False)
            self._ghost_drop_cleanup(dropped_hash)
        self.ghost[block_hash] = True

    def _ghost_drop_cleanup(self,
                            block_hash: BlockHashWithGroupId) -> None:
        super()._ghost_drop_cleanup(block_hash)
        self._ghost_drops += 1
        self._observe_ghost_event(was_readmit=False)

    def _admit_to_queue(self, block_hash: BlockHashWithGroupId) -> None:
        if (block_hash not in self.queue_membership
                and block_hash not in self._was_main
                and block_hash in self.ghost):
            self._ghost_readmits += 1
            self._observe_ghost_event(was_readmit=True)
        super()._admit_to_queue(block_hash)

    def _observe_ghost_event(self, was_readmit: bool) -> None:
        self._event_count += 1
        sample = 1.0 if was_readmit else 0.0
        self._readmit_frac_ema = (
            (1 - self.EMA_ALPHA) * self._readmit_frac_ema
            + self.EMA_ALPHA * sample)
        if (self._event_count >= self.WARMUP_EVENTS
                and self._event_count - self._last_tick_at
                    >= self.UPDATE_INTERVAL):
            self._last_tick_at = self._event_count
            self._update_ratio()

    def _update_ratio(self) -> None:
        signal = self._readmit_frac_ema
        if signal > self.GROWTH_TARGET:
            error = signal - self.GROWTH_TARGET
            self._small_ratio = min(
                self.MAX_RATIO,
                self._small_ratio + self.PROP_STEP * error)
            self._refresh_capacities()
        elif signal < self.SHRINK_TARGET:
            error = signal - self.SHRINK_TARGET  # negative
            self._small_ratio = max(
                self.MIN_RATIO,
                self._small_ratio + self.PROP_STEP * error)
            self._refresh_capacities()
        # Deadband [SHRINK_TARGET, GROWTH_TARGET]: no update — tolerance.
        self._ratio_trace.append((self.current_time, self._small_ratio))


class RandomSmallQueueAdaptiveGhostRatioCappedFreeBlockManager(
        RandomSmallQueueAdaptiveGhostRatioFreeBlockManager):
    """Same controller as parent (original GhostRatio) but with reduced
    growth headroom and slower step.

    Motivation: the parent variant beats LRU on every cache size of
    qwen_thinking (the only adaptive variant to do so), but pays
    1.4–3.7 pp on chutes traces because the ratio occasionally bursts
    to MAX=0.50 and brief excursions damage main. Capping MAX at 0.30
    bounds the damage on chutes without giving up the thinking gain
    entirely (the static `SQ20`/`SQ30` numbers on thinking are 30.5 /
    29.7 — well above SQ10's 30.2 at 16k). Smaller `PROP_STEP=0.10`
    makes brief spikes smaller still.
    """

    MAX_RATIO = 0.30
    PROP_STEP = 0.10


class RandomSmallQueueAdaptiveGhostRatioLongFreeBlockManager(
        RandomSmallQueueAdaptiveGhostRatioFreeBlockManager):
    """Same signal as parent, but with **long-term averaging** so the
    controller only acts on sustained workload patterns, not transient
    bursts.

    Differences vs the original GhostRatio:
    - `EMA_ALPHA = 0.005` (was 0.05) — effective time constant ~10×
      longer. The signal smooths over ~50k ghost events.
    - `UPDATE_INTERVAL = 1024` — fewer ticks; each averages more data.
    - `PROP_STEP = 0.05` — small per-tick changes.

    No hysteresis (the EMA already filters transients). The intent is
    to stay at MIN unless the workload *sustains* high readmit
    fraction over a long window.
    """

    UPDATE_INTERVAL = 1024
    WARMUP_EVENTS = 1024
    EMA_ALPHA = 0.005
    PROP_STEP = 0.05


class RandomSmallQueueAdaptiveGhostRatioStableFreeBlockManager(
        RandomSmallQueueAdaptiveGhostRatioFreeBlockManager):
    """Stability-tuned variant of AdaptiveGhostRatio.

    Same signal (ghost readmit / (readmit + drop)) as the parent, but
    with several knobs tightened to *suppress transient growth*. The
    parent's trajectory study showed identical aggregate behavior (mean
    ratio ≈ 0.15, ~12% time above 0.30) on qwen_thinking 16k vs
    GLM-5-TEE 16k, with opposite outcomes (+1.7 pp on thinking, -3.2 pp
    on GLM). The aggregate is the same but **the brief growth
    excursions** are what hurt chutes — every spike to 0.30+ pulls
    real popular blocks out of main on chutes traces, costing 3+ pp.

    Stability changes vs parent:

    1. **Hysteresis**: require `HYSTERESIS_TICKS=3` consecutive
       same-direction signal events before *any* update fires. Single-
       tick spikes are filtered out completely.
    2. **Smaller step**: `PROP_STEP=0.05` (was 0.20). Even when growth
       fires, it moves slowly.
    3. **Longer averaging**: `UPDATE_INTERVAL=1024`, `WARMUP_EVENTS=
       1024` (both 4× parent), so each tick averages more events and
       noise is reduced.
    4. **Lower EMA alpha**: `EMA_ALPHA=0.02` (was 0.05) — the signal
       EMA itself is smoother across ticks.

    Net effect: the controller *only* moves the ratio when the
    readmit/drop signal exceeds the deadband for 3 sustained ticks
    (≈3072 ghost events), and even then changes ratio by ≤ ~0.05 per
    move. On chutes traces, transient signal noise no longer triggers
    growth; on qwen_thinking, sustained growth still happens (the
    workload provides plenty of consistent above-deadband ticks).
    """

    UPDATE_INTERVAL = 512
    WARMUP_EVENTS = 512
    EMA_ALPHA = 0.03
    PROP_STEP = 0.10
    HYSTERESIS_TICKS = 2

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self._consecutive_grow = 0
        self._consecutive_shrink = 0

    def _update_ratio(self) -> None:
        signal = self._readmit_frac_ema
        if signal > self.GROWTH_TARGET:
            self._consecutive_grow += 1
            self._consecutive_shrink = 0
            if self._consecutive_grow >= self.HYSTERESIS_TICKS:
                error = signal - self.GROWTH_TARGET
                self._small_ratio = min(
                    self.MAX_RATIO,
                    self._small_ratio + self.PROP_STEP * error)
                self._refresh_capacities()
        elif signal < self.SHRINK_TARGET:
            self._consecutive_shrink += 1
            self._consecutive_grow = 0
            if self._consecutive_shrink >= self.HYSTERESIS_TICKS:
                error = signal - self.SHRINK_TARGET  # negative
                self._small_ratio = max(
                    self.MIN_RATIO,
                    self._small_ratio + self.PROP_STEP * error)
                self._refresh_capacities()
        else:
            # Signal in deadband — reset both counters (stops drift toward
            # either direction from intermittent crossings).
            self._consecutive_grow = 0
            self._consecutive_shrink = 0
        self._ratio_trace.append((self.current_time, self._small_ratio))


class RandomSmallQueueAdaptiveBlockEventFreeBlockManager(
        RandomSmallQueueFreeBlockManager):
    """Adaptive small-queue ratio driven by **block-granularity events**.

    Per-event signal (no EMAs, no aggregation windows for the signal
    itself):

    - **Ghost readmission** (a block in the ghost FIFO is re-requested
      and re-admitted to main): *score += 1* — the small queue threw
      this block away too early; the audition window should be longer.
    - **Main eviction with freq ≤ MOVE_TO_MAIN_THRESHOLD** (a block in
      main is evicted having been accessed at most twice since its
      most recent (re)admission): *score -= 1* — the main tier slot
      was wasted on a block that didn't earn its promotion; the
      audition was too lenient.

    The score is accumulated per block-level event, but the actual
    ratio update — which requires recomputing `small_capacity` and
    `main_capacity` — is deferred and only applied every
    `UPDATE_INTERVAL` events. This amortizes the resize cost.

    `MIN_RATIO = INIT_RATIO = 0.10` so the algorithm can only grow
    above the static `RandomSmallQueue` default.
    """

    INIT_RATIO = 0.10
    MIN_RATIO = 0.05
    MAX_RATIO = 1.00
    UPDATE_INTERVAL = 256
    STEP = 0.05  # max ratio change per interval (when score = ±event_count)
    DECAY = 0.5  # damp the score across intervals
    # Sigmoid steepness for the ghost-position weight on +1 readmits.
    # weight = 1 / (1 + exp(SIG_STEEPNESS * (x - 0.5))),
    # where x = rank_from_newest / ghost_size in [0, 1].
    # STEEPNESS=10 gives weight≈0.99 at x=0, weight≈0.5 at x=0.5,
    # weight≈0.007 at x=1 — clear S-curve with flat tails.
    SIG_STEEPNESS = 10.0

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        self._total_capacity = len(blocks)
        self._small_ratio = self.INIT_RATIO
        self._refresh_capacities()
        self._score = 0.0
        self._event_count = 0
        self._ratio_trace: list[tuple[int, float]] = []
        # Insertion-sequence counter for ghost entries. Each ghost entry
        # stores the seq number it received on insertion; the gap
        # `_ghost_insert_seq - block_seq` is the block's "rank from
        # newest" (0 = most recent insert, ghost_size-1 = oldest).
        self._ghost_insert_seq = 0

    def _refresh_capacities(self) -> None:
        new_cap = max(1, int(self._total_capacity * self._small_ratio))
        self.small_capacity = new_cap
        self.main_capacity = self._total_capacity - new_cap
        # Tie the ghost FIFO length to the main size (= 1 − small_ratio
        # of cache). When small grows, ghost shrinks; when small shrinks,
        # ghost grows. The next _add_to_ghost call enforces the cap by
        # popping from the FIFO tail, so existing entries beyond the new
        # cap are naturally dropped. Overrides the parent's fixed
        # GHOST_SIZE_RATIO=0.90.
        self.ghost_capacity = max(1, self.main_capacity)

    def _add_to_ghost(self, block_hash: BlockHashWithGroupId) -> None:
        # Store insertion seq so we can determine ghost-position on readmit.
        if block_hash in self.ghost:
            return
        while len(self.ghost) >= self.ghost_capacity:
            dropped_hash, _ = self.ghost.popitem(last=False)
            self._ghost_drop_cleanup(dropped_hash)
        self.ghost[block_hash] = self._ghost_insert_seq
        self._ghost_insert_seq += 1

    def _admit_to_queue(self, block_hash: BlockHashWithGroupId) -> None:
        # +score per ghost-readmit, weighted by a sigmoid of
        # ghost-position (positive only, in [0, 1]). Newest end of FIFO
        # earns weight ≈ 1; oldest end earns ≈ 0.
        if (block_hash not in self.queue_membership
                and block_hash not in self._was_main
                and block_hash in self.ghost):
            block_seq = self.ghost[block_hash]
            if not isinstance(block_seq, bool):
                rank_from_newest = (self._ghost_insert_seq - 1) - block_seq
                ghost_size = max(1, len(self.ghost))
                x = rank_from_newest / ghost_size  # in [0, 1]
                weight = 1.0 / (1.0 + math.exp(
                    self.SIG_STEEPNESS * (x - 0.5)))
                if weight > 0.0:
                    self._on_event(weight)
        super()._admit_to_queue(block_hash)

    def _get_free_blocks_from_radix_tree_nodes(
        self, node: RadixTreeNode, max_blocks: int,
    ) -> List[KVCacheBlock]:
        # main-eviction with freq <= MOVE_TO_MAIN_THRESHOLD → -1.
        main_evict_freqs: list[int] = []
        found = 0
        for i, child_hash in enumerate(node.block_hashes):
            if found >= max_blocks:
                break
            block_to_evict = self.hashed_free_block_map.get(child_hash)
            if (block_to_evict and block_to_evict.ref_cnt == 0
                    and not node.in_use[i]):
                found += 1
                if self.queue_membership.get(child_hash) == 'main':
                    main_evict_freqs.append(self.freq.get(child_hash, 0))

        evicted_blocks = super()._get_free_blocks_from_radix_tree_nodes(
            node, max_blocks)

        for f in main_evict_freqs:
            if f <= self.MOVE_TO_MAIN_THRESHOLD:
                self._on_event(-1.0)

        return evicted_blocks

    def _on_event(self, delta: float) -> None:
        self._score += delta
        self._event_count += 1
        if self._event_count >= self.UPDATE_INTERVAL:
            self._apply_score()

    def _apply_score(self) -> None:
        # Normalize score to [-1, 1] by dividing by event count, then
        # apply STEP. So ratio changes by at most ±STEP per interval.
        if self._event_count > 0:
            normalized = self._score / self._event_count
            new_ratio = self._small_ratio + self.STEP * normalized
            new_ratio = max(self.MIN_RATIO, min(self.MAX_RATIO, new_ratio))
            if new_ratio != self._small_ratio:
                self._small_ratio = new_ratio
                self._refresh_capacities()
        self._ratio_trace.append((self.current_time, self._small_ratio))
        self._score *= self.DECAY
        self._event_count = 0


class BeladyBlockComputeFreeBlockManager(BeladyComputeFreeBlockManager):
    """Belady Compute Free Block Manager that samples blocks directly."""
    
    def _sample_and_evict_blocks_directly(self, n: int) -> List[KVCacheBlock]:
        import random
        evicted_blocks = []
        ASSOCIATIVITY = 128

        while n > 0:
            valid_hashes = []

            for h in self.hashed_free_block_map:
                node, idx = self.radix_tree._node_map.get(h, (None, -1))
                if node is not None and not node.in_use[idx]:
                    valid_hashes.append(h)

            if not valid_hashes:
                break

            num_to_sample = min(ASSOCIATIVITY, len(valid_hashes))
            sampled_hashes = random.sample(valid_hashes, num_to_sample)

            victim_hash = None
            victim_score = float('-inf')

            for h in sampled_hashes:
                score = float('-inf')
                if h in self.tags:
                    tag = self.tags[h]
                    score = self._get_eviction_score(tag)

                if score > victim_score:  # For Belady, higher score = evict first
                    victim_score = score
                    victim_hash = h

            if victim_hash is None:
                victim_hash = sampled_hashes[0]

            node, idx = self.radix_tree._node_map[victim_hash]

            block_to_evict = self.hashed_free_block_map.pop(victim_hash)
            self.free_blocks_queue_in_radix_tree.remove(block_to_evict)

            if victim_hash in self.tags:
                del self.tags[victim_hash]
            if victim_hash in self.next_access_times:
                del self.next_access_times[victim_hash]

            if not node.children:
                all_evicted = True
                for child_hash in node.block_hashes:
                    if child_hash in self.hashed_free_block_map:
                        all_evicted = False
                        break
                if all_evicted:
                    self.radix_tree.evict(node)

            evicted_blocks.append(block_to_evict)
            n -= 1
            self._num_free_blocks -= 1

        return evicted_blocks

    def _try_get_free_blocks_from_free_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        return self._sample_and_evict_blocks_directly(n)

    def _try_get_free_blocks_from_in_use_radix_tree_nodes(
        self, n: int,
    ) -> List[KVCacheBlock]:
        return []


class BeladyNodeComputeFreeBlockManager(BeladyComputeFreeBlockManager):
    """Belady Compute Free Block Manager that evicts full nodes.
    
    every time they sample some node, the score should be given as 
    (next access) * (sum of compute intensity) / (number of blocks). 
    If the number of blocks in that node is larger than the number of needed blocks, 
    you can prune the node and put the extra blocks in blocks_not_in_tree.
    """

    def _get_node_eviction_score(self, node: RadixTreeNode) -> float:
        min_next_time = float('inf')
        sum_compute_intensity = 0.0
        num_blocks = 0
        
        for child_hash in node.block_hashes:
            if child_hash in self.tags:
                tag = self.tags[child_hash]
                next_time = self._get_next_access_time(tag)
                if next_time < min_next_time:
                    min_next_time = next_time
                    
                sum_compute_intensity += get_compute_intensity(tag.index)
                num_blocks += 1
                
        if num_blocks == 0:
            return float('-inf')
            
        time_until_next_access = max(0, min_next_time - self.current_time)
        return float(time_until_next_access) * sum_compute_intensity / num_blocks

    def _sample_and_evict(self, n: int, sample_fn) -> List[KVCacheBlock]:
        """Core eviction mechanism using candidate node sampling."""
        evicted_blocks = []
        ASSOCIATIVITY = 32
        tried_nodes = set()
        score_cache = {}

        while n > 0:
            sampled_nodes = sample_fn(ASSOCIATIVITY)
            valid_nodes = [node for node in sampled_nodes if id(node) not in tried_nodes]
            if not valid_nodes:
                break

            victim_node = None
            victim_score = float('-inf')

            for node in valid_nodes:
                if id(node) in score_cache:
                    score = score_cache[id(node)]
                else:
                    score = self._get_node_eviction_score(node)
                    score_cache[id(node)] = score

                if score > victim_score:  # Higher score = evict first
                    victim_score = score
                    victim_node = node

            if victim_node is None:
                victim_node = valid_nodes[0]

            # Find all evictable blocks in victim_node
            evictable_hashes = []
            for i, child_hash in enumerate(victim_node.block_hashes):
                block_to_evict = self.hashed_free_block_map.get(child_hash)
                if block_to_evict and block_to_evict.ref_cnt == 0 and not victim_node.in_use[i]:
                    evictable_hashes.append(child_hash)
            
            if not evictable_hashes:
                tried_nodes.add(id(victim_node))
                continue
                
            # Process the blocks
            blocks_for_request = []
            
            for child_hash in evictable_hashes:
                block_to_evict = self.hashed_free_block_map.pop(child_hash)
                self.free_blocks_queue_in_radix_tree.remove(block_to_evict)
                
                self._num_free_blocks -= 1
                
                if len(blocks_for_request) < n:
                    blocks_for_request.append(block_to_evict)
                else:
                    self.blocks_not_in_tree[child_hash] = block_to_evict
                    self._num_free_blocks += 1
                    
                if child_hash in self.tags:
                    del self.tags[child_hash]
                if child_hash in self.next_access_times:
                    del self.next_access_times[child_hash]

            if not victim_node.children:
                all_evicted = True
                for child_hash in victim_node.block_hashes:
                    if child_hash in self.hashed_free_block_map:
                        all_evicted = False
                        break
                if all_evicted:
                    self.radix_tree.evict(victim_node)

            evicted_blocks.extend(blocks_for_request)
            n -= len(blocks_for_request)
            score_cache.pop(id(victim_node), None)
            tried_nodes.add(id(victim_node))

        return evicted_blocks


# =====================================================================
# Formula-based managers
# =====================================================================
# Score-driven eviction policies that explore richer combinations of
# frequency, recency, inter-arrival time (IAT) and last-access gap (LAG).
#
# Definitions (in "block-access" time units):
#   recency = current_time - last_access_time            (>=1)
#   freq    = access_count
#   IAT     = (last_access_time - first_access_time) /
#             (access_count - 1)                          if access_count >= 2
#   LAG     = last_access_time - prev_access_time         if access_count >= 2
#
# For one-hit blocks (access_count == 1), IAT/LAG are unobserved.
# We default them to per-trace P95 values (set externally via
# ``FormulaFreeBlockManager.IAT_DEFAULT`` / ``LAG_DEFAULT``).
# Formula 5 special-cases IAT to 0 for one-hit blocks (per spec).
#
# All managers inherit from RandomQuickDemotionGhostFreeBlockManager so
# they share the leaf-biased sampling and ghost queue (apples-to-apples
# vs the QuickDemotionGhost baseline). The one-hit multiplier penalty is
# disabled (the formula itself encodes admission).


class FormulaFreeBlockManager(RandomQuickDemotionGhostFreeBlockManager):
    """Base class for score-formula eviction managers.

    Subclasses set ``FORMULA_ID`` (1..6). One-hit blocks fall back to
    ``IAT_DEFAULT`` / ``LAG_DEFAULT`` (P95 per trace) so admission is
    encoded in the score itself.
    """

    # Disable RandomQuickDemotion's hard-coded one-hit penalty: we let the
    # formula's response to (IAT_DEFAULT, LAG_DEFAULT) drive admission.
    ONE_HIT_LEAF_PENALTY = 1.0

    # Defaults are P95 averages across the four profiled traces (see
    # evaluate/vllm/iat_lag_profile.json). The simulator overrides these
    # per-trace via class attribute injection before the run.
    IAT_DEFAULT: float = 2_500_000.0
    LAG_DEFAULT: float = 3_000_000.0
    # P99 of LAG (used by formula 9 for one-hit blocks). Overridden per
    # trace by the simulator.
    LAG_P99_DEFAULT: float = 8_000_000.0

    FORMULA_ID: int = 0  # set by subclasses
    LAG_EXP: float = 2.0  # used by formula 6

    def _formula_inputs(self, tag: "RandomFreeBlockManager.Tag"):
        recency = max(1.0, float(self.current_time - tag.last_access_time))
        freq = float(tag.access_count)
        if tag.access_count >= 2 and tag.first_access_time >= 0:
            iat = max(
                1.0,
                (tag.last_access_time - tag.first_access_time) /
                max(1, tag.access_count - 1),
            )
        else:
            iat = float(self.IAT_DEFAULT)
        if tag.access_count >= 2 and tag.prev_access_time >= 0:
            lag = max(1.0,
                      float(tag.last_access_time - tag.prev_access_time))
        else:
            lag = float(self.LAG_DEFAULT)
        return recency, freq, iat, lag

    def _get_eviction_score(self,
                            tag: "RandomFreeBlockManager.Tag") -> float:
        recency, freq, iat, lag = self._formula_inputs(tag)
        fid = self.FORMULA_ID
        if fid == 1:
            score = freq / (recency * iat)
        elif fid == 2:
            score = (freq * freq) / (recency * iat)
        elif fid == 3:
            score = freq / (recency * lag)
        elif fid == 4:
            score = (freq / recency) * (iat / lag)
        elif fid == 5:
            iat5 = 0.0 if tag.access_count <= 1 else iat
            denom = recency + max(0.0, recency - iat5)
            score = freq / max(1.0, denom)
        elif fid == 6:
            score = (freq * iat) / (recency * (lag**self.LAG_EXP))
        elif fid == 7:
            # (freq/(freq+1)) * exp(-recency/IAT)
            score = (freq / (freq + 1.0)) * math.exp(-recency / iat)
        elif fid == 8:
            iat8 = 1.0 if tag.access_count <= 1 else iat
            denom = recency * max(1.0, recency / iat8)
            score = freq / max(1.0, denom)
        elif fid == 9:
            iat9 = 1.0 if tag.access_count <= 1 else iat
            lag9 = float(self.LAG_P99_DEFAULT) \
                if tag.access_count <= 1 or tag.prev_access_time < 0 else lag
            score = (freq / recency) * (iat9 / lag9)
        else:
            raise ValueError(f"Unknown FORMULA_ID {fid}")
        return score * get_compute_intensity(tag.index)

    def _get_leaf_eviction_score(self,
                                 tag: "RandomFreeBlockManager.Tag",
                                 is_leaf: bool) -> float:
        # No multiplier penalty: the formula's behavior on one-hit blocks
        # (via IAT_DEFAULT / LAG_DEFAULT) handles admission.
        return self._get_eviction_score(tag)

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Capture the previous last_access_time *before* super() updates
        # tags (and before ghost recall pulls a stale tag in). Need to
        # check both self.tags and self.ghost since the ghost layer
        # recalls inside its own record_request_blocks.
        captured_prev: dict = {}
        for b in blocks:
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue
            bh = b.block_hash
            if bh in self.tags:
                captured_prev[bh] = self.tags[bh].last_access_time
            elif bh in self.ghost:
                captured_prev[bh] = self.ghost[bh].last_access_time

        super().record_request_blocks(blocks)

        # Now self.tags has fresh last_access_time. Write prev/first.
        for b in blocks:
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue
            bh = b.block_hash
            tag = self.tags.get(bh)
            if tag is None:
                continue
            if bh in captured_prev:
                tag.prev_access_time = captured_prev[bh]
            # first_access_time: set the first time we see the block. -1
            # marks "unset" so re-creation after eviction (without ghost)
            # restarts the clock.
            if tag.first_access_time < 0:
                tag.first_access_time = tag.last_access_time


class Formula1FreeBlockManager(FormulaFreeBlockManager):
    """Score = freq / (recency * IAT) * compute_intensity."""
    FORMULA_ID = 1


class Formula2FreeBlockManager(FormulaFreeBlockManager):
    """Score = freq^2 / (recency * IAT) * compute_intensity."""
    FORMULA_ID = 2


class Formula3FreeBlockManager(FormulaFreeBlockManager):
    """Score = freq / (recency * LAG) * compute_intensity."""
    FORMULA_ID = 3


class Formula4FreeBlockManager(FormulaFreeBlockManager):
    """Score = (freq / recency) * (IAT / LAG) * compute_intensity."""
    FORMULA_ID = 4


class Formula5FreeBlockManager(FormulaFreeBlockManager):
    """Score = freq / (recency + max(0, recency - IAT)) * compute_intensity.

    For one-hit blocks IAT=0, so denominator = 2*recency.
    """
    FORMULA_ID = 5


class Formula6FreeBlockManager(FormulaFreeBlockManager):
    """Score = (freq * IAT) / (recency * LAG^2) * compute_intensity."""
    FORMULA_ID = 6
    LAG_EXP = 2.0


class Formula7FreeBlockManager(FormulaFreeBlockManager):
    """Score = (freq/(freq+1)) * exp(-recency/IAT) * compute_intensity."""
    FORMULA_ID = 7


class Formula8FreeBlockManager(FormulaFreeBlockManager):
    """Score = freq / (recency * max(1, recency/IAT)) * compute_intensity.

    For one-hit blocks IAT=1 (per spec).
    """
    FORMULA_ID = 8


class Formula9FreeBlockManager(FormulaFreeBlockManager):
    """Score = (freq/recency) * (IAT/LAG) * compute_intensity.

    For one-hit blocks IAT=1 and LAG=P99 (per spec). Distinct from
    Formula 4, which uses P95 defaults for both.
    """
    FORMULA_ID = 9


# =====================================================================
# Power-sweep managers
# =====================================================================
# Take the RandomGhost baseline (ghost queue, no leaf bias, no demotion
# penalty) and vary the exponent on freq, recency, or compute_intensity
# one at a time. Lets us isolate which term should be amplified or
# weakened to improve the score function.
#
# Base:   score = (freq / recency) * compute_intensity
# Variants override one exponent at a time (others stay at 1.0).


class PowerGhostFreeBlockManager(RandomGhostFreeBlockManager):
    """Parameterized RandomGhost: score = (freq^F / recency^R) * CI^C."""

    FREQ_EXP: float = 1.0
    RECENCY_EXP: float = 1.0
    CI_EXP: float = 1.0

    def _get_eviction_score(self,
                            tag: "RandomFreeBlockManager.Tag") -> float:
        recency = max(1.0, float(self.current_time - tag.last_access_time))
        freq = float(tag.access_count)
        ci = float(get_compute_intensity(tag.index))
        score = (freq**self.FREQ_EXP) / (recency**self.RECENCY_EXP)
        return score * (ci**self.CI_EXP)


class PowFreq05FreeBlockManager(PowerGhostFreeBlockManager):
    """freq^0.5 / recency * CI."""
    FREQ_EXP = 0.5


class PowFreq2FreeBlockManager(PowerGhostFreeBlockManager):
    """freq^2 / recency * CI."""
    FREQ_EXP = 2.0


class PowRec05FreeBlockManager(PowerGhostFreeBlockManager):
    """freq / recency^0.5 * CI."""
    RECENCY_EXP = 0.5


class PowRec2FreeBlockManager(PowerGhostFreeBlockManager):
    """freq / recency^2 * CI."""
    RECENCY_EXP = 2.0


class PowCi05FreeBlockManager(PowerGhostFreeBlockManager):
    """freq / recency * CI^0.5."""
    CI_EXP = 0.5


class PowCi2FreeBlockManager(PowerGhostFreeBlockManager):
    """freq / recency * CI^2."""
    CI_EXP = 2.0


class PowRec075FreeBlockManager(PowerGhostFreeBlockManager):
    """freq / recency^0.75 * CI. Fills the gap between RandomGhost (a=1) and
    PowRec05 (a=0.5). Useful as a base for the Adapt2 prefix-boost variants
    since RandomAdaptive2 v3+ centers a in [0.5, 1.0] for short workloads.
    """
    RECENCY_EXP = 0.75


# =====================================================================
# Adapt2: block-level translation of RandomAdaptive2 v11-v14 (non-adaptive)
# =====================================================================
# RandomAdaptive2's final design (see docs/algorithms/RandomAdaptive2.md)
# combines:
#   1. score = freq * cost / age^a / size, with adaptive a in [0.25, 1.5].
#      In our block-level world `size = 1` so the size term drops; `cost`
#      becomes per-block compute_intensity. The remaining choice is `a`.
#   2. A "recurring-prefix" boost. They track a saturating count of how
#      often each leading-block hash has been seen. Nodes whose leading
#      block hash recurs (count >= 3) get score *= (1 + log2(count)).
#   3. A cold-workload admission cap (skipped here — block-level admission
#      requires changing evaluate.py's allocation path, separate work).
#
# The non-adaptive translation here:
#   - `a` (RECENCY_EXP) is fixed per subclass instead of derived from
#     mean_len; we sweep {0.5, 0.75, 1.0}.
#   - The prefix-history map is maintained as a saturating counter on
#     leading-block hashes. The score boost is `1 + log2(count)` whenever
#     `count >= PREFIX_BOOST_THRESHOLD` (3, matching the doc).
#   - Per-block leader-count is the maximum leading-block count across
#     requests that touched the block — so the boost propagates to deep
#     children of a recurring root, not just the root itself.
#   - We optionally blend with what was useful in our own sweep: freq^2
#     (FREQ_EXP=2.0). FREQ_EXP=1.0 keeps the original v11-v14 form.


class Adapt2BaseFreeBlockManager(RandomGhostFreeBlockManager):
    """Non-adaptive RandomAdaptive2 v11-v14 base.

    Subclasses set FREQ_EXP, RECENCY_EXP, USE_PREFIX_BOOST.
    """

    FREQ_EXP: float = 1.0
    RECENCY_EXP: float = 1.0
    CI_EXP: float = 1.0
    USE_PREFIX_BOOST: bool = True
    PREFIX_BOOST_THRESHOLD: int = 3
    PREFIX_HISTORY_SATURATION: int = 32

    def __init__(self, blocks: List[KVCacheBlock]):
        super().__init__(blocks)
        # Count of how many times each leading-block hash has been seen.
        self.prefix_history: dict = {}
        # Max leader-count seen so far for each block hash. Survives
        # eviction so re-admitted blocks keep their boost.
        self.block_leader_count: dict = {}

    def record_request_blocks(self, blocks: List[KVCacheBlock]) -> None:
        # Pick the first non-null block as the request leader.
        leader_hash = None
        for b in blocks:
            if b.block_hash is None or getattr(b, 'is_null', False):
                continue
            leader_hash = b.block_hash
            break

        leader_count = 0
        if leader_hash is not None:
            new_count = min(
                self.PREFIX_HISTORY_SATURATION,
                self.prefix_history.get(leader_hash, 0) + 1)
            self.prefix_history[leader_hash] = new_count
            leader_count = new_count

        # Propagate leader count to every block in this request.
        if leader_count > 0:
            for b in blocks:
                if b.block_hash is None or getattr(b, 'is_null', False):
                    continue
                bh = b.block_hash
                if leader_count > self.block_leader_count.get(bh, 0):
                    self.block_leader_count[bh] = leader_count

        super().record_request_blocks(blocks)

    def _get_eviction_score(self,
                            tag: "RandomFreeBlockManager.Tag") -> float:
        recency = max(1.0, float(self.current_time - tag.last_access_time))
        freq = float(tag.access_count)
        ci = float(get_compute_intensity(tag.index))

        score = (freq**self.FREQ_EXP) / (recency**self.RECENCY_EXP)
        score *= ci**self.CI_EXP

        if self.USE_PREFIX_BOOST:
            count = self.block_leader_count.get(tag.block_hash, 0)
            if count >= self.PREFIX_BOOST_THRESHOLD:
                score *= 1.0 + math.log2(count)

        return score


# Boost-only ablations (a swept, freq exponent = 1).
class Adapt2_a10_BFreeBlockManager(Adapt2BaseFreeBlockManager):
    """RandomGhost + recurring-prefix boost (a=1.0)."""
    RECENCY_EXP = 1.0


class Adapt2_a075_BFreeBlockManager(Adapt2BaseFreeBlockManager):
    """a=0.75 + recurring-prefix boost."""
    RECENCY_EXP = 0.75


class Adapt2_a05_BFreeBlockManager(Adapt2BaseFreeBlockManager):
    """a=0.5 + recurring-prefix boost."""
    RECENCY_EXP = 0.5


# freq^2 blends (best combination from our power sweep + Adapt2 boost).
class Adapt2_F2_a10_BFreeBlockManager(Adapt2BaseFreeBlockManager):
    """freq^2, a=1.0, prefix boost."""
    FREQ_EXP = 2.0
    RECENCY_EXP = 1.0


class Adapt2_F2_a075_BFreeBlockManager(Adapt2BaseFreeBlockManager):
    """freq^2, a=0.75, prefix boost."""
    FREQ_EXP = 2.0
    RECENCY_EXP = 0.75


class Adapt2_F2_a05_BFreeBlockManager(Adapt2BaseFreeBlockManager):
    """freq^2, a=0.5, prefix boost."""
    FREQ_EXP = 2.0
    RECENCY_EXP = 0.5


# Boost-isolation control: a=1.0 with NO boost — should match RandomGhost
# exactly (modulo RNG). Useful sanity check that the boost is the only
# effect at this point.
class Adapt2_a10_NoBoostFreeBlockManager(Adapt2BaseFreeBlockManager):
    """a=1.0, no prefix boost (control). Should match RandomGhost."""
    RECENCY_EXP = 1.0
    USE_PREFIX_BOOST = False


class TreeAwareGhostBaseFreeBlockManager(RandomGhostFreeBlockManager):
    """RandomGhost + tree-aware score blend over radix-tree ancestors.

    Hypothesis: in prefix-cache workloads, blocks under the same radix-tree
    ancestor share a reuse pattern. An ancestor's frequency-rate is a prior
    on the candidate's future hotness, with closer ancestors more predictive.

    Score:
        r_X    = X.access_count / max(1, current_time - X.last_access_time)
        r_A_d  = same rate computed for ancestor block at block-distance d
        log r_eff = (1 - LAMBDA) * log r_X
                   + LAMBDA * Σ w_d * log r_A_d / Σ w_d
        w_d    = ALPHA ** d                       (closer ancestor → higher)
        score  = r_eff * compute_intensity(X.index)

    Walking ancestors traverses block-by-block: first the prior positions
    inside X's own radix-tree node, then up to parent nodes, capped at
    MAX_ANCESTOR_DEPTH. Ancestors without an entry in self.tags (e.g.
    evicted, no longer warm) are skipped — they neither contribute nor
    dilute the weighted sum.

    LAMBDA = 0 collapses to plain RandomGhost (control).
    """

    ALPHA: float = 0.5
    LAMBDA: float = 0.3
    MAX_ANCESTOR_DEPTH: int = 32

    def _walk_ancestor_tags(self, block_hash: BlockHashWithGroupId):
        """Yield (distance, tag) for ancestor blocks in radix-tree order.

        Distance is in block-positions: 1 = immediately preceding block in
        the request prefix. Stops at the root or after MAX_ANCESTOR_DEPTH
        steps.
        """
        node_idx = self.radix_tree._node_map.get(block_hash)
        if node_idx is None:
            return
        node, idx = node_idx
        root = self.radix_tree._root
        d = 0
        while d < self.MAX_ANCESTOR_DEPTH:
            if idx > 0:
                idx -= 1
            else:
                parent = node.parent
                if parent is None or parent is root:
                    return
                node = parent
                idx = len(node.block_hashes) - 1
                if idx < 0:
                    return
            d += 1
            ancestor_hash = node.block_hashes[idx]
            atag = self.tags.get(ancestor_hash)
            if atag is not None:
                yield d, atag

    def _get_eviction_score(
            self, tag: "RandomFreeBlockManager.Tag") -> float:
        own_recency = max(1, self.current_time - tag.last_access_time)
        own_rate = tag.access_count / own_recency

        if self.LAMBDA <= 0.0:
            return own_rate * get_compute_intensity(tag.index)

        log_anc_sum = 0.0
        weight_sum = 0.0
        for d, atag in self._walk_ancestor_tags(tag.block_hash):
            arec = max(1, self.current_time - atag.last_access_time)
            arate = atag.access_count / arec
            w = self.ALPHA ** d
            log_anc_sum += w * math.log(arate)
            weight_sum += w

        if weight_sum > 0.0:
            avg_log_anc = log_anc_sum / weight_sum
            log_eff = ((1.0 - self.LAMBDA) * math.log(own_rate)
                       + self.LAMBDA * avg_log_anc)
            rate = math.exp(log_eff)
        else:
            rate = own_rate

        return rate * get_compute_intensity(tag.index)


class TreeAwareGhostFreeBlockManager(TreeAwareGhostBaseFreeBlockManager):
    """Default tuning: moderate ancestor influence, fast decay."""
    ALPHA = 0.5
    LAMBDA = 0.3


# Decay sweeps (ALPHA): how fast ancestor influence falls off with distance.
class TreeAwareGhost_a03_l03FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.3
    LAMBDA = 0.3


class TreeAwareGhost_a07_l03FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.7
    LAMBDA = 0.3


class TreeAwareGhost_a09_l03FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.9
    LAMBDA = 0.3


# Blend sweeps (LAMBDA): how strongly ancestor signal pulls the score.
class TreeAwareGhost_a05_l01FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.5
    LAMBDA = 0.1


class TreeAwareGhost_a05_l05FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.5
    LAMBDA = 0.5


class TreeAwareGhost_a05_l07FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.5
    LAMBDA = 0.7


class TreeAwareGhost_a07_l05FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.7
    LAMBDA = 0.5


class TreeAwareGhost_a09_l05FreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    ALPHA = 0.9
    LAMBDA = 0.5


class TreeAwareGhostGapBaseFreeBlockManager(
        TreeAwareGhostBaseFreeBlockManager):
    """Variant that uses the ancestor *gap* as a demotion signal.

    Key asymmetry the parent class throws away: r_A ≥ r_X always (every
    access to X also updates every ancestor). So r_X / r_A is in (0, 1]
    and measures how much X "keeps up" with its subtree:

      r_X / r_A_wgt ≈ 1   → X is the dominant continuation; neutral.
      r_X / r_A_wgt ≪ 1   → X is a cold branch of a hot tree; demote.

    Score:
        score = r_X · compute_intensity(X.index) · (r_X / r_A_wgt)^GAP_LAMBDA

    GAP_LAMBDA = 0 → plain RandomGhost. Larger GAP_LAMBDA pushes lagging
    children out faster.
    """

    GAP_LAMBDA: float = 0.5

    def _get_eviction_score(
            self, tag: "RandomFreeBlockManager.Tag") -> float:
        own_recency = max(1, self.current_time - tag.last_access_time)
        own_rate = tag.access_count / own_recency

        if self.GAP_LAMBDA <= 0.0:
            return own_rate * get_compute_intensity(tag.index)

        log_anc_sum = 0.0
        weight_sum = 0.0
        for d, atag in self._walk_ancestor_tags(tag.block_hash):
            arec = max(1, self.current_time - atag.last_access_time)
            arate = atag.access_count / arec
            w = self.ALPHA ** d
            log_anc_sum += w * math.log(arate)
            weight_sum += w

        if weight_sum > 0.0:
            log_avg_anc = log_anc_sum / weight_sum
            log_gap = math.log(own_rate) - log_avg_anc  # ≤ 0 typically
            log_score = math.log(own_rate) + self.GAP_LAMBDA * log_gap
            rate = math.exp(log_score)
        else:
            rate = own_rate

        return rate * get_compute_intensity(tag.index)


class TreeAwareGhostGapFreeBlockManager(TreeAwareGhostGapBaseFreeBlockManager):
    """Default gap tuning: α=0.5, GAP_LAMBDA=0.5."""
    ALPHA = 0.5
    GAP_LAMBDA = 0.5


class TreeAwareGhostGap_a05_g03FreeBlockManager(
        TreeAwareGhostGapBaseFreeBlockManager):
    ALPHA = 0.5
    GAP_LAMBDA = 0.3


class TreeAwareGhostGap_a05_g10FreeBlockManager(
        TreeAwareGhostGapBaseFreeBlockManager):
    ALPHA = 0.5
    GAP_LAMBDA = 1.0


class TreeAwareGhostGap_a07_g05FreeBlockManager(
        TreeAwareGhostGapBaseFreeBlockManager):
    ALPHA = 0.7
    GAP_LAMBDA = 0.5


# QuickDemotion + tree-aware score: combine leaf-bias one-hit demotion with
# the ancestor-blended rate.
class TreeAwareQuickDemotionGhostFreeBlockManager(
        RandomQuickDemotionGhostFreeBlockManager):
    """RandomQuickDemotionGhost + tree-aware ancestor blend on the base score.

    Reuses the geometric blend from TreeAwareGhostBase but plugs into the
    QuickDemotion leaf-bias machinery: one-hit leaves still get the
    multiplier penalty on top of the blended rate.
    """

    ALPHA: float = 0.5
    LAMBDA: float = 0.3
    MAX_ANCESTOR_DEPTH: int = 32

    # Borrow the ancestor walk + blend from TreeAwareGhostBase.
    _walk_ancestor_tags = TreeAwareGhostBaseFreeBlockManager._walk_ancestor_tags
    _get_eviction_score = TreeAwareGhostBaseFreeBlockManager._get_eviction_score
