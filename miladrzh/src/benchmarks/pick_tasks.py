"""
Print top SWE-bench Verified tasks from slow repos, ranked by FAIL_TO_PASS count.
Run with: conda run -n rag python benchmarks/pick_tasks.py
"""

from datasets import load_dataset

SLOW_REPOS = {
    "scipy/scipy",
    "matplotlib/matplotlib",
    "sympy/sympy",
    "scikit-learn/scikit-learn",
    "astropy/astropy",
    "sqlalchemy/sqlalchemy",
    "django/django",
}

TOP_N = 15

ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")

candidates = []
for row in ds:
    repo = row["repo"]
    if repo not in SLOW_REPOS:
        continue
    fail_to_pass = row["FAIL_TO_PASS"]
    # field is a string of a Python list, e.g. "['test_a', 'test_b']"
    if isinstance(fail_to_pass, str):
        n_tests = fail_to_pass.count("::") + fail_to_pass.count("test_")
    else:
        n_tests = len(fail_to_pass)
    candidates.append({
        "instance_id": row["instance_id"],
        "repo": repo,
        "n_tests": n_tests,
        "fail_to_pass_raw": fail_to_pass[:120],
    })

candidates.sort(key=lambda x: x["n_tests"], reverse=True)

print(f"\nTop {TOP_N} tasks from slow repos (ranked by FAIL_TO_PASS count)\n")
print(f"{'instance_id':<50} {'repo':<30} {'n_tests':>7}")
print("-" * 90)
for c in candidates[:TOP_N]:
    print(f"{c['instance_id']:<50} {c['repo']:<30} {c['n_tests']:>7}")

print(f"\nTotal candidates from slow repos: {len(candidates)}")
