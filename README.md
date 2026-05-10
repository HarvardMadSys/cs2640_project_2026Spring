<<<<<<< HEAD
# cs2640_project_2026Spring

## Projects

- [Howard Huang: Bridging the Semantic Gap: An Evaluation of ZNS and FDP in Cloud Environments](HowardHuang/README.md)
- [Xuanlin Jiang: Erasure Codes for Distributed Storage: Implementation and Trace-Driven Evaluation](xuanlinjiang/README.md)
- [Aengus McGuinness: Evaluating RDMA for Distributed Cache Reads and Metadata Updates](AengusMcGuinness/README.md)
- [Yunjia Zheng: Versioning Practice in Vector Databases](yunjia_zheng/src/README.md)
- [Yiyu Liu: Computation-Aware Caching for KV Cache](LauYeeYu/README.md)
- [Kitty Wang: SSTable Striping and Proactive Reclaim: Optimizations for ZNS-Aware RocksDB](kit-wang/README.md)
- [Jonathan Wu: FS3: FaSt File Systems, For Sure](jdabtieu/src/README.md)
- [Warren Zhu: Cache-Informed Prompting for RLMs](WarrenZhu050413/README.md)
- [Milad Razeghi: Final Version: Agents and Hyperagenting](miladrzh/README.md)
- [Mira Yu: Cache Eviction on Congressional & Court APIs: Workload Shape and Attribution Decide the Policy](mirabor/README.md)
- [Spenser Sun: When Do Sketches Beat Exact SQL? Benchmarking Apache DataSketches in Spark SQL](Spenserrrr/README.md)
- [Aadit Saluja: THE NEW META: Metadata Management for CephFS with Cold Packing and Online Hot-Directory Learning](AaditSaluja/README.md)
- [Waseem Ahmad: Workload-Aware KV-Cache Management for LLM Inference](waseemahmad1/README.md)
- [Vlad Cainamisir: AdaptiveCache: A Lifetime-Cost Study of Context Management for LLM Agents under Prefix-Cache-Aware Pricing](cainamisir/README.md)
- [Nicholas Yang: Mini Replicated KV Store](Nickanda/README.md)
- [Cole Harten: AsyncMux: Event-Driven Coordination for Multiplexed Tiered Storage](ColeHarten/README.md)
- [Minkai Li: Easy Learned Caching: An Honest Look at Augmenting S3-FIFO with a Learned Promotion Gate](Minkai25/README.md)
=======
# SAS-DB

Prototype in-memory key-value store that loads its clients as shared libraries
into a single flat address space. See `report/` for the write-up.

## Layout

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
- `run_benchmarks.py`: sweep driver; writes JSON to `results/` and emits
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

### `report/`, `presentation/`, `results/`

LaTeX paper, slides, and benchmark output (JSONs + PNG plots written by
`run_benchmarks.py` / `run_ycsb.py`).

## Build & test

```sh
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
>>>>>>> df25535c (updated readme)
