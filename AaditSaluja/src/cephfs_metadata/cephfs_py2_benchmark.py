"""Python 2 compatible libcephfs benchmark runner for the Nautilus container.

The local Docker image exposes the CephFS Python binding only to Python 2. This
module mirrors the main benchmark runner's output shape for local development.
"""

from __future__ import print_function

import argparse
import csv
import json
import math
import os
import random
import socket
import subprocess
import sys
import time
import uuid

import cephfs


class NoopPolicy(object):
    name = "none"

    def on_dirs_created(self, client, dirs):
        return None

    def before_operation(self, client, operation, path):
        return None

    def events(self):
        return []


class StaticSubtreePinningPolicy(object):
    name = "static"

    def __init__(self, pin_ranks):
        self.pin_ranks = pin_ranks
        self._events = []

    def on_dirs_created(self, client, dirs):
        if not self.pin_ranks:
            return
        for index, directory in enumerate(dirs):
            rank = self.pin_ranks[index % len(self.pin_ranks)]
            try:
                client.set_pin(directory, rank)
            except Exception as exc:
                self._record("pin_failed", directory, rank, str(exc))
            else:
                self._record("pin", directory, rank, "round_robin_static_subtree_pin")

    def before_operation(self, client, operation, path):
        return None

    def events(self):
        return list(self._events)

    def _record(self, action, path, rank, reason):
        self._events.append(
            {
                "sequence": len(self._events) + 1,
                "policy": self.name,
                "action": action,
                "path": path,
                "rank": rank,
                "reason": reason,
            }
        )


class PredictiveHotDirectoryPolicy(object):
    name = "predictive"

    def __init__(self, pin_ranks, hot_window, hot_threshold, hot_min_interval):
        self.pin_ranks = pin_ranks
        self.hot_window = max(1, hot_window)
        self.hot_threshold = max(1, hot_threshold)
        self.hot_min_interval = max(1, hot_min_interval)
        self.recent_dirs = []
        self.counts = {}
        self.pinned = {}
        self.last_pin_sequence = {}
        self.sequence = 0
        self.next_rank_index = 0
        self._events = []

    def on_dirs_created(self, client, dirs):
        return None

    def before_operation(self, client, operation, path):
        if not self.pin_ranks:
            return
        directory = parent_dir(path)
        self.sequence += 1
        self.recent_dirs.append(directory)
        self.counts[directory] = self.counts.get(directory, 0) + 1
        if len(self.recent_dirs) > self.hot_window:
            expired = self.recent_dirs.pop(0)
            self.counts[expired] = self.counts.get(expired, 0) - 1
            if self.counts[expired] <= 0:
                del self.counts[expired]
        should_pin = (
            self.counts.get(directory, 0) >= self.hot_threshold
            and directory not in self.pinned
            and self.sequence - self.last_pin_sequence.get(directory, 0)
            >= self.hot_min_interval
        )
        if not should_pin:
            return
        rank = self.pin_ranks[self.next_rank_index % len(self.pin_ranks)]
        self.next_rank_index += 1
        self.pinned[directory] = rank
        self.last_pin_sequence[directory] = self.sequence
        try:
            client.set_pin(directory, rank)
        except Exception as exc:
            self._events.append(
                {
                    "sequence": self.sequence,
                    "policy": self.name,
                    "action": "pin_failed",
                    "path": directory,
                    "rank": rank,
                    "reason": str(exc),
                }
            )
            return
        self._events.append(
            {
                "sequence": self.sequence,
                "policy": self.name,
                "action": "pin",
                "path": directory,
                "rank": rank,
                "reason": "hot_directory count=%d window=%d threshold=%d trigger_op=%s"
                % (
                    self.counts.get(directory, 0),
                    self.hot_window,
                    self.hot_threshold,
                    operation,
                ),
            }
        )

    def events(self):
        return list(self._events)


def parent_dir(path):
    if "/" not in path.rstrip("/"):
        return "/"
    parent = path.rstrip("/").rsplit("/", 1)[0]
    return parent if parent else "/"


def build_policy(args):
    if args.policy == "none":
        return NoopPolicy()
    if args.policy == "static":
        return StaticSubtreePinningPolicy(args.pin_ranks)
    if args.policy == "predictive":
        return PredictiveHotDirectoryPolicy(
            args.pin_ranks,
            args.hot_window,
            args.hot_threshold,
            args.hot_min_interval,
        )
    raise ValueError("unknown policy: %s" % args.policy)


