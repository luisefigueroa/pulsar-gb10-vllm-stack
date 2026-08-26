#!/usr/bin/env python3
"""Contracts for draft Model Serving Release planning."""

from __future__ import annotations

import copy
import contextlib
import hashlib
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
    immutable_descriptor_dir,
    model_identity,
    model_library,
    model_serving_release,
    model_serving_release_plan,
)
from scripts.testlib import model_serving_release_fixture as fixture  # noqa: E402


PROFILE = "qwen3.8-27b-fp8"
PROFILE_IMAGE = (
    "vllm/vllm-openai@sha256:"
    "ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52"
)


def write_json(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def rewrite_json(path: pathlib.Path, value: object) -> None:
    path.write_bytes(model_identity.pretty_json_bytes(value))
    os.chmod(path, 0o600)


def publish_plan_candidate(
    dest: pathlib.Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    release = fixture.build_release()
    contract = fixture.build_contract(release=release)
    candidate = model_serving_release_plan.build_candidate_document(
        profile=PROFILE,
        source_kind="hf",
        release=release,
        contract=contract,
    )
    model_serving_release_plan.write_candidate_directory(
        dest,
        {
            "candidate.json": candidate,
            "release.json": release,
            "validation-contract.json": contract,
        },
    )
    return candidate, release, contract


def manifest(*, model_id: str = "Qwen/Qwen3.8-27B-FP8") -> dict[str, object]:
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

    def test_public_cli_builds_and_verifies_draft_candidate(self) -> None:
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
            self.assertEqual(result["state"], "draft")
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

    def test_public_cli_builds_source_neutral_two_node_primary(self) -> None:
        catalog_profile = "qwen3.8-27b-fp8-2node"
        catalog_image = (
            "vllm/vllm-openai@sha256:"
            "1c8e60a0841b333c700488cb029d3664807249da0c071e862191b00fe34b228c"
        )
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest_path = root / "manifest.json"
            envelope_path = root / "runtime-envelope.json"
            criteria_path = root / "criteria.json"
            candidate_dir = root / "candidate"
            catalog_manifest = manifest(model_id="Qwen/Qwen3.8-27B-FP8")
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
                "local-verified-readonly",
                "--output-dir",
                str(candidate_dir),
            )
            release_raw = (candidate_dir / "release.json").read_text()
            release = json.loads(release_raw)
            primary = release["model_artifact_set"]["artifacts"][0]
            self.assertEqual(primary["kind"], "huggingface-snapshot")
            self.assertEqual(
                primary["model_id"], "Qwen/Qwen3.8-27B-FP8"
            )
            self.assertNotIn("Official Models", release_raw)
            self.assertNotIn("/mnt/", release_raw)

            verification = self.run_cli(
                "verify",
                catalog_profile,
                "--candidate-dir",
                str(candidate_dir),
                "--model-access-contract",
                "local-verified-readonly",
                "--json",
            )
            self.assertEqual(json.loads(verification.stdout)["verification"], "passed")

    def test_live_remote_readonly_is_rejected_for_new_plans(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            manifest_path = root / "manifest.json"
            envelope_path = root / "runtime-envelope.json"
            criteria_path = root / "criteria.json"
            candidate_dir = root / "candidate"
            write_json(manifest_path, manifest())
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
                "live-remote-readonly",
                "--output-dir",
                str(candidate_dir),
                expect_success=False,
            )
            combined = f"{result.stdout}\n{result.stderr}"
            self.assertTrue(
                "live-remote-readonly" in combined
                and ("invalid choice" in combined or "ADR 0005" in combined),
                combined,
            )
            self.assertFalse(candidate_dir.exists())

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
                        "state": "draft",
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
            extra = candidate_dir / "unexpected.txt"
            extra.write_text("unexpected\n")
            os.chmod(extra, 0o600)
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

    def test_pretty_json_bytes_preserves_utf8_and_planner_encoding(self) -> None:
        payload = {"note": "café"}
        pretty = model_identity.pretty_json_bytes(payload)
        compact = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertIn("café".encode("utf-8"), pretty)
        self.assertNotIn(b"\\u00e9", pretty)
        self.assertTrue(pretty.endswith(b"\n"))
        self.assertIn(b"\n  ", pretty)
        self.assertEqual(
            model_identity.canonical_json_digest(payload),
            hashlib.sha256(compact).hexdigest(),
        )
        self.assertNotEqual(
            model_identity.canonical_json_digest(payload),
            hashlib.sha256(pretty).hexdigest(),
        )
        self.assertEqual(
            model_identity.canonical_json_digest(json.loads(pretty.decode("utf-8"))),
            model_identity.canonical_json_digest(payload),
        )

        with tempfile.TemporaryDirectory() as raw:
            dest = pathlib.Path(raw) / "candidate"
            publish_plan_candidate(dest)
            for name in (
                "candidate.json",
                "release.json",
                "validation-contract.json",
            ):
                raw_bytes = (dest / name).read_bytes()
                parsed = json.loads(raw_bytes.decode("utf-8"))
                self.assertEqual(raw_bytes, model_identity.pretty_json_bytes(parsed))

    def test_load_verified_release_plan_candidate_accepts_planner_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            dest = pathlib.Path(raw) / "candidate"
            candidate, release, contract = publish_plan_candidate(dest)
            verified = (
                model_serving_release_plan.load_verified_release_plan_candidate(dest)
            )
            self.assertEqual(
                verified.candidate["candidate_id"],
                candidate["candidate_id"],
            )
            self.assertEqual(verified.release["release_id"], release["release_id"])
            self.assertEqual(
                verified.contract["contract_id"],
                contract["contract_id"],
            )

    def test_load_verified_rejects_release_identity_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            dest = pathlib.Path(raw) / "candidate"
            _candidate, release, _contract = publish_plan_candidate(dest)
            release = copy.deepcopy(release)
            release["serving_recipe"]["engine_args"] = ["--max-model-len", "1"]
            rewrite_json(dest / "release.json", release)
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "identity mismatch",
            ):
                model_serving_release_plan.load_verified_release_plan_candidate(dest)

    def test_load_verified_rejects_candidate_cross_link_mismatch(self) -> None:
        other = "ab" * 32
        with tempfile.TemporaryDirectory() as raw:
            dest = pathlib.Path(raw) / "candidate"
            candidate, _release, _contract = publish_plan_candidate(dest)
            for field, message in (
                ("release_id", "release_id differs from release.json"),
                ("contract_id", "contract_id differs from validation-contract.json"),
            ):
                with self.subTest(field=field):
                    mutated = copy.deepcopy(candidate)
                    self.assertNotEqual(mutated[field], other)
                    mutated[field] = other
                    mutated["candidate_id"] = model_serving_release_plan.candidate_id(
                        mutated
                    )
                    rewrite_json(dest / "candidate.json", mutated)
                    with self.assertRaisesRegex(
                        model_serving_release_plan.ModelServingReleasePlanError,
                        message,
                    ):
                        model_serving_release_plan.load_verified_release_plan_candidate(
                            dest
                        )

    def test_load_verified_rejects_extra_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            extra_dir = root / "extra"
            publish_plan_candidate(extra_dir)
            unexpected = extra_dir / "unexpected.txt"
            unexpected.write_text("unexpected\n")
            os.chmod(unexpected, 0o600)
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "file set is invalid",
            ):
                model_serving_release_plan.load_verified_release_plan_candidate(
                    extra_dir
                )

            missing_dir = root / "missing"
            publish_plan_candidate(missing_dir)
            (missing_dir / "release.json").unlink()
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "file set is invalid",
            ):
                model_serving_release_plan.load_verified_release_plan_candidate(
                    missing_dir
                )

    def test_load_verified_rejects_unsafe_modes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            dest = pathlib.Path(raw) / "candidate"
            publish_plan_candidate(dest)
            os.chmod(dest, 0o755)
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "mode is not 0700",
            ):
                model_serving_release_plan.load_verified_release_plan_candidate(dest)
            os.chmod(dest, 0o700)
            os.chmod(dest / "release.json", 0o644)
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "mode is not 0600",
            ):
                model_serving_release_plan.load_verified_release_plan_candidate(dest)

    def test_load_verified_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = pathlib.Path(raw)
            real = root / "real"
            publish_plan_candidate(real)
            linked = root / "linked"
            linked.symlink_to(real)
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "must not be a symlink",
            ):
                model_serving_release_plan.load_verified_release_plan_candidate(linked)

            dest = root / "files"
            publish_plan_candidate(dest)
            outside = root / "release.json"
            target = dest / "release.json"
            target.rename(outside)
            target.symlink_to(outside)
            with self.assertRaisesRegex(
                model_serving_release_plan.ModelServingReleasePlanError,
                "must not contain a symlink",
            ):
                model_serving_release_plan.load_verified_release_plan_candidate(dest)

    def test_load_verified_rejects_replacement_and_in_read_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            dest = pathlib.Path(raw) / "candidate"
            publish_plan_candidate(dest)
            release_path = dest / "release.json"
            original = release_path.read_bytes()
            mutated = b"x" * len(original)

            def mutate(key: str | None) -> None:
                if key == "release.json":
                    release_path.write_bytes(mutated)

            immutable_descriptor_dir.READ_STABILITY_HOOK = mutate
            try:
                with self.assertRaisesRegex(
                    model_serving_release_plan.ModelServingReleasePlanError,
                    "changed during read",
                ):
                    model_serving_release_plan.load_verified_release_plan_candidate(
                        dest
                    )
            finally:
                immutable_descriptor_dir.READ_STABILITY_HOOK = None
                release_path.write_bytes(original)
                os.chmod(release_path, 0o600)

            extra = dest / "extra-after-scan.json"

            def add_extra() -> None:
                extra.write_text("{}\n", encoding="utf-8")
                os.chmod(extra, 0o600)

            immutable_descriptor_dir.VERIFY_AFTER_SCAN_HOOK = add_extra
            try:
                with self.assertRaisesRegex(
                    model_serving_release_plan.ModelServingReleasePlanError,
                    "directory entries changed",
                ):
                    model_serving_release_plan.load_verified_release_plan_candidate(
                        dest
                    )
            finally:
                immutable_descriptor_dir.VERIFY_AFTER_SCAN_HOOK = None
                if extra.exists():
                    extra.unlink()

            moved = dest.with_name(dest.name + ".original")

            def replace() -> None:
                dest.rename(moved)
                dest.mkdir()
                os.chmod(dest, 0o700)

            immutable_descriptor_dir.VERIFY_AFTER_SCAN_HOOK = replace
            try:
                with self.assertRaisesRegex(
                    model_serving_release_plan.ModelServingReleasePlanError,
                    "path no longer identifies the same directory",
                ):
                    model_serving_release_plan.load_verified_release_plan_candidate(
                        dest
                    )
            finally:
                immutable_descriptor_dir.VERIFY_AFTER_SCAN_HOOK = None
                if (
                    dest.exists()
                    and dest.is_dir()
                    and not (dest / "candidate.json").exists()
                ):
                    dest.rmdir()
                if moved.exists() and not dest.exists():
                    moved.rename(dest)


if __name__ == "__main__":
    unittest.main()
