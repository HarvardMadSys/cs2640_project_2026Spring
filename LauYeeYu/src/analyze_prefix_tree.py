#!/usr/bin/env python3
"""
Build and visualize a radix (compressed) prefix tree from trace files.

For each trace, the script:
1. Reads all requests' hash_ids sequences
2. Builds a trie, then compresses it into a radix tree
3. Renders the tree as an SVG using Graphviz
4. Prints summary statistics (leaf hit distribution, one-hit wonder analysis)
"""

import json
import argparse
import os
from collections import Counter
from pathlib import Path

import graphviz
import matplotlib.pyplot as plt
import numpy as np


# ---------------------------------------------------------------------------
# Trie / Radix tree data structures
# ---------------------------------------------------------------------------

class TrieNode:
    __slots__ = ("children", "hits", "is_end")

    def __init__(self):
        self.children: dict[int, "TrieNode"] = {}
        self.hits: int = 0      # number of requests passing through
        self.is_end: bool = False  # marks the end of at least one request


class RadixNode:
    """A node in the compressed radix tree."""
    __slots__ = ("keys", "hits", "is_end", "children", "depth")

    def __init__(self, keys: list[int], hits: int, is_end: bool, depth: int):
        self.keys = keys           # the block hash_ids collapsed into this edge
        self.hits = hits
        self.is_end = is_end
        self.children: list["RadixNode"] = []
        self.depth = depth         # depth (in blocks) of the first key in this node


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------

def build_trie(sequences: list[list[int]]) -> TrieNode:
    root = TrieNode()
    for seq in sequences:
        node = root
        for h in seq:
            node.hits += 1
            if h not in node.children:
                node.children[h] = TrieNode()
            node = node.children[h]
        node.hits += 1
        node.is_end = True
    return root


def compress_trie(trie_node: TrieNode, depth: int = 0) -> RadixNode:
    """Compress a trie into a radix tree."""
    radix = RadixNode(keys=[], hits=trie_node.hits, is_end=trie_node.is_end, depth=depth)

    for key, child in trie_node.children.items():
        edge_keys = [key]
        current = child
        current_depth = depth + 1

        while len(current.children) == 1 and not current.is_end:
            only_key = next(iter(current.children))
            edge_keys.append(only_key)
            current = current.children[only_key]
            current_depth += 1

        child_radix = RadixNode(
            keys=edge_keys,
            hits=current.hits,
            is_end=current.is_end,
            depth=depth + 1,
        )

        for ck, cv in current.children.items():
            child_radix.children.append(_compress_child(cv, [ck], current_depth + 1))

        radix.children.append(child_radix)

    return radix


def _compress_child(trie_node: TrieNode, prefix_keys: list[int], depth: int) -> RadixNode:
    edge_keys = list(prefix_keys)
    current = trie_node
    current_depth = depth

    while len(current.children) == 1 and not current.is_end:
        only_key = next(iter(current.children))
        edge_keys.append(only_key)
        current = current.children[only_key]
        current_depth += 1

    node = RadixNode(
        keys=edge_keys,
        hits=current.hits,
        is_end=current.is_end,
        depth=depth,
    )

    for ck, cv in current.children.items():
        node.children.append(_compress_child(cv, [ck], current_depth + 1))

    return node


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def collect_leaf_stats(node: RadixNode) -> list[dict]:
    leaves = []
    _collect_leaves(node, leaves)
    return leaves


def _collect_leaves(node: RadixNode, acc: list[dict]):
    if not node.children:
        acc.append({"hits": node.hits, "blocks": len(node.keys), "depth": node.depth})
    for c in node.children:
        _collect_leaves(c, acc)


def count_nodes(node: RadixNode) -> int:
    return 1 + sum(count_nodes(c) for c in node.children)


def count_blocks(node: RadixNode) -> int:
    """Total number of blocks in this node and its entire subtree."""
    return len(node.keys) + sum(count_blocks(c) for c in node.children)


def collect_subtree_hits(node: RadixNode) -> list[int]:
    """Hits of every node in the subtree rooted at ``node`` (including itself)."""
    acc = [node.hits]
    for c in node.children:
        acc.extend(collect_subtree_hits(c))
    return acc


