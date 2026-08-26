#!/usr/bin/env python3
"""Contracts for the SIM-04 launch-plan and serving-probe schemas."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import launch_plan as plan  # noqa: E402

PROFILE = "qwen3-1.7b-2node"
TOPOLOGY = "a" * 64
CONTRACT = "b" * 64
REVISION = "c" * 40
SEAL = "d" * 64
BUNDLE = "e" * 64
CONTENT = "f" * 12
NODE_A = "node-zero"
NODE_B = "node-one"
HUB = "/data/models/qwen3-1.7b"
CONTAINER_PATH = (
    "/root/.cache/huggingface/hub/models--Qwen--Qwen3-1.7B/snapshots/" + REVISION
)


def rank(
    index: int,
    *,
    node_id: str,
    hostname: str,
    ssh_host: str,
    ip: str,
) -> dict[str, object]:
    return {
        "rank": index,
        "node_id": node_id,
        "hostname": hostname,
        "ssh_host": ssh_host,
        "control_ip": ip,
        "control_if": "lan0",
        "hcas": "roce0",
    }


def facts(*, nodes: int = 1, identity: str = "receipt-occupancy", extra: dict | None = None) -> dict:
    ranks = [
        rank(0, node_id=NODE_A, hostname="spark-a", ssh_host="local", ip="192.0.2.10")
    ]
    if nodes > 1:
        ranks.append(
            rank(
                1,
                node_id=NODE_B,
                hostname="spark-b",
                ssh_host="spark-b.local",
                ip="192.0.2.11",
            )
        )
    storage = {
        "mechanism": "local-files",
        "identity_status": identity,
        "revision": REVISION,
        "home_node_id": NODE_A,
        "content_id": CONTENT,
        "hub_path": HUB,
        "container_model_path": CONTAINER_PATH,
        "transport": "ssh-roce" if nodes > 1 else "ssh-control",
    }
    document = {
        "lifecycle_action": "dry-run",
        "profile": PROFILE,
        "served_name": PROFILE,
        "model_id": "Qwen/Qwen3-1.7B",
        "image": "vllm/vllm-openai@sha256:" + ("0" * 64),
        "nodes": nodes,
        "port": 8000,
        "gpu_mem_util": 0.3,
        "topology_id": TOPOLOGY,
        "launch_contract_id": CONTRACT,
        "spec_decode": {"enabled": False, "source": "profile-default"},
        "storage": storage,
        "ranks": ranks,
    }
    if extra:
        document.update(extra)
    return document


class LaunchPlanContracts(unittest.TestCase):
    def test_n1_and_n2_share_schema_and_label_keys(self) -> None:
        one = plan.build_launch_plan(facts(nodes=1))
        two = plan.build_launch_plan(facts(nodes=2))
        self.assertEqual(one["schema_version"], 1)
        self.assertEqual(set(one) - {"ranks", "nodes", "network_mode", "container_name"},
                         set(two) - {"ranks", "nodes", "network_mode", "container_name"})
        spec1 = plan.rank_container_spec(one, 0)
        spec2 = plan.rank_container_spec(two, 0)
        shared = {
            plan.LABEL_MANAGED,
            plan.LABEL_CONF,
            plan.LABEL_RANK,
            plan.LABEL_WORLD_SIZE,
            plan.LABEL_TOPOLOGY,
            plan.LABEL_NODE_ID,
            plan.LABEL_LAUNCH_CONTRACT,
            plan.LABEL_SPEC_DECODE,
            plan.LABEL_WEIGHT_SOURCE,
            plan.LABEL_WEIGHT_OWNER,
            plan.LABEL_WEIGHT_CONFIG,
            plan.LABEL_MODEL_REVISION,
            plan.LABEL_IDENTITY_STATUS,
        }
        self.assertTrue(shared <= set(spec1["labels"]))
        self.assertTrue(shared <= set(spec2["labels"]))
        self.assertEqual(spec1["labels"][plan.LABEL_WEIGHT_SOURCE], "local-files")
        self.assertEqual(spec2["labels"][plan.LABEL_WEIGHT_SOURCE], "local-files")
        self.assertEqual(spec1["mounts"], spec2["mounts"])
        self.assertTrue(spec1["api_auth_on_rank"])
        self.assertTrue(spec2["api_auth_on_rank"])
        self.assertFalse(plan.rank_container_spec(two, 1)["api_auth_on_rank"])

    def test_current_n1_rank_label_and_network_are_preserved(self) -> None:
        one = plan.build_launch_plan(facts(nodes=1))
        spec = plan.rank_container_spec(one, 0)
        self.assertEqual(one["ranks"][0]["rank_label"], "single")
        self.assertEqual(spec["rank_label"], "single")
        self.assertEqual(one["network_mode"], "published-port")
        self.assertEqual(spec["network"]["mode"], "published-port")
        self.assertEqual(spec["health"]["kind"], "docker-health-cmd")
        self.assertEqual(one["container_name"], "vllm-qwen3-1.7b-2node")

    def test_n2_uses_numeric_ranks_host_network_and_completion_liveness(self) -> None:
        two = plan.build_launch_plan(facts(nodes=2))
        head = plan.rank_container_spec(two, 0)
        worker = plan.rank_container_spec(two, 1)
        self.assertEqual(two["ranks"][0]["rank_label"], "0")
        self.assertEqual(two["ranks"][1]["rank_label"], "1")
        self.assertEqual(two["network_mode"], "host")
        self.assertEqual(head["network"]["mode"], "host")
        self.assertEqual(worker["devices"], ["/dev/infiniband"])
        self.assertEqual(head["liveness"]["kind"], "completion-probe")
        self.assertEqual(two["container_name"], "vllm-cluster-qwen3-1.7b-2node")

    def test_plan_is_not_a_permit(self) -> None:
        document = facts()
        document["authorized"] = True
        with self.assertRaisesRegex(plan.LaunchPlanError, "not a permit"):
            plan.build_launch_plan(document)
        valid = plan.build_launch_plan(facts())
        self.assertIs(valid["is_permit"], False)

    def test_removed_weight_axis_is_rejected(self) -> None:
        document = facts()
        document["weight_source"] = "replicated"
        with self.assertRaisesRegex(plan.LaunchPlanError, "weight-source"):
            plan.build_launch_plan(document)
        document = facts()
        document["storage"]["mechanism"] = "replicated"
        with self.assertRaisesRegex(plan.LaunchPlanError, "local-files"):
            plan.build_launch_plan(document)

    def test_storage_refuses_retired_seal_fields(self) -> None:
        document = facts(identity="receipt-occupancy")
        document["storage"]["model_seal_id"] = SEAL
        with self.assertRaisesRegex(plan.LaunchPlanError, "ADR 0012"):
            plan.build_launch_plan(document)
        clean = plan.build_launch_plan(facts(identity="receipt-occupancy"))
        self.assertNotIn("model_seal_id", clean["storage"])
        self.assertNotIn("validation_bundle_id", clean["storage"])

    def test_absolute_path_model_id_is_rejected(self) -> None:
        document = facts()
        document["model_id"] = "/mnt/Models/Qwen/Qwen3-1.7B"
        with self.assertRaisesRegex(plan.LaunchPlanError, "absolute-path"):
            plan.build_launch_plan(document)

    def test_rank_count_must_match_nodes(self) -> None:
        document = facts(nodes=2)
        document["ranks"] = document["ranks"][:1]
        with self.assertRaisesRegex(plan.LaunchPlanError, "length must equal"):
            plan.build_launch_plan(document)

    def test_wrong_network_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(plan.LaunchPlanError, "published-port"):
            plan.build_launch_plan(facts(nodes=1, extra={"network_mode": "host"}))
        with self.assertRaisesRegex(plan.LaunchPlanError, "host"):
            plan.build_launch_plan(
                facts(nodes=2, extra={"network_mode": "published-port"})
            )

    def test_image_aggregate_drops_pair_only_names(self) -> None:
        self.assertEqual(plan.rank_image_aggregate_state(["ok", "ok"]), "ok")
        self.assertEqual(
            plan.rank_image_aggregate_state(["ok", "missing-on-worker"]),
            "missing-on-rank",
        )
        self.assertEqual(
            plan.rank_image_aggregate_state(["ok", "worker-unreachable"]),
            "rank-unreachable",
        )
        self.assertEqual(
            plan.rank_image_aggregate_state(["head-docker-error"]),
            "rank-docker-error",
        )
        self.assertEqual(
            plan.rank_image_aggregate_state(["missing-on-head"]),
            "missing-on-rank",
        )

    def test_probe_node_mapping_n1_and_n2(self) -> None:
        probe = {
            "probe_schema_version": 2,
            "gpu": "NVIDIA GB10",
            "docker_ok": True,
            "docker_nvidia": True,
            "hostname": "spark-a",
            "node_id": NODE_A,
            "rdma": [{"hca": "roce0"}],
            "reject_reasons": [],
        }
        one = plan.serving_rank_probe_from_node_probe(probe, 0)
        self.assertTrue(one["ok"])
        self.assertEqual(one["kind"], plan.PROBE_KIND)
        two = plan.serving_rank_probe_from_node_probe(
            probe, 1, require_rdma=True
        )
        self.assertTrue(two["ok"])
        bad = dict(probe)
        bad["gpu"] = "CPU"
        mapped = plan.serving_rank_probe_from_node_probe(bad, 0)
        self.assertFalse(mapped["ok"])

    def test_docker_argv_n1_and_n2_share_mounts_and_auth_split(self) -> None:
        one = plan.build_launch_plan(facts(nodes=1))
        two = plan.build_launch_plan(facts(nodes=2))
        argv1 = plan.rank_docker_argv(one, 0, detach=True)
        argv2_head = plan.rank_docker_argv(two, 0)
        argv2_worker = plan.rank_docker_argv(two, 1)
        self.assertIn("-v", argv1)
        self.assertIn(
            f"{HUB}:/root/.cache/huggingface/hub/models--Qwen--Qwen3-1.7B:ro",
            argv1,
        )
        self.assertIn(
            f"{HUB}:/root/.cache/huggingface/hub/models--Qwen--Qwen3-1.7B:ro",
            argv2_head,
        )
        self.assertIn(f"{plan.LABEL_MANAGED}=true", " ".join(argv1))
        self.assertIn("--nnodes", argv2_head)
        self.assertIn("--headless", argv2_worker)
        self.assertNotIn("--headless", argv2_head)
        self.assertIn("--health-cmd", argv1)
        self.assertNotIn("--health-cmd", argv2_head)
        self.assertIn("-p", argv1)
        self.assertIn("--network", argv2_head)

    def test_rank_spec_honors_models_nfs_override(self) -> None:
        document = facts(nodes=2)
        document["runtime"] = {
            **plan.DEFAULT_RUNTIME,
            "models_nfs": "/data/Models",
        }
        built = plan.build_launch_plan(document)
        spec = plan.rank_container_spec(built, 0)
        self.assertEqual(
            spec["mounts"][1],
            {"source": "/data/Models", "target": "/mnt/Models", "mode": "ro"},
        )
        argv = plan.rank_docker_argv(built, 0)
        self.assertIn("/data/Models:/mnt/Models:ro", argv)

    def test_cli_build_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "facts.json"
            path.write_text(json.dumps(facts(nodes=2)), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "launch_plan.py"), "build", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(result.stdout)
            self.assertEqual(document["nodes"], 2)
            self.assertIs(document["is_permit"], False)


if __name__ == "__main__":
    unittest.main()
