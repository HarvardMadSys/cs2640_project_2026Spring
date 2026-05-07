# Multi-tenant Vector Database Experiments

Local Milvus standalone benchmarks supporting a study of how a vector database lays out near-identical data across collections and how concurrent tenants interact through the OS page cache. Two experiment families:

- [CodeSearchNet/](CodeSearchNet/): measures whether the same logical corpus produces the same segment layout across two collections, using Jina embeddings of the VIBE CodeSearchNet dataset (768-dim, cosine).
- [wiki/](wiki/): drives concurrent tenant traffic against a partitioned collection of a shared partition plus two per-tenant private partitions, using Cohere wiki passages and TriviaQA queries (768-dim, cosine), to characterize the page-cache thrashing regime under shared storage.

## Starting Milvus

`prepare_milvus.sh` brings up the three services Milvus standalone needs (etcd, MinIO, milvus) and tracks each one with a PID file under `/scratch/yunjia/milvus-pids/`. The Milvus binary itself must already be built at `/scratch/yunjia/milvus/bin/milvus`.

```
# First time on a fresh box: install etcd + MinIO and start everything
bash /scratch/yunjia/milvus_experiments/setup/prepare_milvus.sh

# Subsequent runs (already installed): just start
bash /scratch/yunjia/milvus_experiments/setup/prepare_milvus.sh start
```

The script is idempotent. If a service is already running (PID file present and the PID is alive), it logs `already running` and moves on instead of double-starting it.

After it finishes you should see:

```
Services running:
  etcd   : http://localhost:2379     (log: /scratch/yunjia/milvus-pids/etcd.log)
  MinIO  : http://localhost:9000     (log: /scratch/yunjia/milvus-pids/minio.log)
  Milvus : http://localhost:19530    (log: /scratch/yunjia/milvus-pids/milvus.log)
```

The script polls `http://localhost:19530/v1/vector/collections` for up to 30 seconds and reports `Milvus is ready` once it responds. If you see the `did not respond after 30s` warning, check `milvus.log` first, etcd/MinIO logs second.

### Health check

```
curl -s http://localhost:19530/v1/vector/collections
```

A JSON response (even an empty list) means Milvus is up.

## Stopping Milvus

```
bash /scratch/yunjia/milvus_experiments/setup/prepare_milvus.sh stop
```

This sends `SIGTERM` to the PIDs in `milvus.pid`, `minio.pid`, `etcd.pid` (in that order) and removes the PID files. Data on disk is preserved, so the next `start` will see the same collections.

If you ever need to confirm or force-kill manually:

```
ps -fp $(cat /scratch/yunjia/milvus-pids/milvus.pid 2>/dev/null) 2>/dev/null
ps -fp $(cat /scratch/yunjia/milvus-pids/etcd.pid   2>/dev/null) 2>/dev/null
ps -fp $(cat /scratch/yunjia/milvus-pids/minio.pid  2>/dev/null) 2>/dev/null
```

## Workflows

Both experiments follow the same three phases: **prepare** (download + materialize `.npy` files under `data/`), **insert** (create the collection, build the HNSW index, push vectors in), and **query** (load the collection and run an ANN recall benchmark against ground truth). Steps assume Milvus is already running, see the section above.

### CodeSearchNet (768-dim, cosine)

The corpus is the VIBE CodeSearchNet HDF5 (Jina embeddings, 768-dim, cosine metric). Insert and query happen in one collection without partitions.

```
cd /scratch/yunjia/milvus_experiments/CodeSearchNet

# 1. Prepare: download VIBE HDF5 and write data/CodeSearchNet_dataset_original.npy,
#    CodeSearchNet_queries_original.npy, CodeSearchNet_neighbors_original.npy,
#    CodeSearchNet_distances_original.npy. Also recomputes ground truth against
#    the full corpus as a sanity check.
python prepare_from_VIBE.py

# Optional trimmed variant: drop the first 2% of the corpus and recompute GT.
python prepare_from_VIBE.py --skip 0.02

# 2. Insert + query: create collection codesearchnet_original, insert all
#    vectors in batches of 10,000, build HNSW (M=16, efC=200), load, then
#    benchmark Recall@100 with ef=200.
python load_to_milvus.py --variant original

# Trimmed variant lands in its own collection.
python load_to_milvus.py --variant trimmed2

# 3. Re-query only (no re-insert) at a different ef to sweep recall vs QPS.
python load_to_milvus.py --variant original --skip-insert --ef 400
```

