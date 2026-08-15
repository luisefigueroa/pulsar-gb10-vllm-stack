#!/usr/bin/env python3
"""Contracts for unreviewed Model Serving Release planning."""

from __future__ import annotations

import copy
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    model_library,
    model_serving_release,
    model_serving_release_plan,
)
from scripts.testlib import model_serving_release_fixture as fixture  # noqa: E402


PROFILE = "qwen3-1.7b"
PROFILE_IMAGE = (
    "vllm/vllm-openai@sha256:"
    "ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52"
)


def write_json(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def manifest(*, model_id: str = "Qwen/Qwen3-1.7B") -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": model_library.SNAPSHOT_MANIFEST_SCHEMA_VERSION,
        "kind": model_library.SNAPSHOT_MANIFEST_KIND,
        "model_id": model_id,
        "snapshot_revision": "a" * 40,
        "files": [{"path": "config.json", "size": 2, "sha256": "b" * 64}],
        "file_count": 1,
        "total_bytes": 2,
    }
    value["manifest_id"] = model_library.snapshot_manifest_id(value)
    return value


def runtime_envelope(
    *,
    image_reference: str = PROFILE_IMAGE,
    node_count: int = 1,
) -> dict[str, object]:
    runtime = fixture.build_runtime(image_reference=image_reference)
    multi_node = node_count > 1
    geometry = model_serving_release.build_supported_hardware_geometry(
        hardware_class="nvidia-dgx-spark-gb10",
        architecture="aarch64",
        node_count=node_count,
        accelerators_per_node=1,
        accelerator_count=node_count,
        tensor_parallel_size=node_count,
        pipeline_parallel_size=1,
        topology_class="roce-full-mesh" if multi_node else "single",
        interconnect_class="roce-v2" if multi_node else "local",
        minimum_rails_per_pair=2 if multi_node else 0,
        minimum_unified_memory_gib_per_node="128",
    )
    return {
        "schema_version": model_serving_release_plan.ENVELOPE_SCHEMA_VERSION,
        "kind": model_serving_release_plan.ENVELOPE_KIND,
        "runtime_image_identity": runtime,
        "supported_hardware_geometry": geometry,
    }


def criteria_input() -> dict[str, object]:
    return copy.deepcopy(fixture.build_contract()["release_criteria"])


