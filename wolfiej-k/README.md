# SAS-DB

Prototype in-memory key-value store that loads its clients as shared libraries
into a single flat address space. See `report/` for the write-up.

## Layout

The repo is organized as:

- `src/`: all C++ source, build files, and benchmarks.
- `report/`: LaTeX paper.
- `presentation/`: slides.
- `results/`: benchmark output (JSON + PNG plots written by the scripts in
  `src/benchmarks/`).

Everything below is rooted at `src/`.

### Top-level Source

- `client.h`: single-header client API (`sas::put`, `sas::get`, `sas::deref`,
`sas::close`, plus the C++ RAII handle wrappers).
- `host.cpp`: host binary; `dlopen`s each client `.so` and calls its
`entry(cid)` from a dedicated thread.
- `host_runtime.h`, `host_drivers.h`: host-side glue: backend selection and the
symbols that `client.h` resolves at runtime.
- `handle.{h,cpp}`: reference-counted handle type returned by `get`.
- `tagged_ptr.h`: pointer + tag word used for ABA-safe CAS.
- `memory_pool.h`: thread-local pool used to recycle handle allocations.

### `hash_tables/`

The five backends benchmarked in the report. Backend is selected at build time
to avoid runtime overhead.

- `hash_table.h`, `common.h`, `slot_table.h`: shared definitions.
- `spinlock.h`: `std::unordered_map` behind a single spinlock (baseline).
- `sharded.h`: striped `boost::concurrent_flat_map`.
- `ebr.{h,cpp}`, `ebr_store.{h,cpp}`: chained lock-free table with epoch-based
reclamation.
- `hazard.{h,cpp}`, `hp_store.{h,cpp}`: chained lock-free table with hazard
pointers.
- `hybrid.h`: sharded-style readers-writer striping that performs pointer CAS
under a *read* lock; SAS-DB's default backend.

### `benchmarks/`

- `benchmark.h`, `workload.h`, `arch_workload.h`, `timing.h`, `memory.h`: Google
  Benchmark, latency histogram, RDTSC timing, etc.
- `compare_{spinlock,sharded,ebr,hp,hybrid}.cpp`: per-backend microbenchmarks.
- `compare_shm.cpp`, `sas.cpp`: SAS-vs-SHM architecture comparison.
- `end_to_end.cpp`: end-to-end SAS-DB stack on top of the chosen backend.
- `run_benchmarks.py`: sweep driver; writes JSON to `../results/` and emits
matplotlib plots.
- `run_ycsb.py`: YCSB driver; runs SAS and Lightning across workloads and thread
counts.
- `ycsb/`: Java side of the YCSB integration:
`SasClient.java`/`LightningClient.java` (YCSB DB bindings), `sas_jni.cpp` (JNI
bridge into the host), `YcsbDriver.java` (entry point), `RecordCodec.java`.

### `tests/`

CTest-driven correctness tests. Single-threaded coverage in `basic.cpp`,
`advanced.cpp`, `lifetime.cpp`, `ref.cpp`, `dtor.cpp`. Concurrency and
reclamation in `concurrent.cpp`, `churn.cpp`, `stress.cpp`, `gc.cpp`,
`publish_poll.cpp`, `disjoint.cpp`, `resize.cpp`. Run on every backend
(currently failing two tests for EBR, not believed to be a correctness issue.)

### `example/`

Minimal client `.so`s loaded by `host`: `hello.cpp` and `world.cpp` (toy
two-client demo), `bench.cpp` (used for quick benchmarking).

### `external/`

Third party headers: `xxhash.h`, `zipfian_int_distribution.h`. `setup.sh`
additionally fetches YCSB and Lightning into `external/{ycsb,lightning}` at
install time.

## Build & test

All build targets are run from `src/`.

```sh
cd src
./setup.sh           # one-time setup
make build           # compile to build/
make test            # ctest
make SAN=1           # compile with sanitizers
```

Microbenchmarks and YCSB:

```sh
.venv/bin/python benchmarks/run_benchmarks.py
.venv/bin/python benchmarks/run_ycsb.py
make ycsb-bench YCSB_STORE=sas YCSB_WORKLOAD=workloada YCSB_THREADS=8
```
