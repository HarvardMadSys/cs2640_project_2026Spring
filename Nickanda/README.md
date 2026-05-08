# cs2640-kvstore

A small **replicated key/value store** over **gRPC**, built for CS2640-style experiments comparing **consistency models**, **replication strategies**, and **performance** under faults.

## What’s included

- **Replication modes**: primary-backup style **`leader`** replication and **`quorum`** (configurable read/write quorum sizes `r` / `w`).
- **Versioning**: **Lamport logical clocks** or **vector clocks** for ordering concurrent writes.
- **Storage**: in-memory or **SQLite** backends; optional **faulty storage** wrapper (delay, stalls, fail-slow bursts) for timing experiments.
- **Anti-entropy**: optional background digest sync between peers (`--anti-entropy-interval`).
- **Observability**: per-node **metrics** (counts and latency percentiles) via RPC; clients can query **`health`**, **`metrics`**, and toggle serving state for fault injection.

Protocol definitions live in [`proto/kvstore.proto`](proto/kvstore.proto). Generated Python stubs are committed under [`src/kvstore/generated/`](src/kvstore/generated/).

## Requirements

- **Python 3.10+**
- Dependencies are pinned in [`requirements.txt`](requirements.txt) (`grpcio`, `grpcio-tools`, `pytest`, `matplotlib`).

## Setup

Create a virtual environment, install dependencies, and install the package in editable mode so `python -m kvstore.*` works from anywhere:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

If you prefer not to install the package, set `PYTHONPATH=src` when running modules manually.

## Regenerating gRPC code

After editing `proto/kvstore.proto`, regenerate the Python files:

```bash
bash scripts/gen_proto.sh
```

This runs `grpc_tools.protoc` and writes into `src/kvstore/generated/`.

## Tests

From the repository root (pytest picks up `pythonpath = ["src"]` from [`pyproject.toml`](pyproject.toml)):

```bash
pytest
```

## Running nodes manually

Each node needs a unique **`--node-id`**, **`--bind`** address, the **`--leader`** address (the primary in leader mode; still required in quorum mode for the CLI), and a **`--peers`** list. The test harness passes the **full** set of replica addresses (including the node’s own address) as peers.

Example: three **leader**-mode replicas on one machine.

**Terminal 1 (leader `n1`):**

```bash
python -m kvstore.node_main \
  --node-id n1 --bind 127.0.0.1:50051 --leader 127.0.0.1:50051 \
  --peers 127.0.0.1:50051,127.0.0.1:50052,127.0.0.1:50053 \
  --mode leader
```

**Terminal 2 (`n2`):**

```bash
python -m kvstore.node_main \
  --node-id n2 --bind 127.0.0.1:50052 --leader 127.0.0.1:50051 \
  --peers 127.0.0.1:50051,127.0.0.1:50052,127.0.0.1:50053 \
  --mode leader
```

**Terminal 3 (`n3`):**

```bash
python -m kvstore.node_main \
  --node-id n3 --bind 127.0.0.1:50053 --leader 127.0.0.1:50051 \
  --peers 127.0.0.1:50051,127.0.0.1:50052,127.0.0.1:50053 \
  --mode leader
```

For **quorum** mode, use `--mode quorum` and set `--w` / `--r` as needed. Use `--backend sqlite --data-dir /path/to/dir` for durable storage.

See `python -m kvstore.node_main --help` for storage fault flags and anti-entropy tuning.

## CLI client

Point `--target` at any node’s `host:port`:

```bash
python -m kvstore.cli_client --target 127.0.0.1:50051 put mykey "hello"
python -m kvstore.cli_client --target 127.0.0.1:50051 get mykey
python -m kvstore.cli_client --target 127.0.0.1:50051 health
python -m kvstore.cli_client --target 127.0.0.1:50051 metrics
```

Subcommands also include `delete` and `state` (enable/disable serving).

## Experiments and plots

[`scripts/run_experiments.py`](scripts/run_experiments.py) drives an in-process cluster via [`src/kvstore/harness.py`](src/kvstore/harness.py), sweeps modes and scenarios, and writes CSV summaries and a markdown report under **`docs/`** (for example `experiment_results.csv`, `experiment_results.md`).

[`scripts/plot_results.py`](scripts/plot_results.py) reads those CSVs and writes PNGs under **`docs/plots/`**.

```bash
python scripts/run_experiments.py --help
python scripts/plot_results.py --help
```

## Project layout

| Path | Role |
|------|------|
| `proto/` | `kvstore.proto` service and messages |
| `src/kvstore/` | Server, replication, storage, versioning, RPC client |
| `src/tests/` | Pytest suite |
| `scripts/` | Protobuf codegen, experiments, plotting |