class ModelServingReleasePlanTests(unittest.TestCase):
    def run_cli(
        self,
        *arguments: str,
        expect_success: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                str(REPO_ROOT / "scripts" / "model-serving-release-plan.sh"),
                *arguments,
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if expect_success and result.returncode != 0:
            self.fail(
                f"command failed:\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        if not expect_success and result.returncode == 0:
            self.fail(f"command unexpectedly passed:\nstdout={result.stdout}")
        return result

    def test_public_cli_builds_and_verifies_unreviewed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest_path = root / "manifest.json"
            envelope_path = root / "runtime-envelope.json"
            criteria_path = root / "criteria.json"
            adapter_path = root / "adapter.json"
            candidate_dir = root / "candidate"
            write_json(manifest_path, manifest())
            write_json(envelope_path, runtime_envelope())
            write_json(criteria_path, criteria_input())
            write_json(
                adapter_path,
                {
                    "artifact_key": "adapter",
                    "kind": "digest-artifact",
                    "artifact_id": "fixture/adapter",
                    "revision": "v1",
                    "digest": {"scheme": "sha256", "value": "d" * 64},
                },
            )

            build = self.run_cli(
                "build",
                PROFILE,
                "--artifact-manifest",
                str(manifest_path),
                "--runtime-envelope",
                str(envelope_path),
                "--criteria",
                str(criteria_path),
                "--model-access-contract",
                "local-verified-readonly",
                "--artifact",
                str(adapter_path),
                "--artifact-binding",
                "adapter=adapter",
                "--output-dir",
                str(candidate_dir),
                "--json",
            )
            result = json.loads(build.stdout)
            self.assertEqual(result["state"], "unreviewed")
            self.assertEqual(result["authority"], "none")
            self.assertEqual(result["promotion"], "not-authorized")
            self.assertEqual(
                {item.name for item in candidate_dir.iterdir()},
                {"candidate.json", "release.json", "validation-contract.json"},
            )

            release = json.loads((candidate_dir / "release.json").read_text())
            self.assertNotIn("status", release)
            self.assertEqual(
                {item["kind"] for item in release["model_artifact_set"]["artifacts"]},
                {"huggingface-snapshot", "digest-artifact"},
            )
            verification = self.run_cli(
                "verify",
                PROFILE,
                "--candidate-dir",
                str(candidate_dir),
                "--model-access-contract",
                "local-verified-readonly",
                "--json",
            )
            self.assertEqual(json.loads(verification.stdout)["verification"], "passed")

    def test_candidate_directory_is_published_atomically(self) -> None:
        documents = {
            "candidate.json": {"kind": "candidate"},
            "release.json": {"kind": "release"},
            "validation-contract.json": {"kind": "contract"},
        }
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            candidate_dir = root / "candidate"
            real_atomic_write = model_serving_release_plan.atomic_write_json
            final_path_states: list[bool] = []

            def observed_write(path: pathlib.Path, value: object) -> None:
                final_path_states.append(candidate_dir.exists())
                real_atomic_write(path, value)

            with mock.patch.object(
                model_serving_release_plan,
                "atomic_write_json",
                side_effect=observed_write,
            ):
                model_serving_release_plan.write_candidate_directory(
                    candidate_dir,
                    documents,
                )

            self.assertEqual(final_path_states, [False, False, False])
            self.assertEqual(
                {item.name for item in candidate_dir.iterdir()},
                set(documents),
            )

    def test_candidate_write_failure_leaves_no_partial_output(self) -> None:
        documents = {
            "candidate.json": {"kind": "candidate"},
            "release.json": {"kind": "release"},
            "validation-contract.json": {"kind": "contract"},
        }
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            candidate_dir = root / "candidate"
            real_atomic_write = model_serving_release_plan.atomic_write_json
            writes = 0

            def interrupted_write(path: pathlib.Path, value: object) -> None:
                nonlocal writes
                writes += 1
                real_atomic_write(path, value)
                if writes == 2:
                    raise OSError("simulated candidate write failure")

            with mock.patch.object(
                model_serving_release_plan,
                "atomic_write_json",
                side_effect=interrupted_write,
            ), self.assertRaisesRegex(OSError, "simulated candidate write failure"):
                model_serving_release_plan.write_candidate_directory(
                    candidate_dir,
                    documents,
                )

            self.assertFalse(candidate_dir.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_public_cli_builds_source_neutral_catalog_primary(self) -> None:
        catalog_profile = "inkling-small-nvfp4"
        catalog_image = (
            "ghcr.io/luisefigueroa/pulsar-gb10-vllm-stack@sha256:"
            "260c854707e8e6db5001838998e390011b648f127bd42aa8705ad7a808fbe9e2"
        )
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest_path = root / "manifest.json"
            envelope_path = root / "runtime-envelope.json"
            criteria_path = root / "criteria.json"
            candidate_dir = root / "candidate"
            catalog_manifest = manifest(
                model_id="Thinkingmachines/Inkling-Small-NVFP4"
            )
            catalog_manifest["snapshot_revision"] = "catalog-release-v1"
            catalog_manifest["manifest_id"] = model_library.snapshot_manifest_id(
                catalog_manifest
            )
            write_json(manifest_path, catalog_manifest)
            write_json(
                envelope_path,
                runtime_envelope(image_reference=catalog_image, node_count=2),
            )
            write_json(criteria_path, criteria_input())
            self.run_cli(
                "build",
                catalog_profile,
                "--artifact-manifest",
                str(manifest_path),
                "--runtime-envelope",
                str(envelope_path),
                "--criteria",
                str(criteria_path),
                "--model-access-contract",
                "live-remote-readonly",
                "--output-dir",
                str(candidate_dir),
            )
            release_raw = (candidate_dir / "release.json").read_text()
            release = json.loads(release_raw)
            primary = release["model_artifact_set"]["artifacts"][0]
            self.assertEqual(primary["kind"], "content-addressed-model")
            self.assertEqual(
                primary["artifact_id"],
                "Thinkingmachines/Inkling-Small-NVFP4",
            )
            self.assertNotIn("Official Models", release_raw)
            self.assertNotIn("/mnt/", release_raw)

            verification = self.run_cli(
                "verify",
                catalog_profile,
                "--candidate-dir",
                str(candidate_dir),
                "--model-access-contract",
                "live-remote-readonly",
                "--json",
            )
            self.assertEqual(json.loads(verification.stdout)["verification"], "passed")

    def test_each_additional_artifact_requires_one_binding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            artifact_path = root / "adapter.json"
            write_json(
                artifact_path,
                {
                    "artifact_key": "adapter",
                    "kind": "digest-artifact",
                    "artifact_id": "fixture/adapter",
                    "revision": "v1",
                    "digest": {"scheme": "sha256", "value": "d" * 64},
                },
            )
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "requires exactly one artifact binding",
            ):
                model_serving_release_plan._additional_artifacts_and_bindings(
                    [str(artifact_path)],
                    [],
                )

    def test_private_profile_artifact_reference_normalizes_to_public_key(self) -> None:
        source = "/mnt/catalog/private-draft"
        reference_map = model_serving_release_plan._artifact_reference_map(
            [f"draft={source}"],
            artifact_keys={"draft"},
        )
        arguments = [
            "--speculative-config",
            json.dumps({"model": source, "num_speculative_tokens": 5}),
        ]
        model_serving_release_plan._validate_reference_usage(
            reference_map,
            argument_groups=(arguments,),
        )
        normalized = model_serving_release_plan._normalize_profile_arguments(
            arguments,
            reference_map=reference_map,
        )
        self.assertNotIn(source, json.dumps(normalized))
        self.assertEqual(json.loads(normalized[1])["model"], "draft")

        with self.assertRaisesRegex(
            model_serving_release_plan.ModelServingReleasePlanError,
            "does not match a profile argument",
        ):
            model_serving_release_plan._validate_reference_usage(
                reference_map,
                argument_groups=(["--unrelated"],),
            )

    def test_human_output_wraps_at_narrow_terminal_width(self) -> None:
        output = io.StringIO()
        prior_columns = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = "48"
        try:
            with contextlib.redirect_stdout(output):
                model_serving_release_plan.render_result(
                    {
                        "state": "unreviewed",
                        "authority": "none",
                        "profile": PROFILE,
                        "release_id": "a" * 64,
                        "contract_id": "b" * 64,
                        "candidate_id": "c" * 64,
                        "promotion": "not-authorized",
                    },
                    json_output=False,
                )
        finally:
            if prior_columns is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = prior_columns
        self.assertTrue(output.getvalue().strip())
        self.assertTrue(all(len(line) <= 48 for line in output.getvalue().splitlines()))

    def test_json_loader_rejects_duplicate_keys_and_nonfinite_constants(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            for name, payload, message in (
                ("duplicate.json", '{"key":1,"key":2}\n', "duplicate key"),
                ("nan.json", '{"key":NaN}\n', "unsupported constant"),
            ):
                with self.subTest(name=name):
                    path = root / name
                    path.write_text(payload)
                    with self.assertRaisesRegex(
                        model_serving_release_plan.ModelServingReleasePlanError,
                        message,
                    ):
                        model_serving_release_plan.load_json(path)

    def test_build_rejects_manifest_for_a_different_hugging_face_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest_path = root / "manifest.json"
            envelope_path = root / "runtime-envelope.json"
            criteria_path = root / "criteria.json"
            write_json(manifest_path, manifest(model_id="Fixture/Different-Model"))
            write_json(envelope_path, runtime_envelope())
            write_json(criteria_path, criteria_input())
            result = self.run_cli(
                "build",
                PROFILE,
                "--artifact-manifest",
                str(manifest_path),
                "--runtime-envelope",
                str(envelope_path),
                "--criteria",
                str(criteria_path),
                "--model-access-contract",
                "local-verified-readonly",
                "--output-dir",
                str(root / "candidate"),
                expect_success=False,
            )
            self.assertIn("manifest model_id differs", result.stderr)

    def test_build_rejects_profile_runtime_and_geometry_mismatches(self) -> None:
        cases = (
            ("image", "runtime envelope image digest differs"),
            ("nodes", "geometry node_count differs"),
        )
        for mutation, message in cases:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as raw:
                root = pathlib.Path(raw)
                manifest_path = root / "manifest.json"
                envelope_path = root / "runtime-envelope.json"
                criteria_path = root / "criteria.json"
                envelope = runtime_envelope()
                if mutation == "image":
                    envelope["runtime_image_identity"]["image"]["digest"] = (
                        "sha256:" + "c" * 64
                    )
                else:
                    geometry = envelope["supported_hardware_geometry"]
                    geometry["node_count"] = 2
                    geometry["accelerator_count"] = 2
                    geometry["tensor_parallel_size"] = 2
                    geometry["topology_class"] = "roce-full-mesh"
                    geometry["interconnect_class"] = "roce-v2"
                    geometry["minimum_rails_per_pair"] = 2
                write_json(manifest_path, manifest())
                write_json(envelope_path, envelope)
                write_json(criteria_path, criteria_input())
                result = self.run_cli(
                    "build",
                    PROFILE,
                    "--artifact-manifest",
                    str(manifest_path),
                    "--runtime-envelope",
                    str(envelope_path),
                    "--criteria",
                    str(criteria_path),
                    "--model-access-contract",
                    "local-verified-readonly",
                    "--output-dir",
                    str(root / "candidate"),
                    expect_success=False,
                )
                self.assertIn(message, result.stderr)

    def test_build_refuses_trusted_and_non_experimental_repo_locations(self) -> None:
        for relative in (
            pathlib.Path("models") / "model-serving-releases" / "candidate",
            pathlib.Path("results") / "candidate",
        ):
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(
                    model_serving_release_plan.ModelServingReleasePlanError,
                    "cannot be written under models|experiments/model-onboarding",
                ):
                    model_serving_release_plan.validate_candidate_location(
                        REPO_ROOT / relative,
                        repo_root=REPO_ROOT,
                    )

    def test_verify_rejects_tampering_and_extra_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest_path = root / "manifest.json"
            envelope_path = root / "runtime-envelope.json"
            criteria_path = root / "criteria.json"
            candidate_dir = root / "candidate"
            write_json(manifest_path, manifest())
            write_json(envelope_path, runtime_envelope())
            write_json(criteria_path, criteria_input())
            self.run_cli(
                "build",
                PROFILE,
                "--artifact-manifest",
                str(manifest_path),
                "--runtime-envelope",
                str(envelope_path),
                "--criteria",
                str(criteria_path),
                "--model-access-contract",
                "local-verified-readonly",
                "--output-dir",
                str(candidate_dir),
            )
            (candidate_dir / "unexpected.txt").write_text("unexpected\n")
            result = self.run_cli(
                "verify",
                PROFILE,
                "--candidate-dir",
                str(candidate_dir),
                "--model-access-contract",
                "local-verified-readonly",
                expect_success=False,
            )
            self.assertIn("file set is invalid", result.stderr)


if __name__ == "__main__":
    unittest.main()
