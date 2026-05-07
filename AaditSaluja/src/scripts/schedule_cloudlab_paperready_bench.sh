#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="report/results/cloudlab-paperready-$(date +%Y%m%d-%H%M%S)"
BENCH_ROOT="/mnt/cephfs/cs2640-bench"
TOOLS_DIR="${HOME}/bench-tools"
REPEATS=3
SEED_BASE=9100
ORDER_SEED=26400507
HEALTH_TIMEOUT=180
SLEEP_BETWEEN_RUNS=5
FILEBENCH_RUNTIME=60
RESUME=1
INCLUDE_ABLATION=0
INCLUDE_FALSE_HOT=0
INCLUDE_SCALED=0
SCALED_REPEATS=3

usage() {
  cat <<'EOF'
Usage: schedule_cloudlab_paperready_bench.sh [options]

Runs direct external IOR/mdtest/Filebench validation jobs and recreated in-repo
policy comparisons. Results are serial, randomized, resumable, and summarized
after every completed job.

Options:
  --out-dir PATH          Output directory under the repo.
  --root PATH             CephFS benchmark root. Default: /mnt/cephfs/cs2640-bench
  --tools PATH            External tool prefix. Default: $HOME/bench-tools
  --repeats N             Repeats per cell. Default: 3
  --seed-base N           First repeat seed. Default: 9100
  --order-seed N          Randomization seed for job order. Default: 26400507
  --filebench-runtime N   Filebench timed run length in seconds. Default: 60
  --health-timeout SEC    Time to wait for HEALTH_OK before each run.
  --sleep SEC             Sleep between runs. Default: 5
  --no-resume             Re-run even if result files already exist.
  --include-ablation      Add predictor_nolearn cold-by-default ablation cells.
  --include-false-hot     Add predictor_false_hot_churn downside workload cells.
  --include-scaled        Add scaled hot/cold 20k-file cells.
  --scaled-repeats N      Repeats for scaled cells. Default: 3
  --help                  Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --root)
      BENCH_ROOT="$2"
      shift 2
      ;;
    --tools)
      TOOLS_DIR="$2"
      shift 2
      ;;
    --repeats)
      REPEATS="$2"
      shift 2
      ;;
    --seed-base)
      SEED_BASE="$2"
      shift 2
      ;;
    --order-seed)
      ORDER_SEED="$2"
      shift 2
      ;;
    --filebench-runtime)
      FILEBENCH_RUNTIME="$2"
      shift 2
      ;;
    --health-timeout)
      HEALTH_TIMEOUT="$2"
      shift 2
      ;;
    --sleep)
      SLEEP_BETWEEN_RUNS="$2"
      shift 2
      ;;
    --no-resume)
      RESUME=0
      shift
      ;;
    --include-ablation)
      INCLUDE_ABLATION=1
      shift
      ;;
    --include-false-hot)
      INCLUDE_FALSE_HOT=1
      shift
      ;;
    --include-scaled)
      INCLUDE_SCALED=1
      shift
      ;;
    --scaled-repeats)
      SCALED_REPEATS="$2"
      shift 2
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

MDTEST="${TOOLS_DIR}/bin/mdtest"
IOR="${TOOLS_DIR}/bin/ior"
FILEBENCH="${TOOLS_DIR}/bin/filebench"

mkdir -p "$OUT_DIR"
JOBS="$OUT_DIR/jobs.tsv"
SHUFFLED_JOBS="$OUT_DIR/jobs_shuffled.tsv"
COMPLETED="$OUT_DIR/completed.tsv"
FAILED="$OUT_DIR/FAILED"
CACHE_LOG="$OUT_DIR/cache_drop.log"

require_tools() {
  local missing=0
  for tool in "$MDTEST" "$IOR" "$FILEBENCH"; do
    if [[ ! -x "$tool" ]]; then
      echo "missing executable: $tool" >&2
      missing=1
    fi
  done
  if ! command -v mpirun >/dev/null 2>&1; then
    echo "missing executable: mpirun" >&2
    missing=1
  fi
  if [[ "$missing" -ne 0 ]]; then
    exit 1
  fi
}

