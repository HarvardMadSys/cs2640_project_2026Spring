"""
One-shot script: build benchmarks/_sweep512.json — a curated frozen
task pool of 512 mixed RAG tasks (~equal thirds GAIA / HotpotQA / BrowseComp,
seed=0). GAIA is supply-limited (~165 questions in 2023_all validation+test
of Levels 1/2/3); we take all available GAIA, then balance the remaining
slots equally between HotpotQA and BrowseComp.

Run from repo root:
    conda run -n rag python benchmarks/build_sweep512.py
"""

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SEED = 0
TARGET = 512


def _t(task_id, prompt, benchmark, gold=None):
    d = {"id": task_id, "agent_type": "rag", "benchmark": benchmark, "prompt": prompt}
    if gold is not None:
        d["gold_answer"] = gold
    return d


def load_gaia():
    """Load all available GAIA Levels 1/2/3 from validation + test."""
    out = []
    try:
        from datasets import load_dataset
        ds = load_dataset("gaia-benchmark/GAIA", "2023_all", trust_remote_code=True)
        for split_name in ("validation", "test"):
            if split_name not in ds:
                continue
            for i, row in enumerate(ds[split_name]):
                try:
                    level = int(row.get("Level", 0))
                except (TypeError, ValueError):
                    continue
                if level not in (1, 2, 3):
                    continue
                raw_id = row.get("task_id", i)
                tid = f"gaia_L{level}_{split_name}_{raw_id}"
                out.append(_t(tid, row["Question"], "gaia",
                              gold=row.get("Final answer")))
    except Exception as e:
        print(f"[gaia] HF load failed: {e}")
    return out


def main():
    rnd = random.Random(SEED)

    gaia = load_gaia()
    print(f"[gaia] available: {len(gaia)}")

    hotpot_path = os.path.join(ROOT, "benchmarks", "hotpotqa", "tasks.json")
    browse_path = os.path.join(ROOT, "benchmarks", "browsecomp", "tasks.json")
    with open(hotpot_path) as f:
        hotpot = json.load(f)
    with open(browse_path) as f:
        browse = json.load(f)
    print(f"[hotpot] available: {len(hotpot)}")
    print(f"[browse] available: {len(browse)}")

    # Equal thirds, but GAIA is supply-limited: take all GAIA, split rest.
    one_third = TARGET // 3
    n_gaia = min(len(gaia), one_third)
    remaining = TARGET - n_gaia
    n_hotpot = remaining // 2
    n_browse = remaining - n_hotpot

    if n_hotpot > len(hotpot):
        sys.exit(f"need {n_hotpot} hotpot but only {len(hotpot)} available")
    if n_browse > len(browse):
        sys.exit(f"need {n_browse} browse but only {len(browse)} available")

    sampled_gaia   = rnd.sample(gaia,   n_gaia)
    sampled_hotpot = rnd.sample(hotpot, n_hotpot)
    sampled_browse = rnd.sample(browse, n_browse)

    pool = sampled_gaia + sampled_hotpot + sampled_browse
    rnd.shuffle(pool)

    out_path = os.path.join(ROOT, "benchmarks", "_sweep512.json")
    with open(out_path, "w") as f:
        json.dump(pool, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {len(pool)} tasks -> {out_path}")
    print(f"  gaia   : {n_gaia}")
    print(f"  hotpot : {n_hotpot}")
    print(f"  browse : {n_browse}")


if __name__ == "__main__":
    main()
