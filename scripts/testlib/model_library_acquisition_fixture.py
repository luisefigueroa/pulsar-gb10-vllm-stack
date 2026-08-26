#!/usr/bin/env python3
"""Create a thin local fixture for the public durable-home acquisition CLI."""

from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.topology_manifest import topology_digest  # noqa: E402


def write_executable(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: model_library_acquisition_fixture.py ROOT")
    root = pathlib.Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    topology = {
        "schema_version": 1,
        "nodes": [
            {
                "rank": 0,
                "node_id": "fixture-acquisition-node",
                "hostname": "fixture-controller",
                "ssh_host": "local",
                "control": {"interface": "lan0", "ip": "192.0.2.10"},
                "gpu": "NVIDIA GB10",
                "rdma": [],
            }
        ],
        "links": [],
        "validation": {
            "class": "roce-full-mesh",
            "full_mesh": True,
            "connectivity_verified": True,
            "min_rails_per_pair": 0,
        },
    }
    topology["topology_id"] = topology_digest(topology)
    (root / "topology.json").write_text(json.dumps(topology), encoding="utf-8")

    bin_dir = root / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "hf",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$MOCK_HF_LOG"
""",
    )
    write_executable(
        root / "model_library_wrapper.py",
        f"""#!/usr/bin/env python3
import os
import sys

REAL = {str(REPO_ROOT / 'scripts' / 'model_library.py')!r}
os.execv(sys.executable, [sys.executable, REAL, *sys.argv[1:]])
""",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
