#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python -m grpc_tools.protoc \
	-I proto \
	--python_out=src/kvstore/generated \
	--grpc_python_out=src/kvstore/generated \
	proto/kvstore.proto

echo "generated gRPC python files in src/kvstore/generated"

