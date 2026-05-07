"""Phase 4c filter logic: smoke test the worker's block_table filter math.

We replicate the in-place compaction pattern from gpu_model_runner.py
(without booting a worker) and verify:

* unmatched IDs → no-op
* one matched block → row compacts left, num_blocks_per_row decremented
* multi-block match → all matches compacted out, order preserved
* match at front, middle, end → all positions handled
* idempotency: running twice does nothing the second time
* multi-row isolation → only target row mutates

Run:
    cd /home/vlad/adaptivecache-paper2
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.tests.test_v4_filter
"""
from __future__ import annotations

import numpy as np


def _filter_inplace(row_np: np.ndarray, num_blocks_per_row: np.ndarray,
                    req_index: int, to_remove_list: list[int]) -> None:
    """Mirror of the worker filter. Mutates row_np + num_blocks_per_row."""
    if not to_remove_list:
        return
    n = int(num_blocks_per_row[req_index])
    if n == 0:
        return
    to_remove = np.asarray(to_remove_list, dtype=np.int32)
    live = row_np[req_index, :n]
    keep_mask = ~np.isin(live, to_remove)
    new_n = int(keep_mask.sum())
    if new_n == n:
        return
    row_np[req_index, :new_n] = live[keep_mask]
    num_blocks_per_row[req_index] = new_n


def _make_row(values: list[int], capacity: int = 16) -> tuple[np.ndarray, np.ndarray]:
    row_np = np.zeros((4, capacity), dtype=np.int32)
    nbpr = np.zeros(4, dtype=np.int32)
    row_np[1, :len(values)] = values
    nbpr[1] = len(values)
    return row_np, nbpr


def test_unmatched_is_noop():
    row, nbpr = _make_row([10, 11, 12, 13, 14])
    snap = row.copy()
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[99, 100])
    assert nbpr[1] == 5
    assert (row == snap).all()
    print("  unmatched IDs → no-op ✓")


def test_single_block_middle():
    row, nbpr = _make_row([10, 11, 12, 13, 14])
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[12])
    assert nbpr[1] == 4
    assert list(row[1, :4]) == [10, 11, 13, 14]
    print("  single block in middle → compacts left ✓")


def test_single_block_front():
    row, nbpr = _make_row([10, 11, 12, 13, 14])
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[10])
    assert nbpr[1] == 4
    assert list(row[1, :4]) == [11, 12, 13, 14]
    print("  single block at front → compacts left ✓")


def test_single_block_end():
    row, nbpr = _make_row([10, 11, 12, 13, 14])
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[14])
    assert nbpr[1] == 4
    assert list(row[1, :4]) == [10, 11, 12, 13]
    print("  single block at end → drops it ✓")


def test_multiple_blocks_preserve_order():
    row, nbpr = _make_row([10, 11, 12, 13, 14, 15, 16])
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[11, 14, 16])
    assert nbpr[1] == 4
    assert list(row[1, :4]) == [10, 12, 13, 15]
    print("  multi-block filter preserves order ✓")


def test_idempotent():
    row, nbpr = _make_row([10, 11, 12, 13, 14])
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[12, 14])
    snap = row.copy()
    snap_n = nbpr[1]
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[12, 14])
    assert nbpr[1] == snap_n
    assert (row == snap).all()
    print("  idempotent: re-applying same filter is no-op ✓")


def test_other_rows_untouched():
    row = np.zeros((4, 16), dtype=np.int32)
    nbpr = np.zeros(4, dtype=np.int32)
    row[0, :3] = [10, 11, 12]; nbpr[0] = 3
    row[1, :5] = [10, 11, 12, 13, 14]; nbpr[1] = 5
    row[2, :4] = [20, 21, 22, 23]; nbpr[2] = 4
    snap0 = row[0].copy(); snap2 = row[2].copy()
    n0 = nbpr[0]; n2 = nbpr[2]
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[11, 13])
    assert nbpr[1] == 3
    assert list(row[1, :3]) == [10, 12, 14]
    assert (row[0] == snap0).all() and nbpr[0] == n0
    assert (row[2] == snap2).all() and nbpr[2] == n2
    print("  multi-row: only target row mutates ✓")


def test_filter_all_blocks():
    row, nbpr = _make_row([10, 11, 12])
    _filter_inplace(row, nbpr, req_index=1, to_remove_list=[10, 11, 12])
    assert nbpr[1] == 0
    print("  filter-everything → num_blocks_per_row = 0 ✓")


def main():
    test_unmatched_is_noop()
    test_single_block_middle()
    test_single_block_front()
    test_single_block_end()
    test_multiple_blocks_preserve_order()
    test_idempotent()
    test_other_rows_untouched()
    test_filter_all_blocks()
    print("ALL PHASE 4C FILTER TESTS PASSED")


if __name__ == "__main__":
    main()
