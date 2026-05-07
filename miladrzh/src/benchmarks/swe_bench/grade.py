"""
SWE-bench correctness grader.

After the agent finishes, run the FAIL_TO_PASS and PASS_TO_PASS tests in the
workspace using the per-repo conda env and parse pass/fail per test.

Resolution rule (SWE-bench convention):
  - resolved iff every fail_to_pass test passes AND every pass_to_pass test
    still passes.

Returns a dict suitable to embed under trace["correctness_grader"].
"""

import os
import re
import subprocess
from typing import Iterable

# pytest summary line examples we need to parse:
#   PASSED tests/test_foo.py::test_bar
#   FAILED tests/test_foo.py::test_bar - AssertionError: ...
#   ERROR tests/test_foo.py::test_bar - ImportError: ...
#   SKIPPED [1] tests/test_foo.py::test_bar: reason
_OUTCOME_RE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(\S+)", re.MULTILINE)


def _run_pytest(workspace: str, venv_bin: str, test_ids: Iterable[str], timeout: int = 600) -> dict:
    test_ids = list(test_ids)
    if not test_ids:
        return {"results": {}, "stdout_tail": "", "exit_code": 0}

    env = os.environ.copy()
    env["PATH"] = venv_bin + ":" + env.get("PATH", "")
    env["VIRTUAL_ENV"] = os.path.dirname(venv_bin)
    env.pop("PYTHONHOME", None)

    cmd = [
        os.path.join(venv_bin, "pytest"),
        "-rN",        # don't collapse PASSED into a header — we need them in summary
        "--tb=short",
        "-p", "no:cacheprovider",
        *test_ids,
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=workspace, capture_output=True, text=True,
            env=env, timeout=timeout,
        )
        out = proc.stdout + "\n" + proc.stderr
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + "\n[TIMEOUT]\n" + (e.stderr or "")
        rc = -1

    # pytest -rN prints a "short test summary info" section near the end with
    # one line per non-passing test. -rA would include passed too; we want
    # both, so use -rA actually... fix: switch to -rA.
    return {"results_raw": out, "exit_code": rc}


def _classify(out: str, test_ids: list) -> dict:
    """Return {test_id: 'passed'|'failed'|'error'|'skipped'|'missing'}."""
    seen = {}
    for m in _OUTCOME_RE.finditer(out):
        outcome, tid = m.group(1), m.group(2)
        seen[tid] = outcome.lower()

    # pytest sometimes prints `tests/foo.py::bar PASSED` inline (no -r line).
    # Catch those too.
    inline = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)", re.MULTILINE)
    for m in inline.finditer(out):
        tid, outcome = m.group(1), m.group(2)
        seen.setdefault(tid, outcome.lower())

    results = {}
    for tid in test_ids:
        # Match exactly, OR by suffix (pytest may use relative path), OR
        # by parametrization prefix (PASS_TO_PASS uses bare names but pytest
        # reports `name[param-1]` for parametrized tests). If a bare name
        # has multiple param instances, treat it as passed only if ALL pass.
        hit = None
        if tid in seen:
            hit = seen[tid]
        else:
            matches = []
            for k, v in seen.items():
                if k == tid or k.endswith(tid) or tid.endswith(k):
                    matches.append(v); continue
                # parametrized case: tid="...::test_x" matches k="...::test_x[case]"
                if k.startswith(tid + "[") or tid.startswith(k + "["):
                    matches.append(v); continue
                # also match suffix-with-bracket: bare tid vs full path with params
                if "::" in tid and "::" in k:
                    tid_short = tid.split("::")[-1]
                    k_short = k.split("::")[-1].split("[")[0]
                    if tid_short.split("[")[0] == k_short and tid.split("::")[:-1] and \
                       k.endswith(tid_short.split("[")[0]) is False:
                        pass  # avoid false positives, no-op
            if matches:
                hit = "passed" if all(m == "passed" for m in matches) else \
                      next((m for m in matches if m != "passed"), "passed")
        results[tid] = hit or "missing"
    return results


def _run_and_classify(task: dict, ids: list, timeout: int = 600) -> dict:
    """Run the given test ids and return {tid: status}. Used both by setup
    (baseline pass) and grade (post-agent pass)."""
    workspace = task["workspace_dir"]
    venv_bin = task["venv_bin"]
    env = os.environ.copy()
    env["PATH"] = venv_bin + ":" + env.get("PATH", "")
    env["VIRTUAL_ENV"] = os.path.dirname(venv_bin)
    env.pop("PYTHONHOME", None)

    if not ids:
        return {}
    files = sorted({i.split("::")[0] for i in ids if "::" in i})
    cmd_co = [os.path.join(venv_bin, "pytest"), "--collect-only", "-q",
              "-p", "no:cacheprovider", *files]
    proc = subprocess.run(cmd_co, cwd=workspace, capture_output=True,
                          text=True, env=env, timeout=300)
    collected = set()
    for line in (proc.stdout + proc.stderr).splitlines():
        line = line.rstrip()
        if "::" in line and not line.lstrip().startswith(("=", "ERROR", "warning")):
            collected.add(line.lstrip())

    keep = []
    for tid in ids:
        if tid in collected:
            keep.append(tid)
        else:
            keep.extend(sorted(c for c in collected if c.startswith(tid + "[")))

    if not keep:
        return {tid: "missing" for tid in ids}

    cmd = [os.path.join(venv_bin, "pytest"), "-rA", "--tb=short",
           "-p", "no:cacheprovider", *keep]
    try:
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True,
                              text=True, env=env, timeout=timeout)
        out = proc.stdout + "\n" + proc.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + "\n[TIMEOUT]\n" + (e.stderr or "")

    return _classify(out, ids)