def payload(size):
    if size <= 0:
        return b""
    pattern = b"cs2640-cephfs-metadata-benchmark\n"
    repeats = (size // len(pattern)) + 1
    return (pattern * repeats)[:size]


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * (pct / 100.0)
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[int(index)]
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean(values):
    if not values:
        return 0.0
    return sum(values) / float(len(values))


class CephFsClient(object):
    def __init__(self, conffile):
        self.fs = cephfs.LibCephFS(conffile=conffile)
        self.fs.mount()

    def close(self):
        self.fs.shutdown()

    def mkdirs(self, path):
        self.fs.mkdirs(path, 0o755)

    def rmdir(self, path):
        self.fs.rmdir(path)

    def write_file(self, path, data):
        fd = self.fs.open(path, "w", 0o644)
        try:
            if data:
                self.fs.write(fd, data, 0)
        finally:
            self.fs.close(fd)

    def read_file(self, path, size):
        fd = self.fs.open(path, "r", 0)
        try:
            return self.fs.read(fd, 0, size)
        finally:
            self.fs.close(fd)

    def stat(self, path):
        return self.fs.stat(path)

    def unlink(self, path):
        self.fs.unlink(path)

    def set_pin(self, path, rank):
        self.fs.setxattr(path, "ceph.dir.pin", str(rank).encode("ascii"), 0)


def timed_phase(name, operation, client, policy, items, func):
    latencies = []
    phase_start = time.time()
    for item in items:
        start = time.time()
        policy.before_operation(client, operation, item)
        func(client, item)
        latencies.append((time.time() - start) * 1000.0)
    total = time.time() - phase_start
    return {
        "phase": name,
        "operation": operation,
        "count": len(items),
        "total_seconds": round(total, 6),
        "ops_per_sec": round(len(items) / total, 3) if total > 0 else 0,
        "latency_mean_ms": round(mean(latencies), 6),
        "latency_p50_ms": round(percentile(latencies, 50), 6),
        "latency_p95_ms": round(percentile(latencies, 95), 6),
        "latency_p99_ms": round(percentile(latencies, 99), 6),
    }


def mdtest_dirs(root, depth, branching):
    if depth <= 0:
        return [root]
    current = [root]
    all_dirs = []
    for level in range(depth):
        next_level = []
        for parent in current:
            for branch in range(branching):
                child = "%s/d%d_%d" % (parent, level, branch)
                all_dirs.append(child)
                next_level.append(child)
        current = next_level
    return all_dirs or [root]


def files_across_dirs(dirs, count, prefix):
    return [
        "%s/%s%08d.dat" % (dirs[index % len(dirs)], prefix, index)
        for index in range(count)
    ]


def create_dirs(client, dirs, policy):
    for directory in dirs:
        client.mkdirs(directory)
    policy.on_dirs_created(client, dirs)


def remove_dirs(client, dirs):
    for directory in sorted(dirs, key=lambda item: item.count("/"), reverse=True):
        try:
            client.rmdir(directory)
        except Exception:
            pass


def run_mdtest_like(client, root, name, file_count, file_size, depth, branching, policy):
    dirs = mdtest_dirs(root, depth, branching)
    files = files_across_dirs(dirs, file_count, "f")
    data = payload(file_size)
    create_dirs(client, [root] + dirs, policy)
    results = [
        timed_phase(name, "create", client, policy, files, lambda fs, path: fs.write_file(path, data)),
        timed_phase(name, "stat", client, policy, files, lambda fs, path: fs.stat(path)),
        timed_phase(name, "read", client, policy, files, lambda fs, path: fs.read_file(path, file_size)),
        timed_phase(name, "delete", client, policy, files, lambda fs, path: fs.unlink(path)),
    ]
    remove_dirs(client, dirs + [root])
    return results


def run_hotdirs(client, root, file_count, file_size, dirs_count, seed, policy):
    name = "hotdirs_zipf"
    dirs = ["%s/dir%04d" % (root, index) for index in range(dirs_count)]
    create_dirs(client, [root] + dirs, policy)
    rng = random.Random(seed)
    files = []
    for index in range(file_count):
        if rng.random() < 0.8:
            directory = dirs[0]
        else:
            directory = dirs[1 + rng.randrange(max(1, len(dirs) - 1))]
        files.append("%s/f%08d.dat" % (directory, index))
    data = payload(file_size)
    results = [
        timed_phase(name, "skewed_create", client, policy, files, lambda fs, path: fs.write_file(path, data)),
        timed_phase(name, "skewed_stat", client, policy, files, lambda fs, path: fs.stat(path)),
        timed_phase(name, "skewed_delete", client, policy, files, lambda fs, path: fs.unlink(path)),
    ]
    remove_dirs(client, dirs + [root])
    return results


def run_varmail_like(client, root, file_count, file_size, dirs_count, ops, seed, policy):
    name = "filebench_varmail_like"
    dirs = ["%s/mailbox%04d" % (root, index) for index in range(dirs_count)]
    create_dirs(client, [root] + dirs, policy)
    rng = random.Random(seed)
    data = payload(file_size)
    live = []
    create_latencies = []
    mixed_latencies = []
    delete_latencies = []
    phase_start = time.time()
    for index in range(max(1, file_count // 2)):
        path = "%s/msg%08d.eml" % (dirs[index % len(dirs)], index)
        start = time.time()
        policy.before_operation(client, "initial_create", path)
        client.write_file(path, data)
        create_latencies.append((time.time() - start) * 1000.0)
        live.append(path)
    create_total = time.time() - phase_start

    phase_start = time.time()
    next_id = len(live)
    for _ in range(ops):
        action = rng.random()
        start = time.time()
        if action < 0.45 or not live:
            directory = dirs[rng.randrange(len(dirs))]
            path = "%s/msg%08d.eml" % (directory, next_id)
            next_id += 1
            policy.before_operation(client, "mixed_create", path)
            client.write_file(path, data)
            live.append(path)
        elif action < 0.80:
            path = live[rng.randrange(len(live))]
            if rng.random() < 0.5:
                policy.before_operation(client, "mixed_stat", path)
                client.stat(path)
            else:
                policy.before_operation(client, "mixed_read", path)
                client.read_file(path, file_size)
        else:
            index = rng.randrange(len(live))
            path = live.pop(index)
            policy.before_operation(client, "mixed_delete", path)
            client.unlink(path)
        mixed_latencies.append((time.time() - start) * 1000.0)
    mixed_total = time.time() - phase_start

    phase_start = time.time()
    for path in live:
        start = time.time()
        policy.before_operation(client, "cleanup_delete", path)
        client.unlink(path)
        delete_latencies.append((time.time() - start) * 1000.0)
    delete_total = time.time() - phase_start
    remove_dirs(client, dirs + [root])
    return [
        phase_row(name, "initial_create", create_latencies, create_total),
        phase_row(name, "mixed_mail_ops", mixed_latencies, mixed_total),
        phase_row(name, "cleanup_delete", delete_latencies, delete_total),
    ]


def phase_row(name, operation, latencies, total):
    return {
        "phase": name,
        "operation": operation,
        "count": len(latencies),
        "total_seconds": round(total, 6),
        "ops_per_sec": round(len(latencies) / total, 3) if total > 0 else 0,
        "latency_mean_ms": round(mean(latencies), 6),
        "latency_p50_ms": round(percentile(latencies, 50), 6),
        "latency_p95_ms": round(percentile(latencies, 95), 6),
        "latency_p99_ms": round(percentile(latencies, 99), 6),
    }


def collect_ceph_stats():
    snapshots = {"available": True}
    for name, command in {
        "df": ["ceph", "df", "--format", "json"],
        "fs_status": ["ceph", "fs", "status", "--format", "json"],
    }.items():
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()
        if proc.returncode == 0:
            decoded = stdout.decode("utf-8")
            try:
                snapshots[name] = json.loads(decoded)
            except ValueError:
                snapshots[name] = {"raw": decoded}
        else:
            snapshots[name] = {"error": stderr.decode("utf-8").strip()}
    return snapshots


def pool_summary(stats):
    df = stats.get("df")
    if not isinstance(df, dict):
        return {}
    summary = {}
    for pool in df.get("pools", []):
        name = pool.get("name")
        pool_stats = pool.get("stats", {})
        summary[name] = {
            "stored": int(pool_stats.get("stored", 0)),
            "objects": int(pool_stats.get("objects", 0)),
            "bytes_used": int(pool_stats.get("bytes_used", 0)),
        }
    return summary


def pool_delta(before, after):
    before_summary = pool_summary(before)
    after_summary = pool_summary(after)
    delta = {}
    for name in sorted(set(before_summary.keys()) | set(after_summary.keys())):
        delta[name] = {}
        for key in ("stored", "objects", "bytes_used"):
            delta[name][key] = after_summary.get(name, {}).get(key, 0) - before_summary.get(name, {}).get(key, 0)
    return delta


def parse_pin_ranks(value):
    if not value:
        return []
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def ensure_parent(path):
    parent = os.path.dirname(path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent)


def write_outputs(output, csv_output, document, rows):
    if output == "-":
        print(json.dumps(document, indent=2, sort_keys=True))
    elif output:
        ensure_parent(output)
        with open(output, "w") as handle:
            handle.write(json.dumps(document, indent=2, sort_keys=True) + "\n")
    if csv_output:
        ensure_parent(csv_output)
        with open(csv_output, "w") as handle:
            if rows:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conffile", default="/etc/ceph/ceph.conf")
    parser.add_argument("--root", default="/cs2640-bench")
    parser.add_argument("--suite", choices=["quick", "standard", "custom"], default="quick")
    parser.add_argument("--workload", choices=["mdtest_tree", "sprite_lfs_smallfile", "filebench_varmail_like", "hotdirs_zipf"], default="mdtest_tree")
    parser.add_argument("--file-count", type=int, default=1000)
    parser.add_argument("--file-size", type=int, default=1024)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--branching", type=int, default=8)
    parser.add_argument("--dirs", type=int, default=32)
    parser.add_argument("--ops", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--pin-ranks", type=parse_pin_ranks, default=[])
    parser.add_argument("--policy", choices=["none", "static", "predictive"], default="none")
    parser.add_argument("--hot-window", type=int, default=128)
    parser.add_argument("--hot-threshold", type=int, default=64)
    parser.add_argument("--hot-min-interval", type=int, default=32)
    parser.add_argument("--output", default="/tmp/cs2640-bench-results/latest.json")
    parser.add_argument("--csv", default="/tmp/cs2640-bench-results/latest.csv")
    parser.add_argument("--no-ceph-stats", action="store_true")
    args = parser.parse_args(argv)

    run_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:8])
    root = "%s/%s" % (args.root.rstrip("/"), run_id)
    before = {} if args.no_ceph_stats else collect_ceph_stats()
    started = time.time()
    policy = build_policy(args)
    client = CephFsClient(args.conffile)
    rows = []
    try:
        if args.suite == "quick":
            rows.extend(run_mdtest_like(client, "%s/mdtest-%s" % (root, uuid.uuid4().hex[:8]), "mdtest_tree", 200, 0, 2, 4, policy))
            rows.extend(run_mdtest_like(client, "%s/sprite-%s" % (root, uuid.uuid4().hex[:8]), "sprite_lfs_smallfile", 200, 1024, 1, 8, policy))
            rows.extend(run_hotdirs(client, "%s/hotdirs-%s" % (root, uuid.uuid4().hex[:8]), 250, 512, 8, args.seed, policy))
        elif args.suite == "standard":
            rows.extend(run_mdtest_like(client, "%s/mdtest-%s" % (root, uuid.uuid4().hex[:8]), "mdtest_tree", 5000, 0, 3, 8, policy))
            rows.extend(run_mdtest_like(client, "%s/sprite-%s" % (root, uuid.uuid4().hex[:8]), "sprite_lfs_smallfile", 10000, 1024, 1, 16, policy))
            rows.extend(run_varmail_like(client, "%s/varmail-%s" % (root, uuid.uuid4().hex[:8]), 5000, 4096, 64, 10000, args.seed, policy))
            rows.extend(run_hotdirs(client, "%s/hotdirs-%s" % (root, uuid.uuid4().hex[:8]), 10000, 512, 64, args.seed, policy))
        elif args.workload == "mdtest_tree":
            rows.extend(run_mdtest_like(client, "%s/custom-%s" % (root, uuid.uuid4().hex[:8]), args.workload, args.file_count, args.file_size, args.depth, args.branching, policy))
        elif args.workload == "sprite_lfs_smallfile":
            rows.extend(run_mdtest_like(client, "%s/custom-%s" % (root, uuid.uuid4().hex[:8]), args.workload, args.file_count, args.file_size, 1, args.branching, policy))
        elif args.workload == "filebench_varmail_like":
            rows.extend(run_varmail_like(client, "%s/custom-%s" % (root, uuid.uuid4().hex[:8]), args.file_count, args.file_size, args.dirs, args.ops, args.seed, policy))
        else:
            rows.extend(run_hotdirs(client, "%s/custom-%s" % (root, uuid.uuid4().hex[:8]), args.file_count, args.file_size, args.dirs, args.seed, policy))
    finally:
        client.close()
    ended = time.time()
    after = {} if args.no_ceph_stats else collect_ceph_stats()
    for row in rows:
        row.update(
            {
                "run_id": run_id,
                "backend": "ceph-libcephfs",
                "workers": args.workers,
                "policy": policy.name,
            }
        )
    document = {
        "run_id": run_id,
        "host": socket.gethostname(),
        "backend": "ceph-libcephfs",
        "root": root,
        "started_unix": started,
        "ended_unix": ended,
        "elapsed_seconds": round(ended - started, 6),
        "args": vars(args),
        "results": rows,
        "policy_events": policy.events(),
        "ceph_stats": {
            "before": before,
            "after": after,
            "delta": pool_delta(before, after) if before and after else {},
        },
    }
    write_outputs(args.output, args.csv, document, rows)
    if args.output != "-":
        print("wrote %s" % args.output)
    if args.csv:
        print("wrote %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
