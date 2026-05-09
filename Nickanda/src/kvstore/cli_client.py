from __future__ import annotations

import argparse

from kvstore.generated import kvstore_pb2
from kvstore.rpc_client import make_stub


def build_parser() -> argparse.ArgumentParser:
	p = argparse.ArgumentParser(description="KVStore CLI client")
	p.add_argument("--target", required=True, help="host:port")

	sub = p.add_subparsers(dest="cmd", required=True)

	put = sub.add_parser("put")
	put.add_argument("key")
	put.add_argument("value")

	get = sub.add_parser("get")
	get.add_argument("key")

	delete = sub.add_parser("delete")
	delete.add_argument("key")

	sub.add_parser("health")
	sub.add_parser("metrics")

	state = sub.add_parser("state")
	state.add_argument("enabled", choices=["true", "false"])

	return p


def main() -> None:
	args = build_parser().parse_args()
	stub = make_stub(args.target)

	if args.cmd == "put":
		resp = stub.Put(kvstore_pb2.PutRequest(key=args.key, value=args.value.encode("utf-8")))
		print(f"ok={resp.ok} version=({resp.version.logical_time},{resp.version.node_id})")
	elif args.cmd == "get":
		resp = stub.Get(kvstore_pb2.GetRequest(key=args.key))
		if not resp.found:
			print("not found")
		else:
			print(resp.value.decode("utf-8", errors="replace"))
			print(f"version=({resp.version.logical_time},{resp.version.node_id})")
	elif args.cmd == "delete":
		resp = stub.Delete(kvstore_pb2.DeleteRequest(key=args.key))
		print(f"ok={resp.ok} version=({resp.version.logical_time},{resp.version.node_id})")
	elif args.cmd == "health":
		resp = stub.Health(kvstore_pb2.HealthRequest())
		print(f"serving={resp.serving} node_id={resp.node_id} is_leader={resp.is_leader}")
	elif args.cmd == "metrics":
		resp = stub.Metrics(kvstore_pb2.MetricsRequest())
		print(f"put_count={resp.put_count} get_count={resp.get_count} replicate_count={resp.replicate_count}")
		print(f"put_p50={resp.put_p50_ms:.3f} put_p95={resp.put_p95_ms:.3f} put_p99={resp.put_p99_ms:.3f}")
		print(f"get_p50={resp.get_p50_ms:.3f} get_p95={resp.get_p95_ms:.3f} get_p99={resp.get_p99_ms:.3f}")
		print(
			f"repair_ops={resp.repair_ops} repair_bytes={resp.repair_bytes} "
			f"read_repair_ops={resp.read_repair_ops} ae_rounds={resp.anti_entropy_rounds} "
			f"errors={resp.error_count}"
		)
	elif args.cmd == "state":
		enabled = args.enabled == "true"
		resp = stub.SetNodeState(kvstore_pb2.SetNodeStateRequest(enabled=enabled))
		print(f"ok={resp.ok} enabled={enabled}")


if __name__ == "__main__":
	main()