`load_to_milvus.py` derives the collection name as `codesearchnet_<variant>` unless you pass `--collection`. The Recall@100 number it prints is computed against the VIBE-provided neighbors for the `original` variant, and against the locally recomputed GT for trimmed variants.

### Wikipedia (768-dim, cosine, multi-tenant partitions)

The corpus is the Cohere wiki dump (`Cohere/wikipedia-2023-11-embed-multilingual-v3`, en config) and TriviaQA questions are used as queries. [prepare_wiki_partitions.py](wiki/prepare_wiki_partitions.py) embeds both with a sentence-transformer on GPU, runs brute-force cosine KNN for ground truth, then builds three partitions: a `shared_partition` that captures most of each query's neighbors and two private partitions `partition_A` / `partition_B` that hold the remainder. Tenant A queries `shared + A`, tenant B queries `shared + B`. [bench_tenants.py](wiki/bench_tenants.py) then drives one or both tenants against the resulting collection.

```
cd /scratch/yunjia/milvus_deliverables/wiki

# 1. Prepare: stream wiki passages, embed with BGE on GPU, run exact KNN
#    sharded across the listed devices, greedily grow shared_knn until the
#    coverage target is met, scatter the rest into shared/A/B. Outputs land
#    under wiki/info/. Re-run with --reuse to skip cached stages.
python prepare_wiki_partitions.py \
    --num-wiki 1000000 --num-queries 2000 \
    --model BAAI/bge-base-en-v1.5 \
    --devices cuda:0,cuda:2 --reuse

# 2. Load the partitioned dataset into a Milvus collection.
python load_to_milvus.py --collection wiki_QC02_SS02_nocompact

# 3. Benchmark one tenant (B) for 120s with 16 concurrent in-flight searches
#    and a 20s warm-up that brings HNSW pages into the page cache before the
#    measurement window opens.
python bench_tenants.py --collection wiki_QC02_SS02_nocompact \
    --warm 20 --duration 120 --tenant B --request-per-tenant 16

# Both tenants concurrent (omit --tenant). Each tenant spawns
# --request-per-tenant worker threads, so total in-flight searches
# = 2 * --request-per-tenant.
python bench_tenants.py --collection wiki_QC02_SS02_nocompact \
    --warm 20 --duration 120 --request-per-tenant 8
```

`bench_tenants.py` releases every other loaded collection on the Milvus instance before running so the OS page cache is dedicated to the variant under test. It optionally drops the page cache (`--reset-cache`) before warm-up to start cold. Per-query latency, hits, recall@k, and rolling throughput land in CSVs under `wiki/bench_result/`. The info directory is auto-resolved from the collection name by stripping trailing underscore-suffixes, so `wiki_QC02_SS02_nocompact` resolves to `wiki_QC02_SS02_info/` if it exists; pass `--info-dir` to override.

## Dropping a collection

Each loader script under `CodeSearchNet/` and `SIFT/` exposes the same two flags for collection management. They both talk to Milvus over the standard `MilvusClient` API, so it does not matter which one you use, only whether the collection name exists.

List what is currently in Milvus:

```
python /scratch/yunjia/milvus_experiments/SIFT/load_to_milvus.py --list
# or
python /scratch/yunjia/milvus_experiments/CodeSearchNet/load_to_milvus.py --list
```

Drop a specific collection by name:

```
python /scratch/yunjia/milvus_experiments/SIFT/load_to_milvus.py --drop sift100m
python /scratch/yunjia/milvus_experiments/CodeSearchNet/load_to_milvus.py --drop codesearchnet_original
```

Both commands exit immediately after dropping, they do not insert or run a benchmark. Dropping a collection also drops every partition inside it.

If you want a clean slate without listing first, you can also re-run the loader without `--skip-insert`. The `create_collection` step inside both loaders detects an existing collection and drops it before recreating, so

```
python /scratch/yunjia/milvus_experiments/SIFT/load_to_milvus.py
```

will overwrite `sift100m` end-to-end.

## Wiping all Milvus state

Dropping collections leaves etcd and MinIO state behind. To fully reset Milvus (collections, indexes, segments, WAL), stop the services and delete the data dirs:

```
bash /scratch/yunjia/milvus_experiments/setup/prepare_milvus.sh stop
rm -rf /scratch/yunjia/milvus-data /scratch/yunjia/etcd-data /scratch/yunjia/minio-data
bash /scratch/yunjia/milvus_experiments/setup/prepare_milvus.sh start
```

This is destructive: every collection across every experiment will be gone. Only do it when you intentionally want a fresh Milvus.