def _percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def subtree_hit_stats(nodes: list[RadixNode]) -> tuple[float, float]:
    """Return (mean, p90) of hits across all nodes in the subtrees of ``nodes``."""
    all_hits: list[int] = []
    for n in nodes:
        all_hits.extend(collect_subtree_hits(n))
    if not all_hits:
        return 0.0, 0.0
    mean = sum(all_hits) / len(all_hits)
    p90 = _percentile(all_hits, 0.9)
    return mean, p90


def print_summary(trace_name: str, root: RadixNode, sequences: list[list[int]]):
    leaves = collect_leaf_stats(root)
    total_leaves = len(leaves)
    one_hit_leaves = sum(1 for l in leaves if l["hits"] == 1)
    total_nodes = count_nodes(root)

    print(f"\n{'='*60}")
    print(f"Prefix Tree Summary: {trace_name}")
    print(f"{'='*60}")
    print(f"  Total requests:        {len(sequences)}")
    print(f"  Total radix nodes:     {total_nodes}")
    print(f"  Total leaf nodes:      {total_leaves}")
    print(f"  One-hit leaf nodes:    {one_hit_leaves} ({100*one_hit_leaves/max(total_leaves,1):.1f}%)")
    multi_hit = [l for l in leaves if l["hits"] > 1]
    if multi_hit:
        print(f"  Multi-hit leaf nodes:  {len(multi_hit)} ({100*len(multi_hit)/max(total_leaves,1):.1f}%)")
        hit_counts = sorted([l["hits"] for l in multi_hit], reverse=True)
        print(f"    Hit counts (top 10): {hit_counts[:10]}")
    else:
        print(f"  All leaf nodes are one-hit wonders: YES")

    hit_dist = Counter(l["hits"] for l in leaves)
    print(f"  Leaf hit distribution:")
    for hits, cnt in sorted(hit_dist.items()):
        pct = 100 * cnt / total_leaves
        print(f"    {hits} hit{'s' if hits > 1 else ' '}: {cnt} leaves ({pct:.1f}%)")
        if hits > 10:
            remaining = sum(c for h, c in hit_dist.items() if h > 10)
            print(f"    >10 hits: {remaining} leaves")
            break
    print()


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

