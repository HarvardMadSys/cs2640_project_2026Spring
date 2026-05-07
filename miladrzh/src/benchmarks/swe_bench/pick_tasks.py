"""
Download SWE-bench Lite from HuggingFace and pick slow-test tasks.

Filters to repos where pytest runs are consistently 20s+ (the idle window
where KV offload pays off): sympy, scikit-learn, matplotlib, astropy.

Run once from repo root:
    python benchmarks/swe_bench/pick_tasks.py

Output: benchmarks/swe_bench/selected_tasks.json
"""

import json
import os
from collections import defaultdict

SLOW_REPOS = [
    "sympy/sympy",
    "scikit-learn/scikit-learn",
    "matplotlib/matplotlib",
    "astropy/astropy",
]

TASKS_PER_REPO = 8
OUTPUT = os.path.join(os.path.dirname(__file__), "selected_tasks.json")


def main():
    from datasets import load_dataset

    print("Loading SWE-bench Lite from HuggingFace...")
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")

    by_repo = defaultdict(list)
    for row in ds:
        if row["repo"] not in SLOW_REPOS:
            continue

        fail = row["FAIL_TO_PASS"]
        if isinstance(fail, str):
            fail = json.loads(fail)
        passes = row["PASS_TO_PASS"]
        if isinstance(passes, str):
            passes = json.loads(passes)

        by_repo[row["repo"]].append({
            "id": row["instance_id"],
            "agent_type": "swe_bench",
            "benchmark": "swe_bench_lite",
            "repo": row["repo"],
            "base_commit": row["base_commit"],
            "environment_setup_commit": row.get("environment_setup_commit", ""),
            "problem_statement": row["problem_statement"],
            "hints_text": row.get("hints_text", ""),
            "fail_to_pass": fail,
            "pass_to_pass": passes,
            "version": row.get("version", ""),
            "test_patch": row.get("test_patch", ""),  # adds the FAIL_TO_PASS tests
            "patch": row.get("patch", ""),            # gold source fix (reference only)
        })

    selected = []
    for repo in SLOW_REPOS:
        tasks = by_repo[repo][:TASKS_PER_REPO]
        selected.extend(tasks)
        print(f"  {repo}: {len(tasks)} tasks")

    with open(OUTPUT, "w") as f:
        json.dump(selected, f, indent=2)

    print(f"\nSaved {len(selected)} tasks -> {OUTPUT}")


if __name__ == "__main__":
    main()
