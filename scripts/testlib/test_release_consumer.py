#!/usr/bin/env python3
"""Contracts for the ADR 0017 Stage 3 stack consumer (WP1.4a)."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from release_spec import load_spec, pretty_json_bytes, verify_spec  # noqa: E402
from scripts import release_consumer as consumer  # noqa: E402
from scripts import release_spec_generate as generate  # noqa: E402
from scripts.testlib.test_release_spec_generate import (  # noqa: E402
    PINNED_IMAGE,
    PLATFORM_ID,
    SUPER_JSON,
    nano_kwargs,
    receipt_for,
)

CLI = ROOT / "scripts" / "release_consumer.py"
GOLDEN = ROOT / "release_spec" / "tests" / "fixtures" / "golden_released.json"
SITE_NODE = "site-node-never-print-xyz"


def golden_spec() -> dict:
    return load_spec(GOLDEN)


def write_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty_json_bytes(value))


def make_repo() -> pathlib.Path:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-release-consumer-"))
    (tmp / consumer.RELEASES_DIR).mkdir()
    return tmp


def run_cli(
    args: list[str],
    *,
    repo: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(CLI)]
    if repo is not None:
        cmd.extend(["--repo-root", str(repo)])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def overlay_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 1,
        "kind": consumer.OVERLAY_KIND,
        "defaults": {
            "port": 8000,
            "served_name": None,
            "cache_root": None,
            "placement": None,
        },
        "specs": {},
    }
    document.update(overrides)
    return document


def super_identity_kwargs() -> dict[str, object]:
    receipt = json.loads(
        receipt_for("nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4").read_text(
            encoding="utf-8"
        )
    )
    return {
        "model_id": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4",
        "image": PINNED_IMAGE,
        "nodes": 1,
        "gpu_mem_util": "0.85",
        "engine_args": [
            "--max-model-len",
            "32768",
            "--max-num-seqs",
            "32",
            "--kv-cache-dtype",
            "fp8",
            "--moe-backend",
            "marlin",
        ],
        "container_env": ["VLLM_MARLIN_USE_ATOMIC_ADD=1"],
        "spec_decode_args": ["--speculative-config", SUPER_JSON],
        "platform_id": PLATFORM_ID,
        "snapshot_revision": receipt["snapshot_revision"],
        "files": receipt["observed_manifest"]["files"],
        "receipt_model_id": receipt["model_id"],
    }


def nano_identity_kwargs() -> dict[str, object]:
    receipt = json.loads(receipt_for(nano_kwargs()["model_id"]).read_text(encoding="utf-8"))
    return {
        "model_id": nano_kwargs()["model_id"],
        "image": PINNED_IMAGE,
        "nodes": 1,
        "gpu_mem_util": "0.80",
        "engine_args": [
            "--max-model-len",
            "131072",
            "--max-num-seqs",
            "16",
            "--moe-backend",
            "marlin",
        ],
        "container_env": ["VLLM_MARLIN_USE_ATOMIC_ADD=1"],
        "spec_decode_args": [],
        "spec_decode": False,
        "platform_id": PLATFORM_ID,
        "snapshot_revision": receipt["snapshot_revision"],
        "files": receipt["observed_manifest"]["files"],
        "receipt_model_id": receipt["model_id"],
    }


class ReleaseConsumerTests(unittest.TestCase):
    def tearDown(self) -> None:
        leftover = getattr(self, "_tmp", None)
        if leftover is not None:
            shutil.rmtree(leftover, ignore_errors=True)

    def _repo(self) -> pathlib.Path:
        self._tmp = make_repo()
        return self._tmp

    def test_empty_releases_lists_nothing_and_unknown_verify_fails(self) -> None:
        repo = self._repo()
        self.assertEqual(consumer.list_releases(repo), [])
        result = run_cli(["list"], repo=repo)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "")
        unknown = "a" * 64
        with self.assertRaisesRegex(consumer.ReleaseConsumerError, "missing"):
            consumer.load_release(repo, unknown)
        verify = run_cli(["verify", unknown], repo=repo)
        self.assertEqual(verify.returncode, 2)
        self.assertIn("error:", verify.stderr)

    def test_golden_released_lists_verifies_and_shows(self) -> None:
        repo = self._repo()
        spec = golden_spec()
        dest = repo / consumer.RELEASES_DIR / f"{spec['spec_id']}.json"
        shutil.copy(GOLDEN, dest)
        rows = consumer.list_releases(repo)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["spec_id"], spec["spec_id"])
        self.assertEqual(rows[0]["model_id"], spec["identity"]["model_id"])
        self.assertEqual(rows[0]["state"], "released")
        self.assertEqual(rows[0]["review_status"], "stable")
        listed = run_cli(["list"], repo=repo)
        self.assertEqual(listed.returncode, 0, msg=listed.stderr)
        self.assertIn(spec["spec_id"], listed.stdout)
        self.assertIn("stable", listed.stdout)
        verified = run_cli(["verify", spec["spec_id"]], repo=repo)
        self.assertEqual(verified.returncode, 0, msg=verified.stderr)
        self.assertIn(f"spec_id={spec['spec_id']}", verified.stdout)
        self.assertIn("state=released", verified.stdout)
        self.assertIn("review=stable", verified.stdout)
        shown = run_cli(["show", spec["spec_id"]], repo=repo)
        self.assertEqual(shown.returncode, 0, msg=shown.stderr)
        self.assertEqual(
            shown.stdout.encode("utf-8"),
            pretty_json_bytes(spec),
        )

    def test_filename_mismatch_measured_and_extra_key_are_refused(self) -> None:
        repo = self._repo()
        spec = golden_spec()
        wrong = repo / consumer.RELEASES_DIR / f"{'b' * 64}.json"
        shutil.copy(GOLDEN, wrong)
        with self.assertRaisesRegex(consumer.ReleaseConsumerError, "filename stem"):
            consumer.list_releases(repo)
        listed = run_cli(["list"], repo=repo)
        self.assertEqual(listed.returncode, 2)
        self.assertIn(str(wrong), listed.stderr)

        shutil.rmtree(repo / consumer.RELEASES_DIR)
        (repo / consumer.RELEASES_DIR).mkdir()
        measured, report = generate.build_spec_from_profile(**nano_kwargs())
        self.assertIsNotNone(measured)
        self.assertTrue(report["generated"])
        assert measured is not None
        measured_path = repo / consumer.RELEASES_DIR / f"{measured['spec_id']}.json"
        write_json(measured_path, measured)
        with self.assertRaisesRegex(consumer.ReleaseConsumerError, "released"):
            consumer.list_releases(repo)

        shutil.rmtree(repo / consumer.RELEASES_DIR)
        (repo / consumer.RELEASES_DIR).mkdir()
        extra = json.loads(GOLDEN.read_text(encoding="utf-8"))
        extra["extra"] = "nope"
        extra_path = repo / consumer.RELEASES_DIR / f"{spec['spec_id']}.json"
        write_json(extra_path, extra)
        with self.assertRaisesRegex(consumer.ReleaseConsumerError, extra_path.name):
            consumer.list_releases(repo)

    def test_overlay_schema_merge_and_recipe_keys(self) -> None:
        repo = self._repo()
        spec = golden_spec()
        path = repo / "overlay.json"
        write_json(path, overlay_document())
        loaded = consumer.load_overlay(path)
        resolved = consumer.overlay_for_spec(loaded, spec)
        self.assertEqual(resolved["served_name"], spec["identity"]["model_id"])
        self.assertEqual(resolved["port"], 8000)
        self.assertIsNone(resolved["cache_root"])
        self.assertIsNone(resolved["placement"])

        spec_id = spec["spec_id"]
        write_json(
            path,
            overlay_document(
                defaults={
                    "port": 8000,
                    "served_name": "default-name",
                    "cache_root": None,
                    "placement": None,
                },
                specs={
                    spec_id: {
                        "port": 9001,
                        "served_name": None,
                        "cache_root": None,
                        "placement": {"node_id": SITE_NODE},
                    }
                },
            ),
        )
        loaded = consumer.load_overlay(path)
        resolved = consumer.overlay_for_spec(loaded, spec)
        self.assertEqual(resolved["served_name"], spec["identity"]["model_id"])
        self.assertEqual(resolved["port"], 9001)
        self.assertEqual(resolved["placement"], {"node_id": SITE_NODE})

        for key in sorted(consumer.RECIPE_OVERLAY_KEYS):
            bad = overlay_document()
            defaults = dict(bad["defaults"])  # type: ignore[arg-type]
            defaults[key] = 1
            bad["defaults"] = defaults
            write_json(path, bad)
            with self.assertRaisesRegex(
                consumer.ReleaseConsumerError, "recipe field"
            ):
                consumer.load_overlay(path)

        unknown = overlay_document()
        unknown["extra"] = True
        write_json(path, unknown)
        with self.assertRaisesRegex(consumer.ReleaseConsumerError, "unknown"):
            consumer.load_overlay(path)

    def test_overlay_node_id_never_appears_in_cli_output(self) -> None:
        repo = self._repo()
        spec = golden_spec()
        dest = repo / consumer.RELEASES_DIR / f"{spec['spec_id']}.json"
        shutil.copy(GOLDEN, dest)
        write_json(
            repo / consumer.OVERLAY_FILENAME,
            overlay_document(
                specs={
                    spec["spec_id"]: {
                        "port": 8000,
                        "served_name": None,
                        "cache_root": None,
                        "placement": {"node_id": SITE_NODE},
                    }
                }
            ),
        )
        for args in (["list"], ["verify", spec["spec_id"]], ["show", spec["spec_id"]]):
            result = run_cli(args, repo=repo)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            combined = result.stdout + result.stderr
            self.assertNotIn(SITE_NODE, combined)
            self.assertNotIn("node_id", combined)

    def test_nano_projector_matches_generator_spec(self) -> None:
        spec, report = generate.build_spec_from_profile(**nano_kwargs())
        self.assertIsNotNone(spec)
        self.assertTrue(report["generated"])
        assert spec is not None
        identity = consumer.profile_identity(**nano_identity_kwargs())
        self.assertEqual(identity, spec["identity"])
        computed = consumer.comparable_contract_from_identity(identity)
        expected = consumer.comparable_contract_from_spec(spec)
        self.assertEqual(
            consumer.compare_contracts(computed, expected),
            {"result": "equal", "fields": []},
        )

        extra_args = consumer.compare_contracts(
            consumer.comparable_contract_from_identity(
                identity, extra_args=("--enforce-eager",)
            ),
            expected,
        )
        self.assertEqual(extra_args, {"result": "differs", "fields": ["argv"]})

        extra_env = consumer.compare_contracts(
            consumer.comparable_contract_from_identity(
                identity, extra_env=("FOO=1",)
            ),
            expected,
        )
        self.assertEqual(
            extra_env, {"result": "differs", "fields": ["container_env"]}
        )

        changed_image = dict(computed)
        changed_image["image_digest"] = "sha256:" + ("d" * 64)
        self.assertEqual(
            consumer.compare_contracts(changed_image, expected),
            {"result": "differs", "fields": ["image_digest"]},
        )

        drifted = json.loads(pretty_json_bytes(spec))
        drifted["launch_contract"]["stack_version"] = "9.9.9-other"
        drifted_spec = verify_spec(drifted)
        self.assertEqual(
            consumer.compare_contracts(
                computed, consumer.comparable_contract_from_spec(drifted_spec)
            ),
            {"result": "equal", "fields": []},
        )

        overlay_path = pathlib.Path(tempfile.mkdtemp()) / "overlay.json"
        write_json(
            overlay_path,
            overlay_document(
                defaults={
                    "port": 9000,
                    "served_name": "nemotron-3-nano",
                    "cache_root": None,
                    "placement": None,
                }
            ),
        )
        loaded = consumer.load_overlay(overlay_path)
        resolved = consumer.overlay_for_spec(loaded, spec)
        self.assertEqual(resolved["port"], 9000)
        self.assertEqual(resolved["served_name"], "nemotron-3-nano")
        self.assertEqual(
            consumer.compare_contracts(computed, expected),
            {"result": "equal", "fields": []},
        )
        shutil.rmtree(overlay_path.parent, ignore_errors=True)

    def test_super_has_two_identities_and_default_flag(self) -> None:
        kwargs = super_identity_kwargs()
        off = consumer.profile_identities(**kwargs, recommended_spec=False)
        self.assertEqual(len(off), 2)
        self.assertEqual([row["spec_decode"] for row in off], [False, True])
        self.assertNotEqual(off[0]["spec_id"], off[1]["spec_id"])
        self.assertTrue(off[0]["default"])
        self.assertFalse(off[1]["default"])
        self.assertNotIn("--speculative-config", off[0]["contract"]["argv"])
        self.assertIn("--speculative-config", off[1]["contract"]["argv"])
        self.assertIn(SUPER_JSON, off[1]["contract"]["argv"])

        on = consumer.profile_identities(**kwargs, recommended_spec=True)
        self.assertFalse(on[0]["default"])
        self.assertTrue(on[1]["default"])
        self.assertEqual(on[0]["spec_id"], off[0]["spec_id"])
        self.assertEqual(on[1]["spec_id"], off[1]["spec_id"])

        nano = consumer.profile_identities(
            **{k: v for k, v in nano_identity_kwargs().items() if k != "spec_decode"},
            recommended_spec=False,
        )
        self.assertEqual(len(nano), 1)
        self.assertFalse(nano[0]["spec_decode"])
        self.assertTrue(nano[0]["default"])


if __name__ == "__main__":
    unittest.main()
