from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class NodeConfig:
	"""Runtime configuration for a single node.

	Most fields come from CLI flags on ``node_main``. Experiment runners can
	build this directly to spin up in-process test clusters.
	"""

	node_id: str
	bind_address: str
	peers: list[str]
	leader_address: str
	mode: str = "leader"  # "leader" | "quorum"
	versioning: str = "lamport"  # "lamport" | "vector"
	backend: str = "memory"  # "memory" | "sqlite"
	data_dir: str | None = None
	w: int = 2
	r: int = 2
	storage_delay_ms: float = 0.0
	storage_stall_prob: float = 0.0
	storage_stall_ms: float = 0.0
	storage_fail_slow_period: int = 0
	storage_fail_slow_burst_ms: float = 0.0
	storage_fail_slow_burst_len: int = 0
	storage_seed: int | None = None
	anti_entropy_interval_sec: float = 0.0  # 0 disables
	tags: dict[str, str] = field(default_factory=dict)

	@property
	def is_leader(self) -> bool:
		if self.mode == "leader":
			return self.bind_address == self.leader_address
		return False
