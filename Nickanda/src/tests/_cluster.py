"""Alias of ``kvstore.harness`` for test callers.

All build logic lives in ``kvstore.harness`` so the experiment runner and the
test suite share a single codepath.
"""

from __future__ import annotations

from kvstore.harness import (  # noqa: F401
	Cluster,
	NodeHandle,
	build_cluster,
	build_node,
	free_port,
	wait_ready,
)

# Backward-compat alias: tests use the leading-underscore name.
_build_node = build_node
_free_port = free_port
