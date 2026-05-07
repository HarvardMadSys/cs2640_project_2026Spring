# Policy Plugins

Policy plugins let us iterate on placement heuristics without editing the
benchmark runner. Use a named plugin from this directory:

```sh
./src/scripts/run_posix_bench.sh --policy predictive
./src/scripts/run_posix_bench.sh --policy LLM_policy
```

Or pass an explicit plugin file:

```sh
./src/scripts/run_posix_bench.sh --policy-file src/policies/example_predictive.py
```

The file must expose:

```py
def create_policy(config):
    ...
```

`config` is a `cephfs_metadata.policies.PolicyConfig`. The returned object should
implement `on_dirs_created(backend, dirs)`, `before_operation(backend, operation,
path)`, and `events()`. In practice, subclassing or wrapping the built-in policy
classes is easiest.

Current policy files:

- `none.py`: native behavior.
- `static.py`: round-robin explicit `ceph.dir.pin` baseline.
- `predictive.py`: sliding-window hot-directory pinning.
- `LLM_policy.py`: no runtime LLM call; this is a hand-coded heuristic inspired
  by the kind of policy an LLM suggested for this project, combining hotness,
  fanout, and cooldown signals.
- `static_top.py`: conservative static baseline that pins only top-level
  benchmark subtrees.
- `prepin_hotset.py`: pins a small declared hot set of top-level subtrees before
  measured operations begin.
- `prepin_colocated_hotset.py`: pins the workload root and declared hot set to
  the same MDS rank to test whether parent/child co-location reduces forwarding.
- `predictive_safe.py`: create-only hotness tracker with a higher default
  threshold to avoid reactive migrations during read/stat/delete phases.
