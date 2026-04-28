"""Hermes-only analysis: cliff + lifetime cost + reference-graph importance.

Runs all five compaction policies through the simulator on N Hermes traces,
then layers a reference-graph analysis: for each tool result message,
check whether its content (or some substring of it) gets cited in a later
tool call's arguments. This gives an empirical importance signal that the
AC dataset cannot answer.

Output:
  studies/lifetime_cost/out/hermes/
    cost_summary.csv            — policy × cost-model lifetime cost
    reference_graph.csv         — per-message importance + downstream citations
    importance_distribution.png — histogram of citation counts by message role
    policy_cost.png             — bar chart per policy

Usage:
    python -m studies.lifetime_cost.scripts.analyze_hermes \\
        --config kimi --max-traces 100
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

from studies.lifetime_cost.pipeline.external_traces import (
    evaluate_policies_on_traces,
    load_hermes_agent_traces,
)
from studies.lifetime_cost.pipeline.pricing import PriceSheet
from studies.lifetime_cost.pipeline.types import Trajectory


# ---------------------------------------------------------------------------
# Reference graph
# ---------------------------------------------------------------------------

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)
TOOL_RESPONSE_RE = re.compile(r"<tool_response>\s*(.*?)\s*</tool_response>", re.DOTALL)


def _extract_tool_calls(text: str) -> List[dict]:
    """Pull JSON tool_call blocks out of an assistant message. Hermes uses
    the <tool_call>{json}</tool_call> wrapper."""
    out = []
    for m in TOOL_CALL_RE.finditer(text or ""):
        try:
            d = json.loads(m.group(1))
            out.append(d)
        except json.JSONDecodeError:
            pass
    return out


def _extract_tool_response(text: str) -> str:
    """Pull the response payload out of a tool message. Often itself JSON
    or plain text inside <tool_response>...</tool_response>."""
    m = TOOL_RESPONSE_RE.search(text or "")
    if m:
        return m.group(1).strip()
    return (text or "").strip()


def _significant_tokens(text: str, min_len: int = 4) -> set:
    """Tokens worth caring about for citation-detection: alphanumeric runs of
    `min_len` or longer. Lowercased."""
    return {w.lower() for w in re.findall(rf"[A-Za-z0-9_\-]{{{min_len},}}", text or "")}


def reference_graph(traj: Trajectory) -> Dict[int, Dict[str, int]]:
    """For each message index in the trajectory, count how many *later*
    assistant tool calls cite content from it.

    Citation = the later tool_call's `arguments` JSON contains a substring
    (significant token, ≥4 chars) that appeared in this message.

    Returns: {msg_idx: {"role": ..., "tokens": int, "downstream_cites": int}}
    """
    msgs = traj.extra.get("trajectory_messages") or []
    if not msgs:
        return {}

    # Pre-compute significant token set per message
    msg_tokens: List[set] = []
    for m in msgs:
        content = m.get("content") or ""
        if m["role"] == "tool":
            content = _extract_tool_response(content)
        msg_tokens.append(_significant_tokens(content))

    # For each later assistant message, build the set of tokens appearing in
    # any of its tool_calls' arguments
    later_arg_tokens: List[set] = [set() for _ in msgs]
    for i, m in enumerate(msgs):
        if m["role"] != "assistant":
            continue
        calls = _extract_tool_calls(m.get("content") or "")
        if not calls:
            continue
        # Pool all argument bytes from this assistant turn
        bag = []
        for c in calls:
            bag.append(json.dumps(c.get("arguments", {}), default=str))
        later_arg_tokens[i] = _significant_tokens(" ".join(bag))

    # For each msg i, count how many later assistant turns cite tokens from i
    out: Dict[int, Dict[str, int]] = {}
    for i, m in enumerate(msgs):
        if not msg_tokens[i]:
            out[i] = {"role": m["role"], "tokens": int(m.get("_token_count") or 0),
                      "downstream_cites": 0, "category_cites": 0}
            continue
        cites = 0
        for j in range(i + 1, len(msgs)):
            if msgs[j]["role"] != "assistant":
                continue
            if not later_arg_tokens[j]:
                continue
            # Citation if there's a non-trivial token overlap
            if msg_tokens[i] & later_arg_tokens[j]:
                cites += 1
        out[i] = {"role": m["role"], "tokens": int(m.get("_token_count") or 0),
                  "downstream_cites": cites, "category_cites": 0}

    return out


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def plot_importance_by_role(graphs: List[Dict[int, dict]], out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_role: Dict[str, List[int]] = defaultdict(list)
    for g in graphs:
        for info in g.values():
            by_role[info["role"]].append(info["downstream_cites"])

    roles = ["system", "user", "assistant", "tool"]
    data = [by_role.get(r, []) for r in roles]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.boxplot(data, tick_labels=roles, showfliers=False)
    ax.set_ylabel("Downstream citations (per message)")
    ax.set_title("Importance signal by message role — Hermes Agent Reasoning Traces")
    ax.grid(True, axis="y", alpha=0.3)
    for i, vals in enumerate(data, 1):
        if vals:
            ax.annotate(f"med={statistics.median(vals):.0f}\nn={len(vals)}",
                        (i, statistics.median(vals)),
                        textcoords="offset points", xytext=(15, 0),
                        fontsize=9, va="center")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_position_vs_cites(graphs: List[Dict[int, dict]], out_path: Path):
    """Heatmap-style: x=relative position in trajectory, y=role, color=mean cites."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    bins = 10
    by_pos: Dict[Tuple[str, int], List[int]] = defaultdict(list)
    for g in graphs:
        n = len(g)
        if n == 0:
            continue
        for i, info in g.items():
            rel_bin = min(int(i * bins / n), bins - 1)
            by_pos[(info["role"], rel_bin)].append(info["downstream_cites"])

    roles = ["system", "user", "assistant", "tool"]
    grid = np.zeros((len(roles), bins))
    for ri, role in enumerate(roles):
        for b in range(bins):
            vals = by_pos.get((role, b), [])
            grid[ri, b] = statistics.mean(vals) if vals else 0

    fig, ax = plt.subplots(figsize=(10, 4))
    im = ax.imshow(grid, aspect="auto", cmap="viridis", interpolation="nearest")
    ax.set_yticks(range(len(roles)))
    ax.set_yticklabels(roles)
    ax.set_xticks(range(bins))
    ax.set_xticklabels([f"{i*10}-{(i+1)*10}%" for i in range(bins)], rotation=30)
    ax.set_xlabel("Relative position in trajectory")
    ax.set_title("Mean downstream citations by (role, position) — Hermes")
    fig.colorbar(im, ax=ax, label="mean citations")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_policy_cost(rows: List[dict], out_path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    by_pol_model: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for r in rows:
        by_pol_model[(r["policy"], r["cost_model"])].append(r["lifetime_cost"])

    policies = sorted({p for (p, _) in by_pol_model})
    models = sorted({m for (_, m) in by_pol_model})

    fig, ax = plt.subplots(figsize=(11, 5))
    bw = 0.8 / max(len(policies), 1)
    x = np.arange(len(models))
    colors = {"none": "#1f77b4", "naive_summary": "#2ca02c", "microcompact": "#ff7f0e",
              "prefix_preserving": "#9467bd", "boundary_aware": "#8c564b"}
    for i, p in enumerate(policies):
        means = [statistics.mean(by_pol_model.get((p, m), [0])) for m in models]
        ax.bar(x + i * bw, means, bw, label=p, color=colors.get(p, "gray"))

    ax.set_xticks(x + bw * (len(policies) - 1) / 2)
    ax.set_xticklabels([m.split("/")[-1] for m in models], rotation=20, ha="right")
    ax.set_ylabel("Mean lifetime $ per task")
    ax.set_title("Hermes Agent Reasoning Traces — lifetime cost by policy × provider")
    ax.legend(fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="kimi", choices=["kimi", "glm-5.1"])
    ap.add_argument("--max-traces", type=int, default=100)
    ap.add_argument("--budget", type=int, default=32_000)
    ap.add_argument("--hard-budget", type=int, default=64_000)
    ap.add_argument("--out", default="studies/lifetime_cost/out/hermes")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Loading {args.max_traces} Hermes traces from {args.config}...")
    trajs = load_hermes_agent_traces(config=args.config, max_traces=args.max_traces)
    print(f"  got {len(trajs)} (some filtered for trivially short)")
    for t in trajs[:5]:
        print(f"    {t.task_id[:13]}  steps={len(t.steps):3d}  prompt={t.total_prompt_tokens:>9,}  cat={t.extra.get('category','?')}")

    sheet = PriceSheet()
    cost_models = ["anthropic/claude-haiku-4-5", "anthropic/claude-sonnet-4-6",
                   "openai/gpt-4.1", "openai/gpt-4.1-mini", "qwen/qwen3-30b-a3b"]

    policy_specs = [
        {"name": "none"},
        {"name": "naive_summary", "kwargs": {"recent_keep": 4, "trigger_ratio": 0.85}},
        {"name": "microcompact", "kwargs": {"per_msg_threshold_tokens": 800, "trigger_ratio": 0.85}},
        {"name": "prefix_preserving", "kwargs": {"keep_first_turns": 6, "keep_recent_turns": 4, "trigger_ratio": 0.85}},
        {"name": "boundary_aware", "kwargs": {"keep_first_turns": 6, "keep_recent_turns": 4, "trigger_ratio": 0.85}},
    ]

    print("\nSimulating policies...")
    result = evaluate_policies_on_traces(
        trajs, policy_specs,
        sheet=sheet, cost_models=cost_models,
        budget_tokens=args.budget, hard_budget_tokens=args.hard_budget,
    )
    rows = result["rows"]

    cost_path = out / "cost_summary.csv"
    with open(cost_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["benchmark", "policy", "cost_model", "n", "mean_cost", "mean_compactions"])
        agg = defaultdict(lambda: {"n": 0, "cost": 0.0, "comp": 0})
        for r in rows:
            key = (r["benchmark"], r["policy"], r["cost_model"])
            agg[key]["n"] += 1
            agg[key]["cost"] += r["lifetime_cost"]
            agg[key]["comp"] += r["n_compactions"]
        for (b, p, cm), v in sorted(agg.items()):
            w.writerow([b, p, cm, v["n"], f"{v['cost']/v['n']:.6f}", f"{v['comp']/v['n']:.2f}"])
    print(f"Wrote {cost_path}")

    # ------------------------------------------------------------------
    # Reference graph
    # ------------------------------------------------------------------
    print("\nBuilding reference graph...")
    graphs = []
    for t in trajs:
        g = reference_graph(t)
        graphs.append(g)

    rg_path = out / "reference_graph.csv"
    with open(rg_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "msg_index", "role", "tokens", "downstream_cites"])
        for t, g in zip(trajs, graphs):
            n_msgs = len(g)
            for i, info in g.items():
                w.writerow([t.task_id, i, info["role"], info["tokens"], info["downstream_cites"]])
    print(f"Wrote {rg_path}")

    # Aggregate importance stats
    cite_totals = Counter()
    cite_counts_per_role = defaultdict(int)
    for g in graphs:
        for info in g.values():
            cite_counts_per_role[info["role"]] += 1
            cite_totals[info["role"]] += info["downstream_cites"]

    print("\n=== Citation totals by role (across all loaded traces) ===")
    for role, total in sorted(cite_totals.items(), key=lambda kv: -kv[1]):
        n = cite_counts_per_role[role]
        print(f"  {role:10s}  n_msgs={n:5d}  total_cites={total:6d}  mean_cites={total/max(n,1):.2f}")

    plot_importance_by_role(graphs, out / "importance_by_role.png")
    plot_position_vs_cites(graphs, out / "importance_by_position.png")
    plot_policy_cost(rows, out / "policy_cost.png")
    print(f"\nFigures under {out}/")

    # Headline summary on Haiku prices
    print("\n=== Mean $/task at Haiku 4.5 prices (Hermes traces) ===")
    haiku = [r for r in rows if r["cost_model"] == "anthropic/claude-haiku-4-5"]
    by_pol = defaultdict(list)
    for r in haiku:
        by_pol[r["policy"]].append(r["lifetime_cost"])
    for p in sorted(by_pol, key=lambda p: statistics.mean(by_pol[p])):
        m = statistics.mean(by_pol[p])
        print(f"  {p:20s}  ${m:.4f}/task  (n={len(by_pol[p])})")


if __name__ == "__main__":
    main()
