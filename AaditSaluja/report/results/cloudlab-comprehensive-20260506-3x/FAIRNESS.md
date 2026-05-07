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
