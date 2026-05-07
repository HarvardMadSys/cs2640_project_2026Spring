"""Batch runner for SWE-bench experiments with AdaptiveCache.

Wraps mini-swe-agent's SWE-bench runner, injecting our AdaptiveCacheModel
as the model class. Handles instance loading, batch execution, and
prediction/trace output.

Usage:
    from harness.swe_runner import run_experiment
    from harness.swe_config import SWEConfig

    config = SWEConfig(
        dataset="lite",
        model_name="anthropic/claude-sonnet-4-5-20250929",
        cache_policy="adaptive",
        cache_budget=64000,
    )
    run_experiment(config)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from datasets import load_dataset

from harness.swe_config import SWEConfig

logger = logging.getLogger("swe_runner")

DATASET_MAPPING = {
    "full": "princeton-nlp/SWE-Bench",
    "verified": "princeton-nlp/SWE-Bench_Verified",
    "lite": "princeton-nlp/SWE-Bench_Lite",
}


def load_instances(config: SWEConfig) -> list[dict]:
    """Load SWE-bench instances, preferring a bundled JSON over HuggingFace."""
    from pathlib import Path

    # Use pre-bundled instances if available (avoids HF dependency at runtime)
    bundled = Path("/app/swebench_instances.json")
    if bundled.exists():
        import json
        instances = json.loads(bundled.read_text())
        logger.info("Loaded %d instances from bundled JSON", len(instances))
    else:
        dataset_name = DATASET_MAPPING.get(config.dataset, config.dataset)
        logger.info("Loading dataset: %s (split=%s)", dataset_name, config.split)
        ds = load_dataset(dataset_name, split=config.split)
        instances = [dict(row) for row in ds]

    # Filter by instance IDs if specified
    if config.instance_ids:
        id_set = set(config.instance_ids)
        instances = [i for i in instances if i["instance_id"] in id_set]
        logger.info("Filtered to %d instances", len(instances))

    # Apply slice
    if config.slice_spec:
        parts = config.slice_spec.split(":")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else len(instances)
        instances = instances[start:end]
        logger.info("Sliced to instances [%d:%d]", start, end)

    logger.info("Loaded %d instances", len(instances))
    return instances


def run_experiment(config: SWEConfig) -> Path:
    """Run a full SWE-bench experiment using mini-swe-agent.

    This delegates to mini-swe-agent's batch runner, passing our
    AdaptiveCacheModel as the model class.

    Returns:
        Path to the output directory containing predictions and traces.
    """
    # Lazy import to avoid requiring mini-swe-agent for basic usage
    from minisweagent.run.benchmarks.swebench import main as swe_main

    run_id = config.run_id or f"{config.cache_policy}_{config.cache_budget}_{int(time.time())}"
    output_dir = Path(config.output_dir) / run_id

    # Build CLI args for mini-swe-agent's SWE-bench runner
    # mini-swe-agent uses typer, so we invoke main() with the right args
    cli_args = {
        "subset": config.dataset,
        "split": config.split,
        "output": str(output_dir),
        "workers": config.workers,
        "model": config.model_name,
        "model_class": "harness.swe_model.AdaptiveCacheModel",
    }

    if config.instance_ids:
        cli_args["filter_spec"] = "|".join(config.instance_ids)
    if config.slice_spec:
        cli_args["slice_spec"] = config.slice_spec

    logger.info("Starting SWE-bench run: %s", run_id)
    logger.info("Policy: %s, Budget: %d, Model: %s", config.cache_policy, config.cache_budget, config.model_name)
    logger.info("Output: %s", output_dir)

    # Save experiment config
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "experiment_config.json").write_text(
        json.dumps({
            "run_id": run_id,
            "config": {
                "dataset": config.dataset,
                "split": config.split,
                "model_name": config.model_name,
                "cache_policy": config.cache_policy,
                "cache_budget": config.cache_budget,
                "max_steps": config.max_steps,
                "cost_limit": config.cost_limit,
            },
            "timestamp": time.time(),
        }, indent=2)
    )

    # Run via mini-swe-agent's CLI
    # For programmatic use, we build the config dict and call process_instance directly
    _run_via_mini_swe_agent(config, output_dir)

    return output_dir


def _run_via_mini_swe_agent(config: SWEConfig, output_dir: Path) -> None:
    """Run instances through mini-swe-agent's infrastructure."""
    from minisweagent.config import builtin_config_dir, get_config_from_spec
    from minisweagent.run.benchmarks.swebench import (
        DEFAULT_CONFIG_FILE,
        process_instance,
    )
    from minisweagent.run.benchmarks.utils.batch_progress import RunBatchProgressManager
    from minisweagent.utils.serialize import recursive_merge

    # Load default SWE-bench config and merge our settings
    base_config = get_config_from_spec(str(DEFAULT_CONFIG_FILE))
    our_config = config.to_mini_swe_config()
    merged = recursive_merge(base_config, our_config)

    # Always use local environment — Modal containers don't have Docker.
    # We clone the repo at base_commit directly into the container filesystem.
    import shutil
    if not shutil.which("docker"):
        logger.info("Docker not available — using local environment (repo clone)")
        merged.setdefault("environment", {})["environment_class"] = "local"
        merged["environment"]["cwd"] = str(output_dir / "workdir")

    # Load instances
    instances = load_instances(config)

    progress = RunBatchProgressManager(num_instances=len(instances))

    for instance in instances:
        instance_id = instance["instance_id"]
        logger.info("Processing: %s", instance_id)

        instance_config = merged.copy()

        # For local environment: clone the repo at base_commit
        if instance_config.get("environment", {}).get("environment_class") == "local":
            workdir = _setup_local_repo(instance, output_dir)
            if workdir is None:
                logger.error("Failed to setup repo for %s", instance_id)
                continue
            instance_config["environment"]["cwd"] = str(workdir)

        try:
            process_instance(instance, output_dir, instance_config, progress)
        except Exception as e:
            logger.error("Failed %s: %s", instance_id, e)
            continue

    logger.info("Run complete. Results in %s", output_dir)