write_fairness_note() {
  cat > "$OUT_DIR/FAIRNESS.md" <<EOF
# Paper-Ready Benchmark Fairness Notes

Started: $(date -Is)

This run has two roles:

- Direct external validation: IOR, mdtest, and Filebench run directly against
  the mounted CephFS filesystem using binaries under \`$TOOLS_DIR\`.
- In-repo policy comparison: recreated workload shapes run through the local
  Python runner against \`native\`, \`oracle_cold_segments\`, and
  \`predictive_cold_segments\`.

Controls:

- Jobs are serial and globally randomized with seed \`$ORDER_SEED\`.
- Each policy cell uses the same workload parameters and repeat seed.
- Each job gets a unique CephFS root under \`$BENCH_ROOT\`.
- \`ceph -s\` must report HEALTH_OK before each job.
- Before each job, the scheduler drops the node0 Linux page cache, drops active
  MDS caches, and runs \`sync\`. Server-node Linux page caches cannot be dropped
  from node0 because this allocation does not allow node0 SSH access to the
  server nodes with the current key setup; this is recorded as a residual
  limitation.
- Filebench requires Linux address-space randomization disabled for stable
  worker startup on this build. The scheduler sets
  \`kernel.randomize_va_space=0\` at startup.

Policy information boundaries:

- \`native\` has no packing or predictor layer.
- \`oracle\` only receives benchmark-defined hot prefixes on workloads with a
  generator hot set: \`oracle_hotcold_mix\`, \`ycsb_file_skew\`, and
  \`predictor_false_hot_churn\`. Generic recreated mdtest/Filebench shapes
  receive no hidden hot labels and are packed as all-cold.
- \`predictor\` does not inspect \`hot*\` or \`cold*\` path labels for placement.
  It observes online read events, learns hot parent directories, and makes later
  creates under predicted-hot directories native. Existing packed files are not
  rewritten during measured read/stat phases.
- \`predictor_nolearn\`, when enabled, uses the same predictive storage layer
  but disables learning with \`predictor_strategy=never_promote\`. It is a
  cold-by-default packing ablation that isolates packing benefit from predictor
  benefit.
- \`predictor_false_hot_churn\`, when enabled, intentionally drives reads to
  generator-cold directories before a later create wave. It exposes the
  downside of false-hot directory classification: later cold creates become
  native under the predictor but remain packed under oracle and no-learning
  packing.

Outputs:

- Raw logs: \`*.log\`
- Exact commands: \`*.cmd\`
- In-repo JSON/CSV: \`*.json\`, \`*.csv\`
- External sidecar JSON: \`*.external.json\`
- Incremental summaries: \`run_manifest.csv\`, \`summary.csv\`,
  \`phase_summary.csv\`, \`external_phase_summary.csv\`, and \`summary.md\`
EOF
}

write_jobs() {
  printf 'job_kind\tworkload_id\tworkload\tvariant\trepeat\tseed\tfile_count\tfile_size\tdirs\tops\tworkers\tdepth\tbranching\tycsb_distribution\tycsb_hot_fraction\tycsb_update_fraction\tycsb_hot_op_fraction\tycsb_zipf_alpha\truntime\n' > "$JOBS"
  local repeat seed variant
  local variants=(native oracle predictor)
  if [[ "$INCLUDE_ABLATION" == "1" ]]; then
    variants+=(predictor_nolearn)
  fi

  for ((repeat = 0; repeat < REPEATS; repeat++)); do
    seed=$((SEED_BASE + repeat))
    printf 'external_mdtest\tdirect_mdtest\tmdtest\texternal\t%d\t%d\t3000\t1024\t64\t0\t8\t3\t8\tnone\t0\t0\t0\t0\t0\n' "$repeat" "$seed" >> "$JOBS"
    printf 'external_ior\tdirect_ior\tior\texternal\t%d\t%d\t0\t4096\t0\t512\t8\t0\t0\tnone\t0\t0\t0\t0\t0\n' "$repeat" "$seed" >> "$JOBS"
    printf 'external_filebench\tdirect_filebench_fileserver\tfilebench_fileserver\texternal\t%d\t%d\t3000\t4096\t64\t0\t8\t0\t0\tnone\t0\t0\t0\t0\t%d\n' "$repeat" "$seed" "$FILEBENCH_RUNTIME" >> "$JOBS"
    printf 'external_mdtest\tdirect_mdtest_10k\tmdtest_10k\texternal\t%d\t%d\t10000\t1024\t64\t0\t8\t3\t8\tnone\t0\t0\t0\t0\t0\n' "$repeat" "$seed" >> "$JOBS"
    printf 'external_ior\tdirect_ior_512m\tior_512m\texternal\t%d\t%d\t0\t4096\t0\t16384\t8\t0\t0\tnone\t0\t0\t0\t0\t0\n' "$repeat" "$seed" >> "$JOBS"
    printf 'external_filebench\tdirect_filebench_varmail\tfilebench_varmail\texternal\t%d\t%d\t3000\t4096\t64\t0\t8\t0\t0\tnone\t0\t0\t0\t0\t%d\n' "$repeat" "$seed" "$FILEBENCH_RUNTIME" >> "$JOBS"

    for variant in "${variants[@]}"; do
      printf 'inrepo\trecreated_mdtest_tree\tmdtest_tree\t%s\t%d\t%d\t3000\t1024\t64\t6000\t8\t3\t8\tzipfian\t0.20\t0.20\t0.80\t0.99\t0\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'inrepo\trecreated_filebench_varmail_like\tfilebench_varmail_like\t%s\t%d\t%d\t3000\t4096\t64\t9000\t8\t2\t8\tzipfian\t0.20\t0.20\t0.80\t0.99\t0\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'inrepo\tycsb_zipfian_file_skew\tycsb_file_skew\t%s\t%d\t%d\t3000\t4096\t64\t9000\t8\t2\t8\tzipfian\t0.20\t0.20\t0.80\t0.99\t0\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'inrepo\tycsb_hotspot_file_skew\tycsb_file_skew\t%s\t%d\t%d\t3000\t4096\t64\t9000\t8\t2\t8\thotspot\t0.20\t0.20\t0.80\t0.99\t0\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'inrepo\thotcold_cold90_access10\toracle_hotcold_mix\t%s\t%d\t%d\t3000\t4096\t64\t12000\t8\t2\t8\tzipfian\t0.20\t0.20\t0.80\t0.99\t0\n' "$variant" "$repeat" "$seed" >> "$JOBS"
    done

    if [[ "$INCLUDE_FALSE_HOT" == "1" ]]; then
      for variant in "${variants[@]}"; do
        printf 'inrepo\tpredictor_false_hot_churn\tpredictor_false_hot_churn\t%s\t%d\t%d\t3000\t4096\t64\t9000\t8\t2\t8\tzipfian\t0.20\t0.20\t0.80\t0.99\t0\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      done
    fi
  done

  if [[ "$INCLUDE_SCALED" == "1" ]]; then
    for ((repeat = 0; repeat < SCALED_REPEATS; repeat++)); do
      seed=$((SEED_BASE + 100 + repeat))
      for variant in "${variants[@]}"; do
        printf 'inrepo\tscaled_hotcold_cold90_access10_20k\toracle_hotcold_mix\t%s\t%d\t%d\t20000\t4096\t128\t80000\t8\t2\t8\tzipfian\t0.20\t0.20\t0.80\t0.99\t0\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      done
    done
  fi
}

shuffle_jobs() {
  python3 - "$JOBS" "$SHUFFLED_JOBS" "$ORDER_SEED" <<'PY'
import csv
import random
import sys

source, target, seed = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(source, newline="") as handle:
    reader = csv.DictReader(handle, delimiter="\t")
    rows = list(reader)
random.Random(seed).shuffle(rows)
with open(target, "w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=reader.fieldnames, delimiter="\t")
    writer.writeheader()
    writer.writerows(rows)
PY
}

wait_for_health() {
  local deadline last_output
  deadline=$((SECONDS + HEALTH_TIMEOUT))
  last_output=""
  while [[ $SECONDS -lt $deadline ]]; do
    if last_output="$(ceph -s 2>&1)" && grep -q 'health: HEALTH_OK' <<<"$last_output"; then
      return 0
    fi
    sleep 5
  done
  echo "Ceph did not become HEALTH_OK:" >&2
  echo "$last_output" >&2
  return 1
}

active_mds_names() {
  ceph mds stat --format json 2>/dev/null | python3 -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for fs in data.get("fsmap", {}).get("filesystems", []):
    info = fs.get("mdsmap", {}).get("info", {})
    for daemon in info.values():
        if daemon.get("state") == "up:active" and daemon.get("name"):
            print(daemon["name"])
'
}

disable_aslr_for_filebench() {
  sudo sysctl -w kernel.randomize_va_space=0 >> "$CACHE_LOG" 2>&1 || true
}

drop_caches() {
  {
    echo "== cache drop $(date -Is) =="
    sync
    sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
    while IFS= read -r mds_name; do
      [[ -n "$mds_name" ]] || continue
      ceph tell "mds.${mds_name}" cache drop || true
    done < <(active_mds_names)
  } >> "$CACHE_LOG" 2>&1 || true
}

storage_args() {
  local workload="$1"
  local variant="$2"
  local hot_prefix="__no_oracle_hot_prefix__"

  case "$variant" in
    native)
      printf '%s\n' --storage native
      ;;
    oracle)
      if [[ "$workload" == "oracle_hotcold_mix" || "$workload" == "ycsb_file_skew" || "$workload" == "predictor_false_hot_churn" ]]; then
        hot_prefix="hot"
      fi
      printf '%s\n' \
        --storage oracle_cold_segments \
        --storage-opt "hot_prefixes=${hot_prefix}" \
        --storage-opt virtual_cold_dirs=true \
        --storage-opt index_batch_bytes=1048576 \
        --storage-opt index_batch_records=512 \
        --storage-opt cold_data_batch_bytes=1048576 \
        --storage-opt cold_data_backend=cephfs \
        --storage-opt packed_stat_mode=index
      ;;
    predictor)
      printf '%s\n' \
        --storage predictive_cold_segments \
        --storage-opt virtual_cold_dirs=true \
        --storage-opt index_batch_bytes=1048576 \
        --storage-opt index_batch_records=512 \
        --storage-opt cold_data_batch_bytes=1048576 \
        --storage-opt cold_data_backend=cephfs \
        --storage-opt packed_stat_mode=index \
        --storage-opt predictor_strategy=directory_hotset \
        --storage-opt predictor_directory_promotion=true \
        --storage-opt predictor_promote_existing=false \
        --storage-opt predictor_dir_event_threshold=8 \
        --storage-opt predictor_dir_distinct_threshold=3 \
        --storage-opt predictor_eval_hot_prefixes=hot \
        --storage-opt promotion_threshold=1 \
        --storage-opt promotion_triggers=read
      ;;
    predictor_nolearn)
      printf '%s\n' \
        --storage predictive_cold_segments \
        --storage-opt virtual_cold_dirs=true \
        --storage-opt index_batch_bytes=1048576 \
        --storage-opt index_batch_records=512 \
        --storage-opt cold_data_batch_bytes=1048576 \
        --storage-opt cold_data_backend=cephfs \
        --storage-opt packed_stat_mode=index \
        --storage-opt predictor_strategy=never_promote \
        --storage-opt predictor_directory_promotion=false \
        --storage-opt predictor_promote_existing=false \
        --storage-opt predictor_eval_hot_prefixes=hot \
        --storage-opt promotion_threshold=1 \
        --storage-opt promotion_triggers=read
      ;;
    *)
      echo "unknown variant: $variant" >&2
      return 1
      ;;
  esac
}

summarize_results() {
  python3 - "$OUT_DIR" <<'PY'
import csv
import json
import re
import statistics
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
records = []
phase_rows = []

for path in sorted(out_dir.glob("*.json")):
    if path.name.startswith("."):
        continue
    try:
        document = json.loads(path.read_text())
    except Exception:
        continue

    if document.get("result_kind") == "external":
        metrics = document.get("metrics", {})
        records.append(
            {
                "result_kind": "external",
                "workload_id": document.get("workload_id", ""),
                "workload": document.get("workload", ""),
                "variant": document.get("variant", "external"),
                "repeat": document.get("repeat", ""),
                "seed": document.get("seed", ""),
                "json": str(path),
                "csv": "",
                "log": document.get("log", ""),
                "measured_ops_per_sec": float(metrics.get("ops_per_sec") or 0.0),
                "measured_seconds": float(document.get("elapsed_seconds", 0.0)),
                "elapsed_seconds": float(document.get("elapsed_seconds", 0.0)),
                "run_mean_p95_ms": "",
                "predicted_hot_dirs": "",
                "predicted_eval_hot_dirs": "",
                "predicted_eval_cold_dirs": "",
                "predicted_hot_dir_precision": "",
                "total_promotions": "",
                "promoted_hot": "",
                "promoted_cold": "",
                "layout_failures": "",
            }
        )
        for phase in document.get("phase_metrics", []):
            phase_rows.append(
                {
                    "result_kind": "external",
                    "workload_id": document.get("workload_id", ""),
                    "variant": document.get("variant", "external"),
                    "operation": phase.get("operation", ""),
                    "runs": 1,
                    "ops_per_sec": float(phase.get("ops_per_sec") or 0.0),
                    "latency_p95_ms": "",
                    "latency_p99_ms": "",
                }
            )
        continue

    args = document.get("args", {})
    stem = path.stem
    parts = stem.split("__")
    workload_id = parts[0] if len(parts) >= 3 else args.get("workload", "")
    variant = parts[1] if len(parts) >= 3 else args.get("storage", "")
    repeat = parts[2][1:] if len(parts) >= 3 and parts[2].startswith("r") else ""
    storage = document.get("storage_metrics", {})
    phase_p95 = [
        float(row.get("latency_p95_ms", 0.0))
        for row in document.get("results", [])
    ]
    records.append(
        {
            "result_kind": "inrepo",
            "workload_id": workload_id,
            "workload": args.get("workload", ""),
            "variant": variant,
            "repeat": repeat,
            "seed": args.get("seed", ""),
            "json": str(path),
            "csv": str(path.with_suffix(".csv")),
            "log": str(path.with_suffix(".log")),
            "measured_ops_per_sec": float(document.get("measured_ops_per_sec", 0.0)),
            "measured_seconds": float(document.get("measured_seconds", 0.0)),
            "elapsed_seconds": float(document.get("elapsed_seconds", 0.0)),
            "run_mean_p95_ms": statistics.fmean(phase_p95) if phase_p95 else 0.0,
            "predicted_hot_dirs": storage.get("predicted_hot_dirs", ""),
            "predicted_eval_hot_dirs": storage.get("predicted_eval_hot_dirs", ""),
            "predicted_eval_cold_dirs": storage.get("predicted_eval_cold_dirs", ""),
            "predicted_hot_dir_precision": storage.get("predicted_hot_dir_precision", ""),
            "total_promotions": storage.get("total_promotions", ""),
            "promoted_hot": storage.get("total_promoted_eval_hot_paths", ""),
            "promoted_cold": storage.get("total_promoted_eval_cold_paths", ""),
            "layout_failures": len(storage.get("layout_xattr_failures", []) or []),
        }
    )
    for phase in document.get("results", []):
        phase_rows.append(
            {
                "result_kind": "inrepo",
                "workload_id": workload_id,
                "variant": variant,
                "operation": str(phase.get("operation", "")),
                "runs": 1,
                "ops_per_sec": float(phase.get("ops_per_sec") or 0.0),
                "latency_p95_ms": float(phase.get("latency_p95_ms") or 0.0),
                "latency_p99_ms": float(phase.get("latency_p99_ms") or 0.0),
            }
        )

def write_csv(path, rows):
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

write_csv(out_dir / "run_manifest.csv", records)

groups = {}
for row in records:
    groups.setdefault((row["result_kind"], row["workload_id"], row["workload"], row["variant"]), []).append(row)

native_means = {}
oracle_means = {}
for (_kind, workload_id, _workload, variant), rows in groups.items():
    mean_ops = statistics.fmean(row["measured_ops_per_sec"] for row in rows)
    if variant == "native":
        native_means[workload_id] = mean_ops
    if variant == "oracle":
        oracle_means[workload_id] = mean_ops

summary = []
for (kind, workload_id, workload, variant), rows in sorted(groups.items()):
    ops_values = [row["measured_ops_per_sec"] for row in rows]
    p95_values = [
        float(row["run_mean_p95_ms"])
        for row in rows
        if row["run_mean_p95_ms"] != ""
    ]
    native = native_means.get(workload_id)
    oracle = oracle_means.get(workload_id)
    predicted_hot = [
        float(row["predicted_hot_dirs"])
        for row in rows
        if row["predicted_hot_dirs"] != ""
    ]
    predicted_eval_hot = [
        float(row["predicted_eval_hot_dirs"])
        for row in rows
        if row["predicted_eval_hot_dirs"] != ""
    ]
    predicted_eval_cold = [
        float(row["predicted_eval_cold_dirs"])
        for row in rows
        if row["predicted_eval_cold_dirs"] != ""
    ]
    predicted_precision = [
        float(row["predicted_hot_dir_precision"])
        for row in rows
        if row["predicted_hot_dir_precision"] != ""
    ]
    summary.append(
        {
            "result_kind": kind,
            "workload_id": workload_id,
            "workload": workload,
            "variant": variant,
            "runs": len(rows),
            "mean_ops_per_sec": round(statistics.fmean(ops_values), 6),
            "stdev_ops_per_sec": round(statistics.stdev(ops_values), 6)
            if len(ops_values) > 1
            else 0.0,
            "mean_phase_p95_ms": round(statistics.fmean(p95_values), 6)
            if p95_values
            else "",
            "speedup_vs_native": round(statistics.fmean(ops_values) / native, 6)
            if native
            else "",
            "relative_to_oracle": round(statistics.fmean(ops_values) / oracle, 6)
            if oracle
            else "",
            "mean_predicted_hot_dirs": round(statistics.fmean(predicted_hot), 6)
            if predicted_hot
            else "",
            "mean_predicted_eval_hot_dirs": round(statistics.fmean(predicted_eval_hot), 6)
            if predicted_eval_hot
            else "",
            "mean_predicted_eval_cold_dirs": round(statistics.fmean(predicted_eval_cold), 6)
            if predicted_eval_cold
            else "",
            "mean_predicted_hot_dir_precision": round(statistics.fmean(predicted_precision), 6)
            if predicted_precision
            else "",
        }
    )
write_csv(out_dir / "summary.csv", summary)

phase_groups = {}
for row in phase_rows:
    key = (row["result_kind"], row["workload_id"], row["variant"], row["operation"])
    phase_groups.setdefault(key, []).append(row)

phase_summary = []
for (kind, workload_id, variant, operation), rows in sorted(phase_groups.items()):
    p95 = [float(row["latency_p95_ms"]) for row in rows if row["latency_p95_ms"] != ""]
    p99 = [float(row["latency_p99_ms"]) for row in rows if row["latency_p99_ms"] != ""]
    phase_summary.append(
        {
            "result_kind": kind,
            "workload_id": workload_id,
            "variant": variant,
            "operation": operation,
            "runs": len(rows),
            "mean_ops_per_sec": round(
                statistics.fmean(float(row["ops_per_sec"]) for row in rows), 6
            ),
            "mean_latency_p95_ms": round(statistics.fmean(p95), 6) if p95 else "",
            "mean_latency_p99_ms": round(statistics.fmean(p99), 6) if p99 else "",
        }
    )
write_csv(out_dir / "phase_summary.csv", phase_summary)
write_csv(out_dir / "external_phase_summary.csv", [row for row in phase_summary if row["result_kind"] == "external"])

lines = [
    "# CloudLab Paper-Ready Benchmark",
    "",
    f"Completed result files: {len(records)}",
    "",
    "| Kind | Workload | Variant | Runs | Mean ops/s | Stdev | Mean p95 ms | vs native | vs oracle | Predictor dir precision |",
    "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
]
for row in summary:
    lines.append(
        "| {kind} | {workload_id} | {variant} | {runs} | {ops:.2f} | {stdev:.2f} | {p95} | {native} | {oracle} | {precision} |".format(
            kind=row["result_kind"],
            workload_id=row["workload_id"],
            variant=row["variant"],
            runs=row["runs"],
            ops=float(row["mean_ops_per_sec"]),
            stdev=float(row["stdev_ops_per_sec"]),
            p95=row["mean_phase_p95_ms"],
            native=row["speedup_vs_native"],
            oracle=row["relative_to_oracle"],
            precision=row["mean_predicted_hot_dir_precision"],
        )
    )
lines.append("")
(out_dir / "summary.md").write_text("\n".join(lines))
PY
}

write_external_sidecar() {
  local job_kind="$1"
  local workload_id="$2"
  local workload="$3"
  local repeat="$4"
  local seed="$5"
  local log="$6"
  local json="$7"
  local started="$8"
  local ended="$9"
  python3 - "$job_kind" "$workload_id" "$workload" "$repeat" "$seed" "$log" "$json" "$started" "$ended" <<'PY'
import json
import re
import statistics
import sys
from pathlib import Path

job_kind, workload_id, workload, repeat, seed, log, out_json, started, ended = sys.argv[1:]
text = Path(log).read_text(errors="replace") if Path(log).exists() else ""
phase_metrics = []

if job_kind == "external_mdtest":
    in_rates = False
    for line in text.splitlines():
        if line.startswith("SUMMARY rate"):
            in_rates = True
            continue
        if in_rates and line.startswith("SUMMARY time"):
            break
        match = re.match(r"\s*(File creation|File stat|File read|File removal|Tree creation|Tree removal)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", line)
        if match:
            phase_metrics.append(
                {"operation": match.group(1), "ops_per_sec": float(match.group(4))}
            )
elif job_kind == "external_ior":
    in_summary = False
    for line in text.splitlines():
        if line.startswith("Summary of all tests"):
            in_summary = True
            continue
        parts = line.split()
        if in_summary and len(parts) >= 8 and parts[0] in {"write", "read"}:
            try:
                phase_metrics.append(
                    {"operation": parts[0], "ops_per_sec": float(parts[7])}
                )
            except ValueError:
                pass
elif job_kind == "external_filebench":
    summary = re.search(r"IO Summary:\s+([0-9]+)\s+ops\s+([0-9.]+)\s+ops/s", text)
    if summary:
        phase_metrics.append(
            {"operation": "filebench_io_summary", "ops_per_sec": float(summary.group(2))}
        )
    for line in text.splitlines():
        match = re.match(r"(?:\s*[0-9]+(?:\.[0-9]+)?:\s*)?([A-Za-z_][A-Za-z0-9_]*)\s+([0-9]+)ops\s+([0-9.]+)ops/s", line)
        if match and match.group(1) != "Summary":
            phase_metrics.append(
                {"operation": match.group(1), "ops_per_sec": float(match.group(3))}
            )

primary = [row["ops_per_sec"] for row in phase_metrics]
ops = statistics.fmean(primary) if primary else 0.0
if job_kind == "external_filebench":
    for row in phase_metrics:
        if row["operation"] == "filebench_io_summary":
            ops = row["ops_per_sec"]
            break

Path(out_json).write_text(
    json.dumps(
        {
            "result_kind": "external",
            "job_kind": job_kind,
            "workload_id": workload_id,
            "workload": workload,
            "variant": "external",
            "repeat": int(repeat),
            "seed": int(seed),
            "log": log,
            "started_unix": float(started),
            "ended_unix": float(ended),
            "elapsed_seconds": round(float(ended) - float(started), 6),
            "metrics": {"ops_per_sec": round(ops, 6)},
            "phase_metrics": phase_metrics,
        },
        indent=2,
        sort_keys=True,
    )
)
PY
}

write_filebench_workload() {
  local workdir="$1"
  local wml="$2"
  local file_count="$3"
  local file_size="$4"
  local workers="$5"
  local runtime="$6"
  local workload="$7"
  python3 - "$workdir" "$wml" "$file_count" "$file_size" "$workers" "$runtime" "$TOOLS_DIR" "$workload" <<'PY'
from pathlib import Path
import sys

workdir, wml, file_count, file_size, workers, runtime, tools_dir, workload = sys.argv[1:]
source_name = "varmail.f" if workload == "filebench_varmail" else "fileserver.f"
source = Path(tools_dir) / "share/filebench/workloads" / source_name
text = source.read_text()
replacements = {
    "set $dir=/tmp": f"set $dir={workdir}",
    "set $nfiles=10000": f"set $nfiles={file_count}",
    "set $nfiles=1000": f"set $nfiles={file_count}",
    "set $meandirwidth=1000000": "set $meandirwidth=20",
    "set $nthreads=50": f"set $nthreads={workers}",
    "set $nthreads=16": f"set $nthreads={workers}",
    "set $runtime=60": f"set $runtime={runtime}",
    "set $iosize=1m": f"set $iosize={file_size}",
    "set $meanappendsize=16k": f"set $meanappendsize={file_size}",
    "set $filesize=cvar(type=cvar-gamma,parameters=mean:131072;gamma:1.5)": f"set $filesize={file_size}",
    "set $filesize=cvar(type=cvar-gamma,parameters=mean:16384;gamma:1.5)": f"set $filesize={file_size}",
    "run 60": f"run {runtime}",
}
for old, new in replacements.items():
    text = text.replace(old, new)
Path(wml).write_text(text)
PY
}

run_external_job() {
  local job_kind="$1"
  local workload_id="$2"
  local workload="$3"
  local repeat="$4"
  local seed="$5"
  local file_count="$6"
  local file_size="$7"
  local ops="$8"
  local workers="$9"
  local runtime="${10}"
  local stem root log cmdfile json started ended rc

  stem="${workload_id}__external__r${repeat}"
  root="$BENCH_ROOT/${stem}"
  log="$OUT_DIR/${stem}.log"
  cmdfile="$OUT_DIR/${stem}.cmd"
  json="$OUT_DIR/${stem}.external.json"

  if [[ "$RESUME" == "1" && -s "$json" ]]; then
    echo "SKIP $stem"
    return 0
  fi

  sudo rm -rf "$root"
  mkdir -p "$root"

  local command=()
  if [[ "$job_kind" == "external_mdtest" ]]; then
    local per_rank=$(( (file_count + workers - 1) / workers ))
    command=(mpirun -np "$workers" "$MDTEST" -d "$root" -n "$per_rank" -F -L -P -C -T -E -r -w "$file_size" -e "$file_size" --random-seed "$seed")
  elif [[ "$job_kind" == "external_ior" ]]; then
    command=(mpirun -np "$workers" "$IOR" -a POSIX -F -w -r -g -e -C -t "$file_size" -b "$file_size" -s "$ops" -o "$root/iorfile")
  elif [[ "$job_kind" == "external_filebench" ]]; then
    local wml="$OUT_DIR/${stem}.f"
    write_filebench_workload "$root" "$wml" "$file_count" "$file_size" "$workers" "$runtime" "$workload"
    command=(sudo "$FILEBENCH" -f "$wml")
  else
    echo "unknown external job kind: $job_kind" >&2
    return 1
  fi

  printf '%q ' "${command[@]}" > "$cmdfile"
  printf '\n' >> "$cmdfile"

  echo "START $stem $(date -Is)"
  wait_for_health
  drop_caches
  started="$(python3 - <<'PY'
import time
print(time.time())
PY
)"
  set +e
  "${command[@]}" > "$log" 2>&1 < /dev/null
  rc=$?
  set -e
  ended="$(python3 - <<'PY'
import time
print(time.time())
PY
)"
  if [[ "$rc" -ne 0 ]]; then
    echo "FAIL $stem exit=$rc" | tee "$FAILED"
    tail -80 "$log" || true
    return "$rc"
  fi
  write_external_sidecar "$job_kind" "$workload_id" "$workload" "$repeat" "$seed" "$log" "$json" "$started" "$ended"
  sudo rm -rf "$root"
  printf '%s\t%s\t%s\t%s\t%s\n' "$stem" "$workload_id" "external" "$repeat" "$seed" >> "$COMPLETED"
  echo "DONE $stem"
  summarize_results
}

run_inrepo_job() {
  local workload_id="$1"
  local workload="$2"
  local variant="$3"
  local repeat="$4"
  local seed="$5"
  local file_count="$6"
  local file_size="$7"
  local dirs="$8"
  local ops="$9"
  local workers="${10}"
  local depth="${11}"
  local branching="${12}"
  local ycsb_distribution="${13}"
  local ycsb_hot_fraction="${14}"
  local ycsb_update_fraction="${15}"
  local ycsb_hot_op_fraction="${16}"
  local ycsb_zipf_alpha="${17}"
  local stem json csv log cmdfile rc

  stem="${workload_id}__${variant}__r${repeat}"
  json="$OUT_DIR/${stem}.json"
  csv="$OUT_DIR/${stem}.csv"
  log="$OUT_DIR/${stem}.log"
  cmdfile="$OUT_DIR/${stem}.cmd"

  if [[ "$RESUME" == "1" && -s "$json" && -s "$csv" ]]; then
    echo "SKIP $stem"
    return 0
  fi

  mapfile -t storage < <(storage_args "$workload" "$variant")
  local command=(
    ./src/scripts/run_posix_bench.sh
    --suite custom
    --workload "$workload"
    --file-count "$file_count"
    --file-size "$file_size"
    --dirs "$dirs"
    --ops "$ops"
    --workers "$workers"
    --depth "$depth"
    --branching "$branching"
    --seed "$seed"
    --oracle-cold-fraction 0.90
    --oracle-cold-access-fraction 0.10
    --ycsb-distribution "${ycsb_distribution:-zipfian}"
    --ycsb-hot-fraction "${ycsb_hot_fraction:-0.20}"
    --ycsb-update-fraction "${ycsb_update_fraction:-0.20}"
    --ycsb-hot-op-fraction "${ycsb_hot_op_fraction:-0.80}"
    --ycsb-zipf-alpha "${ycsb_zipf_alpha:-0.99}"
    --segment-size 67108864
    "${storage[@]}"
  )

  printf 'BENCH_ROOT=%q BENCH_OUTPUT=%q BENCH_CSV=%q ' "$BENCH_ROOT" "$json" "$csv" > "$cmdfile"
  printf '%q ' "${command[@]}" >> "$cmdfile"
  printf '\n' >> "$cmdfile"

  echo "START $stem $(date -Is)"
  wait_for_health
  drop_caches
  set +e
  BENCH_ROOT="$BENCH_ROOT" BENCH_OUTPUT="$json" BENCH_CSV="$csv" "${command[@]}" > "$log" 2>&1 < /dev/null
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    echo "FAIL $stem exit=$rc" | tee "$FAILED"
    tail -80 "$log" || true
    return "$rc"
  fi
  printf '%s\t%s\t%s\t%s\t%s\n' "$stem" "$workload_id" "$variant" "$repeat" "$seed" >> "$COMPLETED"
  echo "DONE $stem"
  summarize_results
}

require_tools
write_fairness_note
write_jobs
shuffle_jobs
rm -f "$FAILED"
touch "$COMPLETED" "$CACHE_LOG"
disable_aslr_for_filebench

echo "Output directory: $OUT_DIR"
echo "Total jobs: $(( $(wc -l < "$JOBS") - 1 ))"
echo "Randomized job order: $SHUFFLED_JOBS"
echo "Started: $(date -Is)"

tail -n +2 "$SHUFFLED_JOBS" | while IFS=$'\t' read -r job_kind workload_id workload variant repeat seed file_count file_size dirs ops workers depth branching ycsb_distribution ycsb_hot_fraction ycsb_update_fraction ycsb_hot_op_fraction ycsb_zipf_alpha runtime; do
  if [[ "$job_kind" == "inrepo" ]]; then
    run_inrepo_job "$workload_id" "$workload" "$variant" "$repeat" "$seed" "$file_count" "$file_size" "$dirs" "$ops" "$workers" "$depth" "$branching" "$ycsb_distribution" "$ycsb_hot_fraction" "$ycsb_update_fraction" "$ycsb_hot_op_fraction" "$ycsb_zipf_alpha"
  else
    run_external_job "$job_kind" "$workload_id" "$workload" "$repeat" "$seed" "$file_count" "$file_size" "$ops" "$workers" "$runtime"
  fi
  sleep "$SLEEP_BETWEEN_RUNS"
done

summarize_results
echo "Finished: $(date -Is)"
echo "Summary: $OUT_DIR/summary.md"