def _restore_test_files(task: dict):
    """Revert any agent edits to the test files (anti-cheat: an agent must
    fix source code, not the tests). The setup commit at HEAD is the
    post-test_patch baseline."""
    workspace = task.get("workspace_dir")
    files = task.get("_test_patch_files") or []
    if not workspace or not files:
        return []
    cmd = ["git", "checkout", "HEAD", "--"] + files
    proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
    return [{"files_restored": files, "rc": proc.returncode,
             "stderr": proc.stderr[:500]}]


def grade(task: dict, timeout_per_group: int = 600) -> dict:
    """Run the two test groups, return a summary dict."""
    workspace = task.get("workspace_dir")
    venv_bin = task.get("venv_bin")
    if not workspace or not venv_bin:
        return {"error": "task missing workspace_dir or venv_bin"}

    restore_log = _restore_test_files(task)

    fail_to_pass = task.get("fail_to_pass", [])
    pass_to_pass = task.get("pass_to_pass", [])

    env = os.environ.copy()
    env["PATH"] = venv_bin + ":" + env.get("PATH", "")
    env["VIRTUAL_ENV"] = os.path.dirname(venv_bin)
    env.pop("PYTHONHOME", None)

    def _filter_to_existing(ids):
        # Pytest 9 aborts the whole session if any positional test arg is
        # "not found", even with --continue-on-collection-errors. So we
        # pre-collect the test files referenced and keep only IDs that match
        # something pytest can actually find. We use prefix matching to
        # tolerate parametrization (`name` vs `name[case]`).
        if not ids:
            return [], {}, ""
        files = sorted({i.split("::")[0] for i in ids if "::" in i})
        if not files:
            return list(ids), {i: i for i in ids}, ""
        cmd = [os.path.join(venv_bin, "pytest"), "--collect-only", "-q",
               "-p", "no:cacheprovider", *files]
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True,
                              text=True, env=env, timeout=300)
        collected = set()
        for line in (proc.stdout + proc.stderr).splitlines():
            line = line.rstrip()
            # pytest -q --collect-only outputs one full nodeid per line, e.g.
            # `path/to/test_x.py::test_y[case with spaces inside brackets]`.
            # Don't split on spaces — parametrization values can contain them.
            if "::" in line and not line.lstrip().startswith(("=", "ERROR", "warning")):
                collected.add(line.lstrip())
        keep, mapping = [], {}
        for tid in ids:
            if tid in collected:
                keep.append(tid); mapping[tid] = tid
            else:
                # parametrized: take all collected starting with tid + "["
                params = sorted(c for c in collected if c.startswith(tid + "["))
                if params:
                    keep.extend(params)
                    mapping[tid] = params  # list -> aggregate later
        return keep, mapping, proc.stdout + proc.stderr

    def _run(ids):
        keep, mapping, collect_log = _filter_to_existing(ids)
        if not keep:
            return {"results_raw": collect_log, "exit_code": 5,
                    "filter_mapping": mapping, "missing": [i for i in ids if i not in mapping]}
        cmd = [os.path.join(venv_bin, "pytest"), "-rA", "--tb=short",
               "-p", "no:cacheprovider", *keep]
        try:
            proc = subprocess.run(cmd, cwd=workspace, capture_output=True,
                                  text=True, env=env, timeout=timeout_per_group)
            return {"results_raw": proc.stdout + "\n" + proc.stderr,
                    "exit_code": proc.returncode,
                    "filter_mapping": mapping,
                    "missing": [i for i in ids if i not in mapping]}
        except subprocess.TimeoutExpired as e:
            return {"results_raw": (e.stdout or "") + "\n[TIMEOUT]\n" + (e.stderr or ""),
                    "exit_code": -1, "filter_mapping": mapping,
                    "missing": [i for i in ids if i not in mapping]}

    f2p_run = _run(fail_to_pass)
    p2p_run = _run(pass_to_pass)

    f2p_results = _classify(f2p_run["results_raw"], fail_to_pass)
    p2p_results = _classify(p2p_run["results_raw"], pass_to_pass)

    f2p_passed = sum(1 for v in f2p_results.values() if v == "passed")
    p2p_passed = sum(1 for v in p2p_results.values() if v == "passed")

    # Only tests that PASSED at baseline (before the agent) count as
    # potential regressions. This avoids penalizing the agent for tests
    # that were already broken in the env at base_commit (e.g. matplotlib
    # image-comparison tests that need baseline images we don't have).
    baseline_passing = set(task.get("_p2p_baseline_passing") or pass_to_pass)
    p2p_regressions = [
        k for k, v in p2p_results.items()
        if k in baseline_passing and v not in ("passed", "missing")
    ]

    resolved = (
        len(fail_to_pass) > 0
        and f2p_passed == len(fail_to_pass)
        and len(p2p_regressions) == 0
    )

    return {
        "resolved": resolved,
        "fail_to_pass_total": len(fail_to_pass),
        "fail_to_pass_passed": f2p_passed,
        "pass_to_pass_total": len(pass_to_pass),
        "pass_to_pass_passed": p2p_passed,
        "pass_to_pass_regressions": p2p_regressions,
        "fail_to_pass_results": f2p_results,
        "pass_to_pass_results": p2p_results,
        "fail_to_pass_missing": f2p_run.get("missing", []),
        "pass_to_pass_missing": p2p_run.get("missing", []),
        "fail_to_pass_pytest_exit_code": f2p_run["exit_code"],
        "pass_to_pass_pytest_exit_code": p2p_run["exit_code"],
        # Tail of pytest output for debugging when things go sideways.
        "fail_to_pass_pytest_tail": f2p_run["results_raw"][-2000:],
        "pass_to_pass_pytest_tail": p2p_run["results_raw"][-2000:],
        "test_files_restored": restore_log,
    }