def _setup_local_repo(instance: dict, output_dir: Path) -> Path | None:
    """Clone the instance's repo at base_commit for local execution."""
    import subprocess

    instance_id = instance["instance_id"]
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    workdir = output_dir / "repos" / instance_id

    if workdir.exists():
        # Verify it's actually a valid git repo at the right commit, not a leftover empty dir
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(workdir), capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                logger.info("Repo already exists and is valid: %s", workdir)
                return workdir
        except Exception:
            pass
        logger.info("Repo dir exists but is invalid — re-cloning: %s", workdir)
        import shutil
        shutil.rmtree(workdir)

    workdir.mkdir(parents=True, exist_ok=True)
    logger.info("Cloning %s at %s...", repo, base_commit[:8])

    try:
        # Full clone needed — base_commit is often deep in history
        subprocess.run(
            ["git", "clone", f"https://github.com/{repo}.git", str(workdir)],
            check=True, capture_output=True, text=True, timeout=300,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            cwd=str(workdir), check=True, capture_output=True, text=True, timeout=30,
        )
        logger.info("Repo ready: %s", workdir)
        return workdir
    except Exception as e:
        logger.error("Failed to clone %s: %s", repo, e)
        return None


def collect_cache_traces(output_dir: Path) -> list[dict]:
    """Collect cache traces from all trajectory files in an output directory."""
    traces = []
    for traj_file in output_dir.glob("*/*.traj.json"):
        try:
            data = json.loads(traj_file.read_text())
            if "cache_trace" in data:
                traces.append({
                    "instance_id": traj_file.parent.name,
                    "cache_trace": data["cache_trace"],
                    "cache_final_stats": data.get("cache_final_stats", {}),
                })
        except Exception:
            continue
    return traces