def render_tree_svg(root: RadixNode, output_path: str, trace_name: str,
                    max_depth: int = 6, min_hit_frac: float = 0.01,
                    max_total_nodes: int = 200):
    """Render the radix tree as SVG using Graphviz.

    To keep the SVG readable:
    - At most ``max_total_nodes`` graph nodes are rendered in total.
    - A child is shown if its hits >= ``min_hit_frac`` * parent hits.
      Children below the threshold are collapsed into a summary node.
    - One-hit leaf siblings are always collapsed into a single summary node.
    - Rendering stops at ``max_depth`` levels of the radix tree.
    """
    dot = graphviz.Digraph(
        format="svg",
        engine="dot",
        graph_attr={
            "label": f"Prefix Radix Tree: {trace_name}",
            "labelloc": "t",
            "fontsize": "18",
            "rankdir": "TB",
            "nodesep": "0.4",
            "ranksep": "0.6",
        },
        node_attr={
            "shape": "box",
            "fontsize": "10",
            "fontname": "monospace",
            "style": "filled,rounded",
        },
    )

    _counter = [0]
    _total_rendered = [0]

    def nid():
        _counter[0] += 1
        return f"n{_counter[0]}"

    def budget_left():
        return _total_rendered[0] < max_total_nodes

    def color_for(hits):
        if hits == 1:
            return "#ffe0e0"
        if hits <= 5:
            return "#fff3cd"
        if hits <= 20:
            return "#d4edda"
        return "#b8daff"

    def summarize_children(children: list[RadixNode]) -> str:
        """Summarize a list of children into a single label."""
        total_hits = sum(c.hits for c in children)
        all_leaves = collect_leaf_stats_from_children(children)
        total_leaves = len(all_leaves)
        one_hit = sum(1 for l in all_leaves if l["hits"] == 1)
        total_nodes = sum(count_nodes(c) for c in children)
        mean_hits, p90_hits = subtree_hit_stats(children)
        lines = [f"hits: {total_hits}"]
        lines.append(f"{total_nodes} nodes total")
        lines.append(f"{total_leaves} leaves")
        lines.append(f"({one_hit} one-hit)")
        lines.append(f"subtree hits mean: {mean_hits:.1f}")
        lines.append(f"subtree hits P90: {p90_hits:.1f}")
        return "\\l".join(lines) + "\\l"

    def collect_leaf_stats_from_children(children: list[RadixNode]) -> list[dict]:
        acc = []
        for c in children:
            _collect_leaves(c, acc)
        return acc

    def add_node(node: RadixNode, parent_id: str | None, depth: int):
        if not budget_left():
            return
        _total_rendered[0] += 1
        node_id = nid()

        # Build label — multi-line (one field per line)
        blocks = len(node.keys)
        is_leaf = not node.children

        total_blks = count_blocks(node)
        mean_hits, p90_hits = subtree_hit_stats([node])

        if not node.keys:  # root
            label = (
                f"ROOT\\lhits: {node.hits}\\ltotal blks: {total_blks}\\l"
                f"subtree hits mean: {mean_hits:.1f}\\l"
                f"subtree hits P90: {p90_hits:.1f}\\l"
            )
        else:
            lines = [f"blks: {blocks}", f"hits: {node.hits}", f"depth: {node.depth}"]
            if not is_leaf:
                lines.append(f"total blks: {total_blks}")
                lines.append(f"subtree hits mean: {mean_hits:.1f}")
                lines.append(f"subtree hits P90: {p90_hits:.1f}")
            if is_leaf:
                lines.append("LEAF")
            label = "\\l".join(lines) + "\\l"

        dot.node(node_id, label=label, fillcolor=color_for(node.hits))
        if parent_id:
            dot.edge(parent_id, node_id)

        if is_leaf or not budget_left():
            return

        # At depth boundary: summarize ALL children in one node
        if depth >= max_depth:
            _total_rendered[0] += 1
            sid = nid()
            dot.node(sid, label=summarize_children(node.children),
                     fillcolor="#f0f0f0", shape="note")
            dot.edge(node_id, sid)
            return

        # Partition children: show those above hit threshold, collapse the rest
        hit_threshold = max(2, node.hits * min_hit_frac)
        significant = [c for c in node.children if c.hits >= hit_threshold]
        minor = [c for c in node.children if c.hits < hit_threshold]

        # Sort significant children by hits descending
        significant.sort(key=lambda c: c.hits, reverse=True)

        rendered = 0
        for child in significant:
            if not budget_left():
                break
            add_node(child, node_id, depth + 1)
            rendered += 1

        # Merge unrendered significant + all minor children into one summary
        to_summarize = significant[rendered:] + minor
        if to_summarize and budget_left():
            _total_rendered[0] += 1
            total_hits = sum(c.hits for c in to_summarize)
            all_leaves = collect_leaf_stats_from_children(to_summarize)
            one_hit_count = sum(1 for l in all_leaves if l["hits"] == 1)
            total_sub_nodes = sum(count_nodes(c) for c in to_summarize)
            mean_hits, p90_hits = subtree_hit_stats(to_summarize)
            lines = [f"hits: {total_hits}"]
            if total_sub_nodes > len(to_summarize):
                lines.append(f"{total_sub_nodes} nodes total")
            lines.append(f"{len(all_leaves)} leaves")
            lines.append(f"({one_hit_count} one-hit)")
            lines.append(f"subtree hits mean: {mean_hits:.1f}")
            lines.append(f"subtree hits P90: {p90_hits:.1f}")
            sid = nid()
            dot.node(sid, label="\\l".join(lines) + "\\l",
                     fillcolor="#f0f0f0", shape="note")
            dot.edge(node_id, sid)

    add_node(root, None, 0)

    out = Path(output_path)
    dot.render(str(out.with_suffix("")), cleanup=True)
    print(f"  SVG written to {output_path} ({_total_rendered[0]} nodes rendered)")


def collect_subtree_stat(node: RadixNode, stat: str) -> list[float]:
    """Per-node summary of subtree-hits across the whole radix tree.

    ``stat`` is "mean" or "p90".
    """
    acc: list[float] = []
    _collect_subtree_stat(node, acc, stat)
    return acc


def _collect_subtree_stat(node: RadixNode, acc: list[float], stat: str):
    hits = collect_subtree_hits(node)
    if stat == "mean":
        acc.append(sum(hits) / len(hits))
    elif stat == "p90":
        acc.append(_percentile(hits, 0.9))
    else:
        raise ValueError(f"unknown stat: {stat}")
    for c in node.children:
        _collect_subtree_stat(c, acc, stat)


