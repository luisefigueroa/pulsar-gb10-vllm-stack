#!/usr/bin/env bash
# Topology-bound SSH identity contracts (no hardware/network required).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$REPO_DIR/scripts/testlib/test_topology_ssh_trust.py"
echo "topology SSH trust selftest PASS"
