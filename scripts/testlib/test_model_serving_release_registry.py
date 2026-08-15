#!/usr/bin/env python3
"""Contracts for the read-only ADR 0004 Model Serving Release registry."""

from __future__ import annotations

import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    model_serving_release_registry as registry,
    model_validation_evidence as evidence,
)
from scripts.testlib import (  # noqa: E402
    model_serving_release_registry_fixture as fixture,
)
from scripts.testlib import model_validation_evidence_fixture as evidence_fixture  # noqa: E402


CLI = REPO_ROOT / "scripts" / "model-serving-release-registry.sh"


class ModelServingReleaseRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-msrr-"))
        self.repo_root = self.tmpdir / "repo"
        self.registry_root = self.repo_root / "models" / "model-serving-releases"
        self.repo_root.mkdir()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _load(self) -> registry.RegistryGraph:
        return registry.load_registry(self.repo_root)

    def _cli(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            [
                str(CLI),
                *args,
                "--repo-root",
                str(self.repo_root),
            ],
            cwd=str(REPO_ROOT),
            env=merged,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_empty_tracked_store_verifies(self) -> None:
        fixture.init_registry_root(self.registry_root)
        graph = self._load()
        self.assertEqual(
            graph.counts(),
            {
                "descriptors": 0,
                "contracts": 0,
                "run_records": 0,
                "evidence_bundles": 0,
                "decisions": 0,
            },
        )
        result = self._cli("verify", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["schema_version"], registry.REGISTRY_OUTPUT_SCHEMA_VERSION
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["counts"]["descriptors"], 0)
        self.assertEqual(
            payload["registry_root"], registry.DEFAULT_REGISTRY_RELATIVE
        )
        self.assertNotIn("/tmp", result.stdout)
        self.assertNotIn(str(self.repo_root), result.stdout)

    def test_happy_path_graph_and_unique_inspection(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        graph = self._load()
        inspected = registry.inspect_release(graph, source["release"]["release_id"])
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_UNIQUE)
        self.assertEqual(inspected["inspection"]["effective_status"], "validated")
        self.assertEqual(
            inspected["inspection"]["unique_decision_id"],
            source["decision"]["decision_id"],
        )
        shown = registry.inspect_decision(
            graph, source["decision"]["decision_id"]
        )
        self.assertEqual(shown["base_status"], "validated")
        self.assertEqual(shown["effective_status"], "validated")

    def test_unknown_file_and_malformed_name_fail_closed(self) -> None:
        fixture.init_registry_root(self.registry_root)
        leftover = self.registry_root / "descriptors" / ".tmp-object.json"
        leftover.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "content-addressed"
        ):
            self._load()
        leftover.unlink()
        unknown = self.registry_root / "notes.txt"
        unknown.write_text("nope\n", encoding="utf-8")
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "not an allowed"
        ):
            self._load()

    def test_wrong_kind_and_filename_id_mismatch_fail_closed(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        release = copy.deepcopy(source["release"])
        release["kind"] = "pulsar-validation-contract"
        path = (
            self.registry_root
            / "descriptors"
            / f"{source['release']['release_id']}.json"
        )
        fixture.write_json(path, release)
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "kind"
        ):
            self._load()
        release["kind"] = source["release"]["kind"]
        fixture.write_json(
            self.registry_root / "descriptors" / f"{'a' * 64}.json", release
        )
        path.unlink()
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "does not match"
        ):
            self._load()

    def test_symlink_and_path_escape_fail_closed(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        target = self.tmpdir / "outside.json"
        target.write_text("{}\n", encoding="utf-8")
        linked = self.registry_root / "descriptors" / f"{'b' * 64}.json"
        linked.symlink_to(target)
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "symlink"
        ):
            self._load()
        linked.unlink()
        nested = self.registry_root / "descriptors" / "extra"
        nested.mkdir()
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "subdirectory"
        ):
            self._load()
        nested.rmdir()
        artifact = next(
            item
            for item in source["evidence_bundle"]["evidence_artifacts"]
            if item["visibility"] == "publishable"
        )
        artifact_path = self.repo_root.joinpath(
            *pathlib.PurePosixPath(artifact["location"]["value"]).parts
        )
        artifact_path.unlink()
        artifact_path.symlink_to(target)
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "symlink"
        ):
            self._load()

    def test_publishable_evidence_absence_and_digest_mismatch(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        artifact = next(
            item
            for item in source["evidence_bundle"]["evidence_artifacts"]
            if item["visibility"] == "publishable"
        )
        path = self.repo_root.joinpath(
            *pathlib.PurePosixPath(artifact["location"]["value"]).parts
        )
        path.unlink()
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError,
            "No such file|missing path|not a regular file",
        ):
            self._load()
        path.write_bytes(b"tampered-bytes")
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "digest mismatch"
        ):
            self._load()

    def test_protected_evidence_does_not_require_git_bytes(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        protected = next(
            item
            for item in source["evidence_bundle"]["evidence_artifacts"]
            if item["visibility"] == "protected"
        )
        self.assertEqual(
            protected["location"]["kind"], "protected-content-addressed"
        )
        locator = protected["location"]["value"].removeprefix("sha256:")
        self.assertFalse((self.repo_root / locator).exists())
        graph = self._load()
        self.assertIn(source["decision"]["decision_id"], graph.decisions)

    def test_missing_forward_link_fails_closed(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        (
            self.registry_root
            / "descriptors"
            / f"{source['release']['release_id']}.json"
        ).unlink()
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "not stored"
        ):
            self._load()

    def test_lifecycle_partial_graphs(self) -> None:
        fixture.init_registry_root(self.registry_root)
        release = evidence_fixture.build_release()
        fixture.write_release(self.registry_root, release)
        graph = self._load()
        inspected = registry.inspect_release(graph, release["release_id"])
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_NONE)
        self.assertIsNone(inspected["inspection"]["effective_status"])
        self.assertNotEqual(inspected["inspection"]["effective_status"], "untested")

        contract = evidence_fixture.build_contract(release=release)
        fixture.write_contract(self.registry_root, contract)
        graph = self._load()
        inspected = registry.inspect_release(graph, release["release_id"])
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_NONE)
        self.assertEqual(inspected["inspection"]["contract_ids"], [contract["contract_id"]])

        artifacts = evidence_fixture.build_artifacts()
        runs = evidence_fixture.build_passing_runs(
            release=release, contract=contract, artifacts=artifacts
        )
        bundle = evidence_fixture.build_bundle(
            release=release,
            contract=contract,
            artifacts=artifacts,
            run_records=runs,
        )
        for record in runs:
            fixture.write_run(self.registry_root, record)
        fixture.write_bundle(self.registry_root, bundle)
        fixture.write_publishable_artifacts(self.repo_root, artifacts)
        graph = self._load()
        self.assertEqual(len(graph.evidence_bundles), 1)
        self.assertEqual(len(graph.decisions), 0)
        inspected = registry.inspect_release(graph, release["release_id"])
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_NONE)

    def test_orphan_run_without_bundle_fails_closed(self) -> None:
        fixture.init_registry_root(self.registry_root)
        release = evidence_fixture.build_release()
        contract = evidence_fixture.build_contract(release=release)
        artifacts = evidence_fixture.build_artifacts()
        runs = evidence_fixture.build_passing_runs(
            release=release, contract=contract, artifacts=artifacts
        )
        fixture.write_release(self.registry_root, release)
        fixture.write_contract(self.registry_root, contract)
        fixture.write_run(self.registry_root, runs[0])
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "not referenced"
        ):
            self._load()

    def test_multiple_contracts_are_ambiguous(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        alternate = fixture.build_alternate_contract(source["release"])
        fixture.write_contract(self.registry_root, alternate)
        graph = self._load()
        inspected = registry.inspect_release(
            graph, source["release"]["release_id"]
        )
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_AMBIGUOUS)
        self.assertIsNone(inspected["inspection"]["effective_status"])
        self.assertIn(alternate["contract_id"], inspected["inspection"]["contract_ids"])
        result = self._cli(
            "show-release", source["release"]["release_id"], "--json"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["inspection"]["state"], registry.INSPECTION_AMBIGUOUS)
        self.assertIn("ambiguous", payload["error"])

    def test_multiple_unsuperseded_heads_are_ambiguous(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        second = evidence_fixture.build_decision(
            release=source["release"],
            contract=source["contract"],
            artifacts=source["evidence_bundle"]["evidence_artifacts"],
            run_records=source["run_records"],
            bundle=source["evidence_bundle"],
            reviewed_at="2026-08-14T16:30:00Z",
        )
        fixture.write_decision(self.registry_root, second)
        graph = self._load()
        inspected = registry.inspect_release(
            graph, source["release"]["release_id"]
        )
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_AMBIGUOUS)
        self.assertEqual(len(inspected["inspection"]["unsuperseded_decision_ids"]), 2)

    def test_supersession_projects_effective_status(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        later = evidence_fixture.build_decision(
            release=source["release"],
            contract=source["contract"],
            artifacts=source["evidence_bundle"]["evidence_artifacts"],
            run_records=source["run_records"],
            bundle=source["evidence_bundle"],
            supersedes=[source["decision"]],
            reviewed_at="2026-08-14T16:00:00Z",
        )
        fixture.write_decision(self.registry_root, later)
        graph = self._load()
        inspected = registry.inspect_release(
            graph, source["release"]["release_id"]
        )
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_UNIQUE)
        self.assertEqual(inspected["inspection"]["unique_decision_id"], later["decision_id"])
        earlier = registry.inspect_decision(
            graph, source["decision"]["decision_id"]
        )
        self.assertEqual(earlier["base_status"], "validated")
        self.assertEqual(earlier["effective_status"], "superseded")
        self.assertEqual(
            earlier["superseded_by_decision_ids"], [later["decision_id"]]
        )

    def test_predecessor_complete_and_incomplete_lineage(self) -> None:
        fixture.init_registry_root(self.registry_root)
        current_release = evidence_fixture.build_release()
        predecessor = evidence_fixture.build_superseding_predecessor_source()
        contract = evidence_fixture.build_relative_contract(
            release=current_release, predecessor_source=predecessor
        )
        artifacts = evidence_fixture.build_artifacts()
        runs = evidence_fixture.build_passing_runs(
            release=current_release, contract=contract, artifacts=artifacts
        )
        bundle = evidence_fixture.build_bundle(
            release=current_release,
            contract=contract,
            artifacts=artifacts,
            run_records=runs,
        )
        decision = evidence_fixture.build_decision(
            release=current_release,
            contract=contract,
            artifacts=artifacts,
            run_records=runs,
            bundle=bundle,
            predecessor_registry=[predecessor],
        )
        current = evidence_fixture.evidence_source(
            release=current_release,
            contract=contract,
            bundle=bundle,
            run_records=runs,
            decision=decision,
        )
        fixture.write_source_objects(self.registry_root, self.repo_root, current)
        fixture.write_source_objects(
            self.registry_root, self.repo_root, predecessor
        )
        graph = self._load()
        self.assertIn(decision["decision_id"], graph.decisions)

        prior_id = predecessor["prior_decision_sources"][0]["decision"]["decision_id"]
        (
            self.registry_root / "decisions" / f"{prior_id}.json"
        ).unlink()
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError,
            "superseded decision|prior-decision|not stored",
        ):
            self._load()

    def test_review_metadata_still_enforced_on_stored_decision(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        tampered = copy.deepcopy(source["decision"])
        tampered["review"]["review_reference"] = "looks good to me"
        tampered["decision_id"] = evidence.validation_decision_id(tampered)
        (
            self.registry_root
            / "decisions"
            / f"{source['decision']['decision_id']}.json"
        ).unlink()
        fixture.write_decision(self.registry_root, tampered)
        with self.assertRaisesRegex(
            (registry.ModelServingReleaseRegistryError, evidence.ModelValidationEvidenceError),
            "review_reference",
        ):
            self._load()

    def test_backdated_supersession_fails_closed(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        later = evidence_fixture.build_decision(
            release=source["release"],
            contract=source["contract"],
            artifacts=source["evidence_bundle"]["evidence_artifacts"],
            run_records=source["run_records"],
            bundle=source["evidence_bundle"],
            supersedes=[source["decision"]],
            reviewed_at="2026-08-14T16:00:00Z",
        )
        later["review"]["reviewed_at"] = "2026-08-14T14:45:00Z"
        later["decision_id"] = evidence.validation_decision_id(later)
        fixture.write_decision(self.registry_root, later)
        with self.assertRaisesRegex(
            (
                registry.ModelServingReleaseRegistryError,
                evidence.ModelValidationEvidenceError,
            ),
            "strictly later",
        ):
            self._load()

    def test_tracked_empty_store_verifies_from_repo(self) -> None:
        graph = registry.load_registry(REPO_ROOT)
        self.assertEqual(sum(graph.counts().values()), 0)
        result = subprocess.run(
            [str(CLI), "verify", "--json"],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["counts"]["decisions"], 0)
        self.assertEqual(
            payload["registry_root"], registry.DEFAULT_REGISTRY_RELATIVE
        )
        self.assertNotIn("/tmp", result.stdout)
        self.assertNotIn("/home/", result.stdout)

    def test_cli_human_output_wraps_at_narrow_width(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        commands = (
            ("verify",),
            ("show-release", source["release"]["release_id"]),
            ("show-decision", source["decision"]["decision_id"]),
        )
        for command in commands:
            with self.subTest(command=command[0]):
                result = self._cli(*command, env={"COLUMNS": "40"})
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = [line for line in result.stdout.splitlines() if line]
                self.assertTrue(lines)
                self.assertLessEqual(max(len(line) for line in lines), 40)
                self.assertIn("does not capture", result.stdout)
                if command[0] == "verify":
                    self.assertIn(
                        registry.DEFAULT_REGISTRY_RELATIVE, result.stdout
                    )

    def test_cli_json_error_for_unknown_release(self) -> None:
        fixture.init_registry_root(self.registry_root)
        missing = "a" * 64
        result = self._cli("show-release", missing, "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["schema_version"], registry.REGISTRY_OUTPUT_SCHEMA_VERSION
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "show-release")
        self.assertIn("not stored", payload["error"])

    def test_no_reviewed_decision_json_is_neutral(self) -> None:
        fixture.init_registry_root(self.registry_root)
        release = evidence_fixture.build_release()
        fixture.write_release(self.registry_root, release)
        result = self._cli("show-release", release["release_id"], "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["inspection"]["state"], registry.INSPECTION_NONE)
        self.assertIsNone(payload["inspection"]["effective_status"])
        self.assertIsNone(payload["inspection"]["effective_status_label"])
        self.assertNotEqual(payload["inspection"]["state"], "untested")
        self.assertIn("not Untested", payload["notes"][0])

    def test_relative_contract_validates_predecessor_without_current_decision(
        self,
    ) -> None:
        fixture.init_registry_root(self.registry_root)
        current_release = evidence_fixture.build_release()
        predecessor = evidence_fixture.build_superseding_predecessor_source()
        contract = evidence_fixture.build_relative_contract(
            release=current_release, predecessor_source=predecessor
        )
        fixture.write_release(self.registry_root, current_release)
        fixture.write_contract(self.registry_root, contract)
        fixture.write_source_objects(
            self.registry_root, self.repo_root, predecessor
        )
        graph = self._load()
        self.assertIn(predecessor["decision"]["decision_id"], graph.decisions)
        self.assertNotIn(
            current_release["release_id"],
            {item["release_id"] for item in graph.decisions.values()},
        )

    def test_relative_contract_without_predecessor_objects_fails_closed(
        self,
    ) -> None:
        fixture.init_registry_root(self.registry_root)
        current_release = evidence_fixture.build_release()
        predecessor = evidence_fixture.build_predecessor_source()
        contract = evidence_fixture.build_relative_contract(
            release=current_release, predecessor_source=predecessor
        )
        fixture.write_release(self.registry_root, current_release)
        fixture.write_contract(self.registry_root, contract)
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "not stored"
        ):
            self._load()

    def test_multiple_direct_superseders_fail_verify(self) -> None:
        source = fixture.populate_happy_registry(
            self.registry_root, self.repo_root
        )
        first = evidence_fixture.build_decision(
            release=source["release"],
            contract=source["contract"],
            artifacts=source["evidence_bundle"]["evidence_artifacts"],
            run_records=source["run_records"],
            bundle=source["evidence_bundle"],
            supersedes=[source["decision"]],
            reviewed_at="2026-08-14T16:00:00Z",
        )
        second = evidence_fixture.build_decision(
            release=source["release"],
            contract=source["contract"],
            artifacts=source["evidence_bundle"]["evidence_artifacts"],
            run_records=source["run_records"],
            bundle=source["evidence_bundle"],
            supersedes=[source["decision"]],
            reviewed_at="2026-08-14T16:30:00Z",
        )
        fixture.write_decision(self.registry_root, first)
        fixture.write_decision(self.registry_root, second)
        with self.assertRaisesRegex(
            (
                registry.ModelServingReleaseRegistryError,
                evidence.ModelValidationEvidenceError,
            ),
            "more than one later decision directly supersedes",
        ):
            self._load()

    def test_strict_json_rejects_duplicate_nan_and_invalid_utf8(self) -> None:
        fixture.init_registry_root(self.registry_root)
        release = evidence_fixture.build_release()
        fixture.write_release(self.registry_root, release)
        path = (
            self.registry_root / "descriptors" / f"{release['release_id']}.json"
        )
        valid = path.read_text(encoding="utf-8")
        path.write_text(
            valid.replace(
                '"schema_version": 1',
                '"schema_version": 1, "schema_version": 2',
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "duplicate JSON object key"
        ):
            self._load()
        result = self._cli("verify", "--json")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIn("duplicate JSON object key", payload["error"])
        self.assertNotIn(str(self.repo_root), result.stdout)
        path.write_text(
            valid.replace('"schema_version": 1', '"schema_version": NaN', 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError,
            "non-standard constant|not JSON compliant|NaN",
        ):
            self._load()
        path.write_text(
            valid.replace('"schema_version": 1', '"schema_version": Infinity', 1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError,
            "non-standard constant|not JSON compliant|Infinity",
        ):
            self._load()
        path.write_bytes(b"\xff\xfe{" + b"a" * 20)
        with self.assertRaisesRegex(
            registry.ModelServingReleaseRegistryError, "invalid UTF-8"
        ):
            self._load()

    def test_cli_rejects_registry_root_override(self) -> None:
        fixture.init_registry_root(self.registry_root)
        result = self._cli("verify", "--registry-root", str(self.registry_root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
