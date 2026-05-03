# Paper 2 — Modal App

Run the validate_recall bake and v3 capture smoke on Modal H100, off the
shared lab GPU.

## One-time setup

The Modal CLI is already installed in `/home/vlad/adaptivecache/.venv/bin/modal`.

```bash
# Authenticate (opens a browser; pastes a token into ~/.modal.toml)
/home/vlad/adaptivecache/.venv/bin/modal token new
```

The image picks up `ANTHROPIC_API_KEY` from `/home/vlad/adaptivecache/.env`
via `Secret.from_dotenv` — no separate `modal secret create` needed.

## Run the validate_recall bake (5 variants × N seeds × M tasks)

```bash
cd /home/vlad/adaptivecache-paper2

# Defaults: pytest-7490, 3 seeds, T=0.6, all 5 variants
/home/vlad/adaptivecache/.venv/bin/modal run \
  studies.lifetime_cost.paper2.modal_app.run_validate_recall

# Or with overrides (modal CLI args use --kebab-case)
/home/vlad/adaptivecache/.venv/bin/modal run \
  studies.lifetime_cost.paper2.modal_app.run_validate_recall \
  --instances pytest-dev__pytest-7490,pytest-dev__pytest-5413 \
  --n-seeds 3 \
  --temperature 0.6 \
  --variants off,lru-append,embedding-append
```

First run pays the model-download cost (~60GB Qwen3-30B-A3B at ~200MB/s
≈ 5 min) into the persistent `paper2-hf-cache` volume. Subsequent runs
hit the cache.

## Run the v3 capture smoke

```bash
/home/vlad/adaptivecache/.venv/bin/modal run \
  studies.lifetime_cost.paper2.modal_app.run_smoke_v3_capture
```

A pass means: capture chain fires; CPU bytes land in MementoStore.
A "FAIL or NEEDS-PHASE-3" means the worker captures into a different
process than the scheduler — expected in V1 multi-process deployments,
and a real Phase 3 surface to add.

## Fetch results back to local

Trajectories and the `validate_recall_summary.json` land on the
`paper2-out-v3` volume.

```bash
/home/vlad/adaptivecache/.venv/bin/modal volume get paper2-out-v3 /validate_recall ./modal_out_v3
```

## What's in the image

See `image.py` for full build steps. Highlights:

- CUDA 12.8 base, Python 3.12
- vLLM 0.13.0 + sentence-transformers + anthropic + openai
- Our github repo cloned at `paper2-memento-recall` (the patches + sources)
- microsoft/memento cloned at the patch's base commit (d8c10e6)
- v3 Phase 1 overlay applied via `v3_overlay_patches/apply.sh`
- Memento overlay rsynced over vLLM in site-packages via `install_overlay.sh`

## Cost rough order

H100 80GB on Modal: ~$3.95/hr. A single 3-seed × 5-variant pytest-7490
bake at ~30 min wall ≈ $2. Smoke test ≈ $0.30.

## Troubleshooting

* **"Token missing"** — run `modal token new`.
* **".env not found"** — the Secret loader expects `/home/vlad/adaptivecache/.env`.
  Adjust the path in the entrypoint or pre-create a Modal secret named
  `paper2-anthropic` with `ANTHROPIC_API_KEY`.
* **Image build fails on overlay apply** — the patches assume
  microsoft/memento at d8c10e6. If upstream moved, pin a different
  commit in `image.py:MEMENTO_COMMIT`.
* **OOM on H100** — drop `--gpu-util` to 0.85 or lower the model-len.
