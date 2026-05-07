#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="report/results/cloudlab-comprehensive-$(date +%Y%m%d-%H%M%S)"
BENCH_ROOT="/mnt/cephfs/cs2640-bench"
REPEATS=3
SEED_BASE=7000
ORDER_SEED=26400506
HEALTH_TIMEOUT=180
SLEEP_BETWEEN_RUNS=5
RESUME=1

usage() {
  cat <<'EOF'
Usage: schedule_cloudlab_comprehensive_bench.sh [options]

Runs a serial, randomized CloudLab benchmark matrix for native, oracle, and
predictive cold-packing storage. Results are resumable and summarized after
each completed job.

Options:
  --out-dir PATH          Output directory under the repo.
  --root PATH             CephFS benchmark root. Default: /mnt/cephfs/cs2640-bench
  --repeats N             Repeats per workload/variant. Default: 3
  --seed-base N           First repeat seed. Default: 7000
  --order-seed N          Randomization seed for run order. Default: 26400506
  --health-timeout SEC    Time to wait for HEALTH_OK before each run.
  --sleep SEC             Sleep between runs. Default: 5
  --no-resume             Re-run even if JSON/CSV already exist.
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

mkdir -p "$OUT_DIR"
JOBS="$OUT_DIR/jobs.tsv"
SHUFFLED_JOBS="$OUT_DIR/jobs_shuffled.tsv"
COMPLETED="$OUT_DIR/completed.tsv"
FAILED="$OUT_DIR/FAILED"

write_fairness_note() {
  cat > "$OUT_DIR/FAIRNESS.md" <<'EOF'
# Fairness Notes

This run compares three storage policies serially on the same CephFS mount:

- `native`: direct CephFS POSIX files.
- `oracle`: oracle cold-packing. For `oracle_hotcold_mix`, oracle sees the
  benchmark-defined `hot*` directories and is an upper bound. For generic
  representative workloads that do not define production-visible hot/cold
  labels, oracle is run as `oracle_allcold`: it receives no hidden future-access
  labels and packs all logical files.
- `predictor`: non-oracle directory-hotset lazy predictor. It does not inspect
  `hot*` or `cold*` labels for placement. It observes only online read events,
  learns hot parent directories after 8 read events and 3 distinct paths, makes
  future creates under learned-hot directories native, and does not rewrite
  existing packed files during measured read/stat operations.

Controls:

- Same workload parameters, seed, file count, file size, directory count,
  operation count, worker count, mount, benchmark root, and cleanup behavior for
  all variants in a workload/repeat cell.
- Serial execution only; no benchmark jobs run concurrently.
- Global job order is randomized with a recorded seed.
- `ceph -s` must report `HEALTH_OK` before each run.
- Each run writes a unique workload root through the benchmark runner.
- Raw JSON, CSV, command line, stdout/stderr log, run manifest, phase summary,
  and aggregate summaries are retained in this directory.

Residual caveats:

- The external-source workloads are recreated in-repo shapes, not direct
  executions of the upstream tools/traces. `mdtest_tree` mirrors IOR/mdtest
  metadata phases, `filebench_varmail_like` mirrors a mail-style Filebench
  workload, `hotdirs_zipf` mirrors skewed Zipf/hotspot access, and the hot/cold
  configs serve as trace-like locality stress tests.
- The runner does not drop kernel/Ceph caches between runs. Randomized order,
  unique roots, and repeated trials reduce this bias but do not eliminate it.
EOF
}

write_jobs() {
  : > "$JOBS"
  printf 'workload_id\tworkload\tvariant\trepeat\tseed\tfile_count\tfile_size\tdirs\tops\tworkers\tdepth\tbranching\tcold_fraction\tcold_access_fraction\n' > "$JOBS"
  local repeat seed variant

  for ((repeat = 0; repeat < REPEATS; repeat++)); do
    seed=$((SEED_BASE + repeat))
    for variant in native oracle predictor; do
      printf 'ior_mdtest_tree\tmdtest_tree\t%s\t%d\t%d\t3000\t0\t64\t6000\t8\t3\t8\t0.90\t0.10\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'filebench_varmail_like\tfilebench_varmail_like\t%s\t%d\t%d\t3000\t4096\t64\t6000\t1\t2\t8\t0.90\t0.10\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'ycsb_zipf_hotdirs\thotdirs_zipf\t%s\t%d\t%d\t3000\t4096\t64\t6000\t8\t2\t8\t0.90\t0.10\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'hotcold_cold70_access10\toracle_hotcold_mix\t%s\t%d\t%d\t3000\t4096\t64\t12000\t8\t2\t8\t0.70\t0.10\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'hotcold_cold90_access10\toracle_hotcold_mix\t%s\t%d\t%d\t3000\t4096\t64\t12000\t8\t2\t8\t0.90\t0.10\n' "$variant" "$repeat" "$seed" >> "$JOBS"
      printf 'hotcold_cold90_access20\toracle_hotcold_mix\t%s\t%d\t%d\t3000\t4096\t64\t12000\t8\t2\t8\t0.90\t0.20\n' "$variant" "$repeat" "$seed" >> "$JOBS"
    done
  done
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

storage_args() {
  local workload_id="$1"
  local workload="$2"
  local variant="$3"
  local hot_prefix="__no_oracle_hot_prefix__"

  case "$variant" in
    native)
      printf '%s\n' --storage native
      ;;
    oracle)
      if [[ "$workload" == "oracle_hotcold_mix" ]]; then
        hot_prefix="hot"
      elif [[ "$workload_id" == "ycsb_zipf_hotdirs" ]]; then
        hot_prefix="dir0000"
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
import statistics
import sys
from pathlib import Path

out_dir = Path(sys.argv[1])
records = []
for path in sorted(out_dir.glob("*.json")):
    if path.name.startswith("."):
        continue
    try:
        document = json.loads(path.read_text())
    except Exception:
        continue
    args = document.get("args", {})
    stem = path.stem
    parts = stem.split("__")
    workload_id = parts[0] if len(parts) >= 3 else args.get("workload", "")
    variant = parts[1] if len(parts) >= 3 else args.get("storage", "")
    repeat = parts[2][1:] if len(parts) >= 3 and parts[2].startswith("r") else ""
    storage = document.get("storage_metrics", {})
    derived = document.get("derived_metrics", {})
    phase_p95 = [
        float(row.get("latency_p95_ms", 0.0))
        for row in document.get("results", [])
    ]
    records.append(
        {
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
            "namespace_entries": derived.get("physical_namespace_entries_estimate", ""),
            "packed_create_fraction": derived.get("packed_create_fraction", ""),
            "total_promotions": storage.get("total_promotions", 0),
            "promoted_hot": storage.get("total_promoted_eval_hot_paths", 0),
            "promoted_cold": storage.get("total_promoted_eval_cold_paths", 0),
            "predicted_hot_dirs": storage.get("predicted_hot_dirs", 0),
            "layout_failures": len(storage.get("layout_xattr_failures", []) or []),
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
    groups.setdefault((row["workload_id"], row["workload"], row["variant"]), []).append(row)

summary = []
native_means = {}
oracle_means = {}
for (workload_id, _workload, variant), rows in groups.items():
    mean_ops = statistics.fmean(row["measured_ops_per_sec"] for row in rows)
    if variant == "native":
        native_means[workload_id] = mean_ops
    if variant == "oracle":
        oracle_means[workload_id] = mean_ops

for (workload_id, workload, variant), rows in sorted(groups.items()):
    ops_values = [row["measured_ops_per_sec"] for row in rows]
    p95_values = [row["run_mean_p95_ms"] for row in rows]
    native = native_means.get(workload_id)
    oracle = oracle_means.get(workload_id)
    summary.append(
        {
            "workload_id": workload_id,
            "workload": workload,
            "variant": variant,
            "runs": len(rows),
            "mean_ops_per_sec": round(statistics.fmean(ops_values), 6),
            "stdev_ops_per_sec": round(statistics.stdev(ops_values), 6)
            if len(ops_values) > 1
            else 0.0,
            "mean_phase_p95_ms": round(statistics.fmean(p95_values), 6),
            "speedup_vs_native": round(statistics.fmean(ops_values) / native, 6)
            if native
            else "",
            "relative_to_oracle": round(statistics.fmean(ops_values) / oracle, 6)
            if oracle
            else "",
            "mean_namespace_entries": round(
                statistics.fmean(
                    float(row["namespace_entries"])
                    for row in rows
                    if row["namespace_entries"] != ""
                ),
                6,
            )
            if any(row["namespace_entries"] != "" for row in rows)
            else "",
            "mean_promotions": round(
                statistics.fmean(float(row["total_promotions"]) for row in rows), 6
            ),
            "mean_predicted_hot_dirs": round(
                statistics.fmean(float(row["predicted_hot_dirs"]) for row in rows), 6
            ),
            "failure_runs": sum(1 for row in rows if int(row["layout_failures"] or 0) > 0),
        }
    )
write_csv(out_dir / "summary.csv", summary)

phase_groups = {}
for path in sorted(out_dir.glob("*.json")):
    try:
        document = json.loads(path.read_text())
    except Exception:
        continue
    parts = path.stem.split("__")
    if len(parts) < 3:
        continue
    workload_id, variant = parts[0], parts[1]
    for phase in document.get("results", []):
        key = (workload_id, variant, str(phase.get("operation", "")))
        phase_groups.setdefault(key, []).append(phase)

phase_rows = []
for (workload_id, variant, operation), rows in sorted(phase_groups.items()):
    phase_rows.append(
        {
            "workload_id": workload_id,
            "variant": variant,
            "operation": operation,
            "runs": len(rows),
            "mean_ops_per_sec": round(
                statistics.fmean(float(row["ops_per_sec"]) for row in rows), 6
            ),
            "mean_latency_p95_ms": round(
                statistics.fmean(float(row["latency_p95_ms"]) for row in rows), 6
            ),
            "mean_latency_p99_ms": round(
                statistics.fmean(float(row["latency_p99_ms"]) for row in rows), 6
            ),
        }
    )
write_csv(out_dir / "phase_summary.csv", phase_rows)

lines = [
    "# CloudLab Comprehensive Benchmark",
    "",
    f"Completed JSON runs: {len(records)}",
    "",
    "| Workload | Variant | Runs | Mean ops/s | Stdev | Mean phase p95 ms | vs native | vs oracle |",
    "|---|---|---:|---:|---:|---:|---:|---:|",
]
for row in summary:
    lines.append(
        "| {workload_id} | {variant} | {runs} | {ops:.2f} | {stdev:.2f} | {p95:.3f} | {native} | {oracle} |".format(
            workload_id=row["workload_id"],
            variant=row["variant"],
            runs=row["runs"],
            ops=float(row["mean_ops_per_sec"]),
            stdev=float(row["stdev_ops_per_sec"]),
            p95=float(row["mean_phase_p95_ms"]),
            native=row["speedup_vs_native"],
            oracle=row["relative_to_oracle"],
        )
    )
lines.append("")
(out_dir / "summary.md").write_text("\n".join(lines))
PY
}

run_job() {
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
  local cold_fraction="${13}"
  local cold_access_fraction="${14}"
  local stem json csv log cmdfile
  shift 14

  stem="${workload_id}__${variant}__r${repeat}"
  json="$OUT_DIR/${stem}.json"
  csv="$OUT_DIR/${stem}.csv"
  log="$OUT_DIR/${stem}.log"
  cmdfile="$OUT_DIR/${stem}.cmd"

  if [[ "$RESUME" == "1" && -s "$json" && -s "$csv" ]]; then
    echo "SKIP $stem"
    return 0
  fi

  mapfile -t storage < <(storage_args "$workload_id" "$workload" "$variant")
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
    --oracle-cold-fraction "$cold_fraction"
    --oracle-cold-access-fraction "$cold_access_fraction"
    --segment-size 67108864
    "${storage[@]}"
  )

  printf 'BENCH_ROOT=%q BENCH_OUTPUT=%q BENCH_CSV=%q ' "$BENCH_ROOT" "$json" "$csv" > "$cmdfile"
  printf '%q ' "${command[@]}" >> "$cmdfile"
  printf '\n' >> "$cmdfile"

  echo "START $stem $(date -Is)"
  wait_for_health
  local started ended rc
  started="$(date +%s)"
  set +e
  BENCH_ROOT="$BENCH_ROOT" BENCH_OUTPUT="$json" BENCH_CSV="$csv" "${command[@]}" > "$log" 2>&1
  rc=$?
  set -e
  ended="$(date +%s)"
  if [[ "$rc" -ne 0 ]]; then
    echo "FAIL $stem exit=$rc" | tee "$FAILED"
    tail -80 "$log" || true
    return "$rc"
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$stem" "$workload_id" "$variant" "$repeat" "$seed" "$((ended - started))" >> "$COMPLETED"
  echo "DONE $stem ${ended}s"
  summarize_results
}

write_fairness_note
write_jobs
shuffle_jobs
touch "$COMPLETED"

echo "Output directory: $OUT_DIR"
echo "Total jobs: $(( $(wc -l < "$JOBS") - 1 ))"
echo "Randomized job order: $SHUFFLED_JOBS"
echo "Started: $(date -Is)"

tail -n +2 "$SHUFFLED_JOBS" | while IFS=$'\t' read -r workload_id workload variant repeat seed file_count file_size dirs ops workers depth branching cold_fraction cold_access_fraction; do
  run_job "$workload_id" "$workload" "$variant" "$repeat" "$seed" "$file_count" "$file_size" "$dirs" "$ops" "$workers" "$depth" "$branching" "$cold_fraction" "$cold_access_fraction"
  sleep "$SLEEP_BETWEEN_RUNS"
done

summarize_results
echo "Finished: $(date -Is)"
echo "Summary: $OUT_DIR/summary.md"