def plot_subtree_stat_distribution(root: RadixNode, output_path: str,
                                   trace_name: str, stat: str):
    """Plot histogram + CDF of per-node subtree-hits-{stat} ('mean' or 'p90')."""
    values = collect_subtree_stat(root, stat)
    if not values:
        return
    arr = np.array(values)
    label = f"subtree hits {stat}"

    fig, (ax_hist, ax_cdf) = plt.subplots(1, 2, figsize=(12, 4))

    bins = np.logspace(np.log10(max(arr.min(), 1e-3)), np.log10(arr.max() + 1e-3), 50) \
        if arr.max() > arr.min() else 20
    color_hist = "#4c78a8" if stat == "mean" else "#54a24b"
    color_cdf = "#e45756" if stat == "mean" else "#f58518"
    ax_hist.hist(arr, bins=bins, color=color_hist, edgecolor="black", linewidth=0.3)
    if hasattr(bins, "__len__"):
        ax_hist.set_xscale("log")
    ax_hist.set_xlabel(f"{label} (per radix node)")
    ax_hist.set_ylabel("# nodes")
    ax_hist.set_title("Histogram")
    ax_hist.grid(True, alpha=0.3)

    sorted_arr = np.sort(arr)
    cdf = np.arange(1, len(sorted_arr) + 1) / len(sorted_arr)
    ax_cdf.plot(sorted_arr, cdf, color=color_cdf)
    if arr.max() > arr.min():
        ax_cdf.set_xscale("log")
    ax_cdf.set_xlabel(label)
    ax_cdf.set_ylabel("CDF")
    ax_cdf.set_title(
        f"CDF  (mean={arr.mean():.2f}, p50={np.percentile(arr,50):.2f}, "
        f"p90={np.percentile(arr,90):.2f}, max={arr.max():.2f})"
    )
    ax_cdf.grid(True, alpha=0.3)

    fig.suptitle(f"Subtree-hits-{stat} distribution: {trace_name}")
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)
    print(f"  {stat.upper()} distribution PNG written to {output_path} ({len(arr)} nodes)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_sequences(filepath: str) -> list[list[int]]:
    sequences = []
    with open(filepath) as f:
        for line in f:
            try:
                obj = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            hids = obj.get("hash_ids", [])
            if hids:
                sequences.append(hids)
    return sequences


def main():
    parser = argparse.ArgumentParser(
        description="Analyze and visualize prefix radix trees from traces")
    parser.add_argument("trace_files", nargs="+", help="Trace JSONL files")
    parser.add_argument("--output-dir", default="prefix_tree",
                        help="Output directory for SVGs")
    parser.add_argument("--max-depth", type=int, default=6,
                        help="Max radix-tree depth to render (default: 6)")
    parser.add_argument("--min-hit-frac", type=float, default=0.01,
                        help="Min hit fraction to show a child (default: 0.01)")
    parser.add_argument("--max-nodes", type=int, default=200,
                        help="Max total graph nodes to render (default: 200)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    for filepath in args.trace_files:
        trace_name = Path(filepath).stem
        print(f"\nProcessing: {trace_name}")

        sequences = load_sequences(filepath)
        if not sequences:
            print(f"  No sequences found, skipping.")
            continue

        print(f"  Loaded {len(sequences)} requests")
        trie = build_trie(sequences)
        radix = compress_trie(trie)

        print_summary(trace_name, radix, sequences)

        svg_path = os.path.join(args.output_dir, f"{trace_name}_prefix_tree.svg")
        render_tree_svg(radix, svg_path, trace_name,
                        max_depth=args.max_depth,
                        min_hit_frac=args.min_hit_frac,
                        max_total_nodes=args.max_nodes)

        mean_path = os.path.join(args.output_dir, f"{trace_name}_subtree_mean_dist.png")
        plot_subtree_stat_distribution(radix, mean_path, trace_name, "mean")
        p90_path = os.path.join(args.output_dir, f"{trace_name}_subtree_p90_dist.png")
        plot_subtree_stat_distribution(radix, p90_path, trace_name, "p90")


if __name__ == "__main__":
    main()
