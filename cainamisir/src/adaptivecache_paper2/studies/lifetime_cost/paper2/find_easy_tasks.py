"""Find smaller swebench Lite tasks in our cached repos.

Heuristic: small gold patch (< 800 chars) → likely a focused fix that a
competent agent can solve in 20-30 steps. We sort cached-repo tasks by
gold-patch length and print the smallest ones.

Run:
    .venv-paper2/bin/python -m studies.lifetime_cost.paper2.find_easy_tasks
"""
import os
os.environ.setdefault("HF_HOME", "/scratch/hf/")
from datasets import load_dataset

CACHED_REPOS = {"psf/requests", "pallets/flask", "pylint-dev/pylint", "pytest-dev/pytest"}


def main():
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    rows = []
    for row in ds:
        if row["repo"] not in CACHED_REPOS:
            continue
        patch_len = len(row.get("patch") or "")
        test_patch_len = len(row.get("test_patch") or "")
        rows.append({
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "patch_len": patch_len,
            "test_patch_len": test_patch_len,
            "problem_first_200": (row.get("problem_statement") or "")[:200],
        })

    rows.sort(key=lambda r: r["patch_len"])
    print(f"Found {len(rows)} tasks across {len(CACHED_REPOS)} cached repos\n")
    print(f"{'instance_id':<28} {'repo':<22} {'patch':>6} {'test':>5}  problem")
    for r in rows[:15]:
        print(f"{r['instance_id']:<28} {r['repo']:<22} {r['patch_len']:>6} {r['test_patch_len']:>5}  {r['problem_first_200'][:80]}")


if __name__ == "__main__":
    main()
