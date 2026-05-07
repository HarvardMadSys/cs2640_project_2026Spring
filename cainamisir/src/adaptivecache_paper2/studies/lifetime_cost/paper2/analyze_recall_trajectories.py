"""Post-run trajectory comparison for the v1 recall validation.

Given two trajectory JSONs (recall=off and recall=on) on the same task,
emits a step-by-step diff: tool calls per step, recall events, and a
running per-file read count. Used to interpret validate_recall results.

    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.analyze_recall_trajectories \\
        out_v0_swebench/memento_norecall_psf__requests-3362.json \\
        out_v0_swebench/memento_recall_psf__requests-3362.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


def _tool_call_summary(tcs: List[Dict[str, Any]]) -> str:
    if not tcs:
        return "(no tool call)"
    parts = []
    for tc in tcs:
        fn = tc.get("function") or {}
        name = fn.get("name", "?")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"_raw": args}
        if name == "read_file":
            parts.append(f"read_file({args.get('path', '?')})")
        elif name == "search":
            parts.append(f"search({args.get('query', '?')!r})")
        elif name == "edit_file":
            parts.append(f"edit_file({args.get('path', '?')})")
        elif name == "submit":
            parts.append("submit()")
        elif name == "list_files":
            parts.append(f"list_files({args.get('path', '?')})")
        elif name == "run_tests":
            parts.append(f"run_tests({args.get('path', '?')})")
        else:
            parts.append(f"{name}(...)")
    return ", ".join(parts)


def _summarize(traj: Dict[str, Any], label: str) -> Dict[str, Any]:
    steps = traj.get("steps", [])
    reads: Counter = Counter()
    n_recalls = 0
    n_compactions = 0
    for s in steps:
        for tc in (s.get("response", {}).get("tool_calls") or []):
            fn = tc.get("function") or {}
            if fn.get("name") == "read_file":
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                p = args.get("path", "?") if isinstance(args, dict) else "?"
                reads[p] += 1
        if s.get("recall_before"):
            n_recalls += 1
        if s.get("compaction_after"):
            n_compactions += 1
    return {
        "label": label,
        "steps": len(steps),
        "resolved": traj.get("resolved"),
        "reads": reads,
        "n_recalls": n_recalls,
        "n_compactions": n_compactions,
    }


def _print_step_diff(a_steps: List[Dict], b_steps: List[Dict], a_label: str, b_label: str):
    n = max(len(a_steps), len(b_steps))
    print(f"\n  {'step':<5}  {a_label:<40}  {b_label:<40}  marks")
    for i in range(n):
        a_call = _tool_call_summary(a_steps[i].get("response", {}).get("tool_calls") or []) if i < len(a_steps) else ""
        b_call = _tool_call_summary(b_steps[i].get("response", {}).get("tool_calls") or []) if i < len(b_steps) else ""
        marks = []
        if i < len(a_steps) and a_steps[i].get("compaction_after"):
            marks.append("Aᶜ")
        if i < len(b_steps) and b_steps[i].get("compaction_after"):
            marks.append("Bᶜ")
        if i < len(a_steps) and a_steps[i].get("recall_before"):
            marks.append("Aʳ")
        if i < len(b_steps) and b_steps[i].get("recall_before"):
            marks.append("Bʳ")
        differ = " *" if a_call != b_call else ""
        print(f"  {i:<5}  {a_call[:40]:<40}  {b_call[:40]:<40}  {' '.join(marks)}{differ}")


def main():
    if len(sys.argv) < 3:
        print("usage: analyze_recall_trajectories.py <recall_off.json> <recall_on.json>", file=sys.stderr)
        sys.exit(2)

    p_off = Path(sys.argv[1])
    p_on = Path(sys.argv[2])
    t_off = json.loads(p_off.read_text())
    t_on = json.loads(p_on.read_text())

    s_off = _summarize(t_off, p_off.stem)
    s_on = _summarize(t_on, p_on.stem)

    print(f"task: {t_off.get('task_id')!r} (off vs on, same task)")
    print()
    print(f"  {'metric':<22}  {'recall=off':>14}  {'recall=on':>14}")
    print(f"  {'-'*22}  {'-'*14}  {'-'*14}")
    print(f"  {'steps':<22}  {s_off['steps']:>14}  {s_on['steps']:>14}")
    print(f"  {'resolved':<22}  {str(s_off['resolved']):>14}  {str(s_on['resolved']):>14}")
    print(f"  {'compactions':<22}  {s_off['n_compactions']:>14}  {s_on['n_compactions']:>14}")
    print(f"  {'recalls':<22}  {s_off['n_recalls']:>14}  {s_on['n_recalls']:>14}")
    print(f"  {'total read_file calls':<22}  {sum(s_off['reads'].values()):>14}  {sum(s_on['reads'].values()):>14}")

    all_paths = sorted(set(s_off['reads']) | set(s_on['reads']),
                       key=lambda p: -(s_off['reads'][p] + s_on['reads'][p]))
    print(f"\n  per-path read counts (top 8):")
    print(f"  {'path':<48}  {'off':>5}  {'on':>5}  {'Δ':>5}")
    for p in all_paths[:8]:
        n_off = s_off['reads'].get(p, 0)
        n_on = s_on['reads'].get(p, 0)
        print(f"  {p[:48]:<48}  {n_off:>5}  {n_on:>5}  {n_on - n_off:>+5}")

    # Acceptance check
    target = "requests/models.py"
    target_on = s_on['reads'].get(target, 0)
    print(f"\n  ACCEPTANCE: {target!r} re-reads on recall=on = {target_on} (target: ≤2)")
    print(f"  RESULT: {'PASS ✓' if target_on <= 2 else 'FAIL ✗'}")

    # Step diff
    print("\nstep-by-step (Aᶜ=A compaction, Bᶜ=B compaction, Aʳ=A recall, Bʳ=B recall, *=differs)")
    _print_step_diff(t_off.get("steps", []), t_on.get("steps", []),
                     "recall=off", "recall=on")


if __name__ == "__main__":
    main()
