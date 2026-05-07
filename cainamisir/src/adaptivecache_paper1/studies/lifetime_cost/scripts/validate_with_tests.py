"""Validate Phase D resolved/failed claims by ACTUALLY running the
SWE-bench tests after applying the agent's edits.

For each trajectory in an out_dir:

  1. Reconstruct the agent's edits by walking response.tool_calls and
     extracting every edit_file(path, old_string, new_string).
  2. Create a per-instance isolated venv (cached across trajectories
     of the same instance — venvs are slow to build).
  3. Clone the repo at base_commit into a tempdir.
  4. Apply the agent's edits.
  5. Apply the SWE-bench test_patch (the test additions for this fix).
  6. Run pytest on the FAIL_TO_PASS list. If all pass, this is a TRUE
     resolve. (PASS_TO_PASS is a regression check; we surface it but
     don't require it to be all-pass to call something resolved — many
     repos have flaky tests pre-fix.)
  7. Write `validated.jsonl` next to the trajectory file with the verdict.

Usage:

  .venv/bin/python -m studies.lifetime_cost.scripts.validate_with_tests \\
      --out_dir studies/lifetime_cost/out/phase_d_v2_10tasks \\
      --venv_root /tmp/swebench_val_venvs

Caveats:

  - Some repos have C extensions / native deps that won't install in our
    plain venv. Those tasks just fail validation. Track which ones and
    treat them as "validation_unavailable" rather than "failed".
  - The reconstruction from edit_file calls assumes ALL edits succeeded
    (the runner's edit_file returns "OK" or an error; we don't replay
    failed edits). Most edits succeed; some may have failed at runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


# Older SWE-bench base_commits (especially psf/requests pre-2018, pylint pre-2022)
# don't run on Python 3.12 — `collections.MutableMapping` was moved to
# `collections.abc` in 3.10. Use Python 3.9 (installed via uv) for the venvs.
PYTHON_3_9 = Path.home() / ".local" / "bin" / "python3.9"
if not PYTHON_3_9.exists():
    PYTHON_3_9 = Path("python3.9")  # PATH fallback

from datasets import load_dataset


@dataclass
class ValidationResult:
    instance_id: str
    policy: str
    n_edits_attempted: int
    n_edits_applied: int
    install_ok: bool
    install_log_tail: str
    fail_to_pass_results: Dict[str, str]   # test name → "pass" | "fail" | "error"
    pass_to_pass_results: Dict[str, str]
    real_resolved: bool                     # all FAIL_TO_PASS pass
    note: str


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def get_or_create_venv(venv_root: Path, instance_id: str, repo_path: Path) -> Path:
    """One venv per instance; reused across all policies' trajectories of
    that instance. Idempotent — skips if already built."""
    vpath = venv_root / instance_id
    py = vpath / "bin" / "python"
    if py.exists():
        # Already built; quick check it has pytest
        try:
            subprocess.run([str(py), "-m", "pytest", "--version"],
                           capture_output=True, check=True, timeout=10)
            return vpath
        except Exception:
            pass
    print(f"  building venv for {instance_id} (py3.9)…", flush=True)
    venv_root.mkdir(parents=True, exist_ok=True)
    if vpath.exists():
        shutil.rmtree(vpath)
    # Use Python 3.9 — most SWE-bench Lite base_commits predate 3.10 changes.
    subprocess.run([str(PYTHON_3_9), "-m", "venv", str(vpath)], check=True, timeout=120)
    pip = vpath / "bin" / "pip"
    # Pre-install transitive deps that older repos expect to be available.
    subprocess.run([str(pip), "install", "--quiet", "pytest", "urllib3", "chardet",
                    "idna", "certifi"], check=False, timeout=180)
    return vpath


def install_repo_into_venv(vpath: Path, repo_path: Path) -> tuple[bool, str]:
    pip = vpath / "bin" / "pip"
    proc = subprocess.run(
        [str(pip), "install", "--quiet", "-e", str(repo_path)],
        capture_output=True, text=True, timeout=300,
    )
    log_tail = (proc.stdout or "")[-400:] + ("\n" + (proc.stderr or "")[-400:] if proc.stderr else "")
    return (proc.returncode == 0), log_tail


# ---------------------------------------------------------------------------
# Trajectory replay
# ---------------------------------------------------------------------------

def extract_agent_edits(traj: Dict[str, Any]) -> List[Dict[str, str]]:
    """Walk all response.tool_calls in the trajectory; return the sequence of
    edit_file invocations in order."""
    edits = []
    for s in traj.get("steps", []):
        tcs = s.get("response", {}).get("tool_calls") or []
        for tc in tcs:
            fn = tc.get("function") or {}
            if fn.get("name") != "edit_file":
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    continue
            if not isinstance(args, dict):
                continue
            path = args.get("path")
            old = args.get("old_string")
            new = args.get("new_string")
            if not path or old is None or new is None:
                continue
            edits.append({"path": path, "old_string": old, "new_string": new})
    return edits


def apply_edits(repo_dir: Path, edits: List[Dict[str, str]]) -> int:
    """Apply each edit. Returns the count successfully applied."""
    applied = 0
    for e in edits:
        target = repo_dir / e["path"]
        if not target.exists():
            continue
        try:
            text = target.read_text(errors="replace")
        except Exception:
            continue
        old, new = e["old_string"], e["new_string"]
        n = text.count(old)
        if n == 0:
            continue
        if n > 1:
            # Replace first occurrence only (matches the runner's behavior)
            target.write_text(text.replace(old, new, 1))
        else:
            target.write_text(text.replace(old, new))
        applied += 1
    return applied


def apply_test_patch(repo_dir: Path, test_patch: str) -> bool:
    """Apply a unified diff (the SWE-bench test_patch) to the repo."""
    if not test_patch.strip():
        return True
    proc = subprocess.run(
        ["git", "apply", "--allow-empty", "-"],
        input=test_patch, text=True, cwd=repo_dir, capture_output=True, timeout=30,
    )
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------

def run_one_test(vpath: Path, repo_dir: Path, test_id: str, timeout: int = 90) -> str:
    """Returns 'pass' | 'fail' | 'error'."""
    py = vpath / "bin" / "python"
    proc = subprocess.run(
        [str(py), "-m", "pytest", "-x", "-q", "--no-header", "--tb=no",
         "--disable-warnings", test_id],
        cwd=repo_dir, capture_output=True, text=True, timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return "pass"
    if "failed" in out or "FAILED" in out or "AssertionError" in out:
        return "fail"
    return "error"


# ---------------------------------------------------------------------------
# Main per-trajectory validator
# ---------------------------------------------------------------------------

def validate_trajectory(
    traj: Dict[str, Any],
    inst: Dict[str, Any],
    cached_repo: Path,
    venv_root: Path,
    work_root: Path,
    policy_label: str,
) -> ValidationResult:
    instance_id = inst["instance_id"]
    base_commit = inst["base_commit"]
    test_patch = inst.get("test_patch", "")

    # FAIL_TO_PASS / PASS_TO_PASS may be a JSON string in the dataset
    def _parse_tests(field):
        v = inst.get(field, [])
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v or []

    fail_to_pass = _parse_tests("FAIL_TO_PASS")
    pass_to_pass = _parse_tests("PASS_TO_PASS")

    note = ""
    work_dir = work_root / f"{instance_id}__{policy_label}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    # Clone repo at base commit
    repo_dir = work_dir / "repo"
    subprocess.run(["git", "clone", "--quiet", "--no-checkout",
                    str(cached_repo), str(repo_dir)], check=True)
    subprocess.run(["git", "checkout", "--quiet", base_commit],
                   cwd=repo_dir, check=True)

    # Apply agent's edits (replayed from trajectory)
    edits = extract_agent_edits(traj)
    n_applied = apply_edits(repo_dir, edits)

    # Apply test_patch
    tp_ok = apply_test_patch(repo_dir, test_patch)
    if not tp_ok:
        note = "test_patch_apply_failed"

    # Set up venv (cached) and install the (now-edited) repo
    vpath = get_or_create_venv(venv_root, instance_id, repo_dir)
    install_ok, install_log_tail = install_repo_into_venv(vpath, repo_dir)

    fail_results: Dict[str, str] = {}
    pass_results: Dict[str, str] = {}
    real_resolved = False

    if install_ok:
        for tid in fail_to_pass:
            try:
                fail_results[tid] = run_one_test(vpath, repo_dir, tid)
            except subprocess.TimeoutExpired:
                fail_results[tid] = "error"
        # quick sample of PASS_TO_PASS — full list can be huge; check first 5
        for tid in pass_to_pass[:5]:
            try:
                pass_results[tid] = run_one_test(vpath, repo_dir, tid)
            except subprocess.TimeoutExpired:
                pass_results[tid] = "error"
        real_resolved = bool(fail_to_pass) and all(
            v == "pass" for v in fail_results.values()
        )
    else:
        note = (note + "; install_failed").lstrip("; ")

    return ValidationResult(
        instance_id=instance_id,
        policy=policy_label,
        n_edits_attempted=len(edits),
        n_edits_applied=n_applied,
        install_ok=install_ok,
        install_log_tail=install_log_tail,
        fail_to_pass_results=fail_results,
        pass_to_pass_results=pass_results,
        real_resolved=real_resolved,
        note=note,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--venv_root", default="/tmp/swebench_val_venvs")
    ap.add_argument("--work_root", default="/tmp/swebench_val_work")
    ap.add_argument("--cache_dir", default="/scratch/swebench_repos")
    ap.add_argument("--max_per_policy", type=int, default=None,
                    help="Cap how many trajectories per policy to validate (debug).")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    venv_root = Path(args.venv_root)
    work_root = Path(args.work_root)
    cache_dir = Path(args.cache_dir)

    print(f"Loading SWE-bench Lite…", flush=True)
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    by_id = {x["instance_id"]: x for x in ds}

    # Find all trajectory files
    traj_files = sorted(out_dir.rglob("*.jsonl"))
    print(f"Found {len(traj_files)} trajectory files in {out_dir}\n", flush=True)

    all_results: List[ValidationResult] = []
    for tf in traj_files:
        policy = tf.stem  # filename label
        with open(tf) as f:
            for tline_idx, line in enumerate(f):
                if not line.strip():
                    continue
                if args.max_per_policy and tline_idx >= args.max_per_policy:
                    break
                traj = json.loads(line)
                instance_id = traj.get("task_id")
                if instance_id not in by_id:
                    print(f"  [skip] {policy}/{instance_id}: not in SWE-bench Lite")
                    continue
                inst = by_id[instance_id]
                repo_tail = inst["repo"].split("/", 1)[1]
                cached_repo = cache_dir / repo_tail
                if not (cached_repo / ".git").exists():
                    print(f"  [skip] {policy}/{instance_id}: cached repo not at {cached_repo}")
                    continue
                print(f"  validating {policy}/{instance_id}…", flush=True)
                try:
                    r = validate_trajectory(
                        traj, inst, cached_repo, venv_root, work_root, policy_label=policy,
                    )
                except Exception as e:
                    print(f"    ERROR: {type(e).__name__}: {e}")
                    continue
                all_results.append(r)
                # Inline summary
                tag = "RESOLVED" if r.real_resolved else (
                    "fail" if r.install_ok else "no_install"
                )
                print(f"    {tag} | edits {r.n_edits_applied}/{r.n_edits_attempted}"
                      f" | F2P {sum(v=='pass' for v in r.fail_to_pass_results.values())}/{len(r.fail_to_pass_results)}"
                      f"{(' | note: ' + r.note) if r.note else ''}")

    # Write results
    out_path = out_dir / "validated.json"
    with open(out_path, "w") as f:
        json.dump([r.__dict__ for r in all_results], f, indent=2)
    print(f"\nWrote {len(all_results)} validation results to {out_path}")

    # Quick aggregate
    print("\n=== aggregate ===")
    by_policy: Dict[str, List[ValidationResult]] = {}
    for r in all_results:
        by_policy.setdefault(r.policy, []).append(r)
    print(f"{'policy':<32s} {'real_resolved':>16s} {'install_ok':>11s}")
    print("-" * 70)
    for pol in sorted(by_policy):
        rs = by_policy[pol]
        n = len(rs)
        n_resolved = sum(1 for r in rs if r.real_resolved)
        n_install = sum(1 for r in rs if r.install_ok)
        print(f"{pol:<32s} {n_resolved}/{n:>3d}{'':>10s} {n_install}/{n:>3d}")


if __name__ == "__main__":
    main()
