"""Diagnostic for V4: weight jitter + label imbalance.

Subclasses S3FIFOLearnedV4 with an SGD-update hook that records:
  - weight + bias snapshots every K decisions
  - running label-1 fraction per window
  - cumulative label distribution

Outputs per (trace, cache_size):
  - final weights + bias
  - stddev of each weight over second half of trajectory  -> jitter
  - global label-1 fraction                                -> label imbalance
  - per-window label-1 stats (min/max/std)
  - sign-flips per weight                                  -> non-convergence

Usage:
    python3.11 plugins/diag_v4.py <trace> <cache_size> [limit]
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from sim import process_trace, TRACES
from learned_promotion import S3FIFOLearnedV4


class V4Diag(S3FIFOLearnedV4):
    SNAP_K = 200

    def __init__(self, cache_size: int, **kwargs):
        super().__init__(cache_size, **kwargs)
        self.snaps: list[tuple[int, list[float], float]] = []  # (n, w, b)
        self.window_pos = 0  # labels in current window
        self.window_total = 0
        self.window_label_rate: list[float] = []
        self.global_pos = 0
        self.global_total = 0

    def _sgd_update(self, x, y, p):
        super()._sgd_update(x, y, p)
        self.global_total += 1
        if y > 0.5:
            self.global_pos += 1
            self.window_pos += 1
        self.window_total += 1
        if self.global_total % self.SNAP_K == 0:
            self.snaps.append((self.global_total, list(self.w), self.b))
            self.window_label_rate.append(self.window_pos / max(1, self.window_total))
            self.window_pos = 0
            self.window_total = 0


def jitter_metrics(snaps):
    """Per-weight stats from snapshot trajectories.

    Returns dict with mean/stddev/range over the *second half* (post-warmup),
    sign-flip count over the full trajectory, and the final values.
    """
    if not snaps:
        return None
    n = len(snaps)
    half = n // 2
    if half < 2:
        half = 0
    second = snaps[half:]
    nw = len(snaps[0][1])
    out = {"n_snapshots": n, "weights": []}
    for i in range(nw):
        traj = [s[1][i] for s in snaps]
        traj_2nd = [s[1][i] for s in second]
        flips = 0
        prev = None
        for v in traj:
            s_v = 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
            if prev is not None and s_v != 0 and prev != 0 and s_v != prev:
                flips += 1
            if s_v != 0:
                prev = s_v
        out["weights"].append({
            "final": traj[-1],
            "mean_2nd_half": statistics.fmean(traj_2nd) if traj_2nd else traj[-1],
            "stddev_2nd_half": (
                statistics.stdev(traj_2nd) if len(traj_2nd) >= 2 else 0.0),
            "min": min(traj),
            "max": max(traj),
            "range_2nd_half": (max(traj_2nd) - min(traj_2nd)) if traj_2nd else 0.0,
            "sign_flips": flips,
        })
    btraj = [s[2] for s in snaps]
    btraj_2nd = btraj[half:]
    out["bias"] = {
        "final": btraj[-1],
        "mean_2nd_half": statistics.fmean(btraj_2nd) if btraj_2nd else btraj[-1],
        "stddev_2nd_half": (
            statistics.stdev(btraj_2nd) if len(btraj_2nd) >= 2 else 0.0),
        "min": min(btraj),
        "max": max(btraj),
    }
    return out


def label_metrics(diag: V4Diag):
    g = diag.global_pos / max(1, diag.global_total)
    rates = diag.window_label_rate
    out = {
        "n_decisions": diag.global_total,
        "global_label1_rate": g,
        "global_label0_rate": 1 - g,
        "n_windows": len(rates),
        "window_size": V4Diag.SNAP_K,
    }
    if rates:
        out["window_min"] = min(rates)
        out["window_max"] = max(rates)
        out["window_mean"] = statistics.fmean(rates)
        out["window_stddev"] = (statistics.stdev(rates) if len(rates) >= 2 else 0.0)
        # How many windows are degenerate (all 0 or all 1)?
        out["windows_all_0"] = sum(1 for r in rates if r == 0.0)
        out["windows_all_1"] = sum(1 for r in rates if r == 1.0)
    return out


def run(trace: str, cache_size: int, limit: int | None) -> dict:
    if trace not in TRACES:
        raise SystemExit(f"unknown trace {trace}; have {sorted(TRACES)}")
    reqs = TRACES[trace](limit)
    if not isinstance(reqs, list):
        reqs = list(reqs)
    diag = V4Diag(cache_size)
    req_mr, byte_mr = process_trace(diag, reqs)
    return {
        "trace": trace,
        "cache_size": cache_size,
        "n_requests": len(reqs),
        "req_mr": req_mr,
        "byte_mr": byte_mr,
        "final_w": list(diag.w),
        "final_b": diag.b,
        "n_promote": diag.n_promote,
        "n_evict_from_s": diag.n_evict_from_s,
        "promote_rate": (diag.n_promote / max(1, diag.n_evict_from_s)),
        "n_fallback_fired": diag.n_fallback_fired,
        "labels": label_metrics(diag),
        "jitter": jitter_metrics(diag.snaps),
    }


def fmt(r: dict) -> str:
    L = r["labels"]
    J = r["jitter"]
    out = []
    out.append(
        f"=== {r['trace']} cache={r['cache_size']} reqs={r['n_requests']} "
        f"req_mr={r['req_mr']:.4f} ==="
    )
    out.append(
        f"  decisions={L['n_decisions']} promote_rate={r['promote_rate']:.4f} "
        f"fallback_fired={r['n_fallback_fired']}"
    )
    out.append(
        f"  LABELS: y=1 global={L['global_label1_rate']:.4f}  "
        f"y=0 global={L['global_label0_rate']:.4f}"
    )
    if "window_min" in L:
        out.append(
            f"          window y=1 min={L['window_min']:.3f} max={L['window_max']:.3f} "
            f"mean={L['window_mean']:.3f} std={L['window_stddev']:.3f}  "
            f"all-0 windows={L['windows_all_0']}/{L['n_windows']} "
            f"all-1 windows={L['windows_all_1']}/{L['n_windows']}"
        )
    if J is None:
        out.append("  JITTER: (no decisions)")
        return "\n".join(out)
    names = ["loghits", "age", "recency"]
    out.append(f"  JITTER (snapshots every {V4Diag.SNAP_K} decisions, n={J['n_snapshots']}):")
    for i, w in enumerate(J["weights"]):
        out.append(
            f"    w_{names[i]:7s} final={w['final']:+.4f} mean_2nd={w['mean_2nd_half']:+.4f} "
            f"std_2nd={w['stddev_2nd_half']:.4f} range_2nd={w['range_2nd_half']:.4f} "
            f"sign_flips={w['sign_flips']}"
        )
    b = J["bias"]
    out.append(
        f"    b           final={b['final']:+.4f} mean_2nd={b['mean_2nd_half']:+.4f} "
        f"std_2nd={b['stddev_2nd_half']:.4f}"
    )
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    trace = sys.argv[1]
    cache_size = int(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) >= 4 else None
    r = run(trace, cache_size, limit)
    print(fmt(r))
    out_path = Path(__file__).parent / "result" / f"diag_v4_{trace}_c{cache_size}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(r, indent=2))
    print(f"  -> {out_path}")
