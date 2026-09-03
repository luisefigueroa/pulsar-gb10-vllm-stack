#!/usr/bin/env python3
"""Contracts for the ADR 0017 Stage 3 stack consumer (WP1.4a)."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from release_spec import load_spec, pretty_json_bytes, verify_spec  # noqa: E402
from scripts import model_library_receipt as source_attested  # noqa: E402
from scripts import release_consumer as consumer  # noqa: E402
from scripts import release_spec_generate as generate  # noqa: E402
from scripts.testlib.test_release_spec_generate import (  # noqa: E402
    NANO,
    PINNED_IMAGE,
    PLATFORM_ID,
    SUPER,
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
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(CLI)]
    if repo is not None:
        cmd.extend(["--repo-root", str(repo)])
    cmd.extend(args)
    env = dict(os.environ)
    env.pop("COLUMNS", None)
    env.pop("PULSAR_RELEASES_ROOT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
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


def released_nano_spec() -> dict:
    spec, report = generate.build_spec_from_profile(**nano_kwargs())
    assert spec is not None
    assert report["generated"]
    golden = golden_spec()
    document = json.loads(pretty_json_bytes(spec))
    document["state"] = "released"
    document["measurements"] = golden["measurements"]
    document["baselines"] = golden["baselines"]
    document["evidence"] = golden["evidence"]
    document["review"] = {
        "status": "stable",
        "reviewer": "example-reviewer",
        "reviewed_at": "2026-09-02T00:00:00Z",
    }
    return verify_spec(document)


def write_fixture_library(
    root: pathlib.Path,
    *,
    model_id: str,
    profile: str,
) -> tuple[pathlib.Path, pathlib.Path, dict]:
    library = root / "library"
    home = root / "durable-home"
    home.mkdir(parents=True)
    receipt = source_attested.validate_source_attested_acquisition_receipt(
        json.loads(receipt_for(model_id).read_text(encoding="utf-8"))
    )
    source_attested.write_source_attested_receipt(
        library, receipt, operation="test"
    )
    source_attested.write_source_attested_home_attachment(
        library,
        receipt=receipt,
        node_id="node-0",
        durable_home_path=str(home.resolve()),
        directory_identity={"device": 1, "inode": 1, "ctime_ns": 1},
    )
    catalog = {
        "schema_version": 2,
        "models": [
            {
                "model_id": model_id,
                "revision": receipt["snapshot_revision"],
                "identity_key": f"{model_id}@{receipt['snapshot_revision']}",
                "profiles": [profile],
                "homes": [],
            }
        ],
    }
    catalog_path = library / "catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
    )
    return library, catalog_path, receipt


def project_kwargs(
    library: pathlib.Path,
    catalog: pathlib.Path,
    *,
    profile: str = NANO,
    recommended_spec: bool = False,
    image: str | None = None,
    extra_args: tuple[str, ...] = (),
    extra_env: tuple[str, ...] = (),
    releases_root: pathlib.Path | None = None,
    repo_root: pathlib.Path | None = None,
    identity: dict[str, object] | None = None,
) -> dict[str, object]:
    values = dict(identity or nano_identity_kwargs())
    values.pop("spec_decode", None)
    values.pop("files", None)
    values.pop("snapshot_revision", None)
    values.pop("receipt_model_id", None)
    return {
        "profile": profile,
        "model_id": values["model_id"],
        "image": image if image is not None else values["image"],
        "nodes": values["nodes"],
        "gpu_mem_util": values["gpu_mem_util"],
        "engine_args": values["engine_args"],
        "container_env": values["container_env"],
        "spec_decode_args": values["spec_decode_args"],
        "platform_id": values["platform_id"],
        "recommended_spec": recommended_spec,
        "library_dir": library,
        "catalog_path": catalog,
        "releases_root": releases_root,
        "repo_root": repo_root,
        "extra_args": extra_args,
        "extra_env": extra_env,
    }


def project_cli_args(kwargs: dict[str, object]) -> list[str]:
    args = [
        "project",
        "--library-dir",
        str(kwargs["library_dir"]),
        "--catalog",
        str(kwargs.get("catalog_path") or ""),
        "--profile",
        str(kwargs["profile"]),
        "--model-id",
        str(kwargs["model_id"]),
        "--served-name",
        "nemotron-3-nano",
        "--image",
        str(kwargs["image"]),
        "--nodes",
        str(kwargs["nodes"]),
        "--port",
        "8000",
        "--gpu-mem-util",
        str(kwargs["gpu_mem_util"]),
        "--recommended-spec",
        "1" if kwargs.get("recommended_spec") else "0",
        "--platform-id",
        str(kwargs["platform_id"]),
        "--profile-purpose",
        "serving",
        "--topology-class",
        "single",
        "--min-rails-per-pair",
        "0",
    ]
    if kwargs.get("releases_root"):
        args.extend(["--releases-root", str(kwargs["releases_root"])])
    if kwargs.get("repo_root"):
        args.extend(["--repo-root", str(kwargs["repo_root"])])
    for item in kwargs.get("engine_args") or []:
        args.append(f"--engine-arg={item}")
    for item in kwargs.get("container_env") or []:
        args.append(f"--container-env={item}")
    for item in kwargs.get("spec_decode_args") or []:
        args.append(f"--spec-decode-arg={item}")
    for item in kwargs.get("extra_args") or []:
        args.append(f"--extra-arg={item}")
    for item in kwargs.get("extra_env") or []:
        args.append(f"--extra-env={item}")
    return args


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
        self.assertIn(spec["spec_id"], verified.stdout)
        self.assertRegex(verified.stdout, r"state\s+released")
        self.assertRegex(verified.stdout, r"review\s+stable since ")
        # Narrow terminals: no uncontrolled long lines (AGENTS.md CLI rule).
        for args in (["list"], ["verify", spec["spec_id"]]):
            narrow = run_cli(args, repo=repo, env_extra={"COLUMNS": "40"})
            self.assertEqual(narrow.returncode, 0, msg=narrow.stderr)
            lines = narrow.stdout.splitlines()
            self.assertTrue(lines)
            self.assertTrue(all(len(line) <= 40 for line in lines), narrow.stdout)
            self.assertIn(spec["spec_id"], "".join(line.strip() for line in lines))
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

    def test_review_text_since_only_for_stable(self) -> None:
        self.assertEqual(
            consumer._review_text("stable", "2026-09-02T00:00:00Z"),
            "stable since 2026-09-02T00:00:00Z",
        )
        self.assertEqual(
            consumer._review_text("validated", "2026-09-02T00:00:00Z"),
            "validated",
        )
        self.assertEqual(
            consumer._review_text("failed", "2026-09-02T00:00:00Z"),
            "failed",
        )
        self.assertEqual(
            consumer._review_text("withdrawn", "2026-09-02T00:00:00Z"),
            "withdrawn",
        )
        self.assertEqual(consumer._review_text(None, None), "-")

    def test_project_profile_absence_and_equal_and_hidden(self) -> None:
        root = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-project-"))
        self._tmp = root
        repo = root / "repo"
        (repo / consumer.RELEASES_DIR).mkdir(parents=True)
        missing = consumer.project_profile(
            **project_kwargs(
                pathlib.Path("/no/such/library"),
                pathlib.Path("/no/such/catalog.json"),
            )
        )
        self.assertEqual(missing["receipt"], "missing")
        self.assertEqual(missing["identities"], [])

        empty = root / "empty-lib"
        empty.mkdir()
        catalog_only = empty / "catalog.json"
        catalog_only.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "models": [
                        {
                            "model_id": "other/other",
                            "revision": "a" * 40,
                            "profiles": ["other"],
                            "homes": [],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        absent_profile = consumer.project_profile(
            **project_kwargs(empty, catalog_only)
        )
        self.assertEqual(absent_profile["receipt"], "missing")
        self.assertEqual(absent_profile["identities"], [])

        library, catalog, _receipt = write_fixture_library(
            root, model_id=nano_kwargs()["model_id"], profile=NANO
        )

        no_release = consumer.project_profile(
            **project_kwargs(library, catalog, repo_root=repo)
        )
        self.assertEqual(no_release["receipt"], "found")
        self.assertEqual(len(no_release["identities"]), 1)
        row = no_release["identities"][0]
        self.assertFalse(row["released"])
        self.assertIsNone(row["comparison"])
        self.assertIsNone(row["review_status"])
        self.assertTrue(row["spec_id"])

        spec = released_nano_spec()
        dest = repo / consumer.RELEASES_DIR / f"{spec['spec_id']}.json"
        dest.write_bytes(pretty_json_bytes(spec))
        equal = consumer.project_profile(
            **project_kwargs(library, catalog, repo_root=repo)
        )
        self.assertEqual(equal["receipt"], "found")
        equal_row = equal["identities"][0]
        self.assertTrue(equal_row["released"])
        self.assertEqual(equal_row["comparison"], "equal")
        self.assertEqual(equal_row["review_status"], "stable")
        self.assertEqual(equal_row["reviewed_at"], "2026-09-02T00:00:00Z")
        self.assertEqual(
            consumer.identity_review_cell(equal_row, receipt="found"),
            "stable since 2026-09-02T00:00:00Z",
        )

        hidden_args = consumer.project_profile(
            **project_kwargs(
                library,
                catalog,
                repo_root=repo,
                extra_args=("--enforce-eager",),
            )
        )
        hidden_row = hidden_args["identities"][0]
        self.assertEqual(hidden_row["comparison"], "differs")
        self.assertEqual(hidden_row["differs_fields"], ["argv"])
        self.assertIsNone(hidden_row["review_status"])
        self.assertEqual(equal_row["release_file"], "valid")

        # A corrupt file in the trusted registry is reported, never shown as
        # "no spec": display-only, non-gating, but not silent.
        dest.write_text("{not json", encoding="utf-8")
        invalid = consumer.project_profile(
            **project_kwargs(library, catalog, repo_root=repo)
        )
        invalid_row = invalid["identities"][0]
        self.assertEqual(invalid_row["release_file"], "invalid")
        self.assertFalse(invalid_row["released"])
        self.assertIsNone(invalid_row["comparison"])
        self.assertIsNone(invalid_row["review_status"])
        self.assertEqual(
            consumer.identity_review_cell(invalid_row, receipt="found"),
            "invalid release file (verification failed)",
        )
        measured = json.loads(pretty_json_bytes(spec))
        measured["state"] = "measured"
        measured["review"] = {}
        dest.write_bytes(pretty_json_bytes(measured))
        self.assertEqual(
            consumer.project_profile(
                **project_kwargs(library, catalog, repo_root=repo)
            )["identities"][0]["release_file"],
            "invalid",
        )
        dest.write_bytes(pretty_json_bytes(spec))

        # An attachment that binds a different receipt than the one on disk
        # (partial restore, private-state corruption) must not lend its status.
        store = source_attested.source_attested_home_attachment_store(library)
        attachment_path = next(store.glob("*.json"))
        attachment = json.loads(attachment_path.read_text(encoding="utf-8"))
        original = attachment["observed_manifest_id"]
        attachment["observed_manifest_id"] = "f" * 64
        attachment_path.write_text(json.dumps(attachment), encoding="utf-8")
        unlinked = consumer.project_profile(
            **project_kwargs(library, catalog, repo_root=repo)
        )
        self.assertEqual(unlinked, {"receipt": "unreadable", "identities": []})
        attachment["observed_manifest_id"] = original
        attachment_path.write_text(json.dumps(attachment), encoding="utf-8")

        # A batch context scans the occupancy store once for many records,
        # including the exact-revision path a refreshed catalog takes.
        calls: list[str] = []
        real_listing = source_attested.list_source_attested_home_attachments

        def counting_listing(library_dir):
            calls.append(str(library_dir))
            return real_listing(library_dir)

        source_attested.list_source_attested_home_attachments = counting_listing
        try:
            context = consumer.ProjectionContext()
            for _ in range(3):
                consumer.project_profile(
                    **project_kwargs(library, catalog, repo_root=repo), context=context
                )
        finally:
            source_attested.list_source_attested_home_attachments = real_listing
        self.assertEqual(len(calls), 1)

        # project-batch: one process, shared caches, same payload per record.
        single = consumer.project_profile(
            **project_kwargs(library, catalog, repo_root=repo)
        )
        record = project_cli_args(project_kwargs(library, catalog, repo_root=repo))[1:]
        records_path = repo / "records.txt"
        records_path.write_text(
            "\n".join(record) + "\n\n" + "\n".join(record) + "\n"
            + "\n--profile\nno-such-profile\n--library-dir\n" + str(library) + "\n",
            encoding="utf-8",
        )
        batch = run_cli(["project-batch", "--records", str(records_path)])
        self.assertEqual(batch.returncode, 0, msg=batch.stderr)
        payload = json.loads(batch.stdout)
        self.assertEqual(payload["projections"][NANO], single)
        self.assertEqual(payload["projections"]["no-such-profile"]["identities"], [])
        self.assertEqual(batch.stdout.count("\n"), 1)
        self.assertNotIn("\t", batch.stdout)
        self.assertEqual(
            consumer.identity_review_cell(hidden_row, receipt="found"),
            "hidden (launch contract differs: argv)",
        )

        hidden_env = consumer.project_profile(
            **project_kwargs(
                library,
                catalog,
                repo_root=repo,
                extra_env=("FOO=1",),
            )
        )
        env_row = hidden_env["identities"][0]
        self.assertEqual(env_row["comparison"], "differs")
        self.assertEqual(env_row["differs_fields"], ["container_env"])
        self.assertIsNone(env_row["review_status"])

        measured, report = generate.build_spec_from_profile(**nano_kwargs())
        self.assertIsNotNone(measured)
        dest.write_bytes(pretty_json_bytes(measured))
        measured_proj = consumer.project_profile(
            **project_kwargs(library, catalog, repo_root=repo)
        )
        self.assertFalse(measured_proj["identities"][0]["released"])
        self.assertIsNone(measured_proj["identities"][0]["comparison"])

        unpinned = consumer.project_profile(
            **project_kwargs(
                library,
                catalog,
                repo_root=repo,
                image="vllm/vllm-openai:v0.26.0",
            )
        )
        self.assertEqual(unpinned["receipt"], "found")
        self.assertIsNone(unpinned["identities"][0]["spec_id"])

        super_lib, super_cat, _super_receipt = write_fixture_library(
            root / "super",
            model_id="nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4",
            profile=SUPER,
        )
        off = consumer.project_profile(
            **project_kwargs(
                super_lib,
                super_cat,
                profile=SUPER,
                identity=super_identity_kwargs(),
                recommended_spec=False,
            )
        )
        self.assertEqual(len(off["identities"]), 2)
        self.assertTrue(off["identities"][0]["default"])
        self.assertFalse(off["identities"][1]["default"])
        on = consumer.project_profile(
            **project_kwargs(
                super_lib,
                super_cat,
                profile=SUPER,
                identity=super_identity_kwargs(),
                recommended_spec=True,
            )
        )
        self.assertFalse(on["identities"][0]["default"])
        self.assertTrue(on["identities"][1]["default"])

        kwargs = project_kwargs(library, catalog, repo_root=repo)
        dest.write_bytes(pretty_json_bytes(spec))
        cli = run_cli(project_cli_args(kwargs), repo=repo)
        self.assertEqual(cli.returncode, 0, msg=cli.stderr)
        payload = json.loads(cli.stdout)
        self.assertEqual(payload["receipt"], "found")
        self.assertEqual(payload["identities"][0]["comparison"], "equal")
        self.assertNotIn("\t", cli.stdout)

        missing_cli = run_cli(
            project_cli_args(
                project_kwargs(
                    pathlib.Path("/no/such/library"),
                    pathlib.Path("/no/such/catalog.json"),
                )
            ),
            repo=repo,
        )
        self.assertEqual(missing_cli.returncode, 0, msg=missing_cli.stderr)
        self.assertEqual(json.loads(missing_cli.stdout)["receipt"], "missing")

        env_cli = run_cli(
            ["list"],
            repo=repo,
            env_extra={"PULSAR_RELEASES_ROOT": str(repo / consumer.RELEASES_DIR)},
        )
        self.assertEqual(env_cli.returncode, 0, msg=env_cli.stderr)
        self.assertIn(spec["spec_id"], env_cli.stdout)

    def test_spec_profile_variables_and_plan_to_comparable(self) -> None:
        spec = released_nano_spec()
        overlay = consumer.overlay_for_spec(overlay_document(), spec)
        variables = consumer.spec_profile_variables(
            spec, overlay, "vllm/vllm-openai:v0.26.0"
        )
        self.assertEqual(variables["CONF_NAME"], spec["spec_id"])
        self.assertEqual(variables["CONF_SOURCE"], "spec")
        self.assertEqual(variables["MODEL"], spec["identity"]["model_id"])
        self.assertEqual(
            variables["IMAGE"],
            f"vllm/vllm-openai@{spec['identity']['image']['digest']}",
        )
        self.assertEqual(variables["NODES"], "1")
        self.assertEqual(variables["SERVED_NAME"], spec["identity"]["model_id"])
        self.assertEqual(variables["PORT"], "8000")
        self.assertEqual(variables["GPU_MEM_UTIL"], "0.80")
        self.assertEqual(variables["SPEC_DECODE_ARGS"], [])
        self.assertEqual(variables["RECOMMENDED_SPEC"], "0")
        self.assertEqual(variables["STATUS"], "?")
        self.assertNotIn("--gpu-memory-utilization", variables["ENGINE_ARGS"])
        manifest = spec["identity"]["snapshot_manifest"]
        self.assertEqual(variables["SPEC_MANIFEST_ID"], manifest["manifest_id"])
        for reference, repo in (
            ("vllm/vllm-openai:v0.26.0", "vllm/vllm-openai"),
            ("vllm/vllm-openai@sha256:" + "a" * 64, "vllm/vllm-openai"),
            ("registry.example:5000/team/vllm:v0.26.0", "registry.example:5000/team/vllm"),
            ("registry.example:5000/team/vllm@sha256:" + "a" * 64, "registry.example:5000/team/vllm"),
            ("", "vllm/vllm-openai"),
        ):
            self.assertEqual(consumer.image_repo_from_reference(reference), repo, reference)
        # Whole GiB rounded up, never below 1, so the memory gate sees the size.
        self.assertEqual(variables["WEIGHTS_GIB"], "1")
        big = json.loads(pretty_json_bytes(spec))
        big["identity"]["snapshot_manifest"]["total_bytes"] = 75 * 1024 ** 3 + 1
        self.assertEqual(
            consumer.spec_profile_variables(big, overlay, "vllm/vllm-openai")["WEIGHTS_GIB"],
            "76",
        )
        self.assertNotIn("--tensor-parallel-size", variables["ENGINE_ARGS"])
        self.assertEqual(variables["TOPOLOGY_CLASS"], "single")
        self.assertEqual(variables["MIN_RAILS_PER_PAIR"], "0")
        self.assertEqual(
            variables["SNAPSHOT_REVISION"],
            spec["identity"]["snapshot_revision"],
        )
        self.assertEqual(consumer.spec_lifecycle_key(spec["spec_id"]), spec["spec_id"])

        named = consumer.overlay_for_spec(
            overlay_document(
                defaults={
                    "port": 9001,
                    "served_name": "nemotron-3-nano",
                    "cache_root": "/tmp/hf-cache",
                    "placement": {"node_id": "fixture-node-0"},
                }
            ),
            spec,
        )
        named_vars = consumer.spec_profile_variables(
            spec, named, "vllm/vllm-openai@sha256:" + ("c" * 64)
        )
        self.assertEqual(named_vars["SERVED_NAME"], "nemotron-3-nano")
        self.assertEqual(named_vars["PORT"], "9001")
        self.assertEqual(named_vars["HF_CACHE"], "/tmp/hf-cache")
        self.assertEqual(named_vars["OVERLAY_PLACEMENT_NODE_ID"], "fixture-node-0")

        engine = list(variables["ENGINE_ARGS"])
        plan = {
            "nodes": 1,
            "image": variables["IMAGE"],
            "gpu_mem_util": 0.8,
            "runtime": {
                "engine_args": engine,
                "spec_decode_args": [],
                "container_env": variables["CONTAINER_ENV"],
            },
        }
        comparable = consumer.plan_to_comparable(plan)
        expected = consumer.comparable_contract_from_spec(spec)
        self.assertEqual(
            consumer.compare_contracts(comparable, expected),
            {"result": "equal", "fields": []},
        )

        two_identity = json.loads(pretty_json_bytes(spec["identity"]))
        two_identity["geometry"] = {
            **two_identity["geometry"],
            "nodes": 2,
            "tp": 2,
            "pp": 1,
            "fabric": "roce-v2",
        }
        with self.assertRaisesRegex(consumer.ReleaseConsumerError, "fabric"):
            consumer.spec_profile_variables(
                {"spec_id": spec["spec_id"], "identity": {
                    **spec["identity"],
                    "geometry": {
                        **spec["identity"]["geometry"],
                        "fabric": "roce-v2",
                    },
                }},
                overlay,
                "vllm/vllm-openai",
            )
        n2_vars = consumer.spec_profile_variables(
            {"spec_id": spec["spec_id"], "identity": two_identity},
            overlay,
            "vllm/vllm-openai",
        )
        self.assertEqual(n2_vars["NODES"], "2")
        self.assertIn("--tensor-parallel-size", n2_vars["ENGINE_ARGS"])
        self.assertEqual(n2_vars["TOPOLOGY_CLASS"], "roce-full-mesh")
        self.assertEqual(n2_vars["MIN_RAILS_PER_PAIR"], "2")

    def test_export_profile_cli_and_missing_overlay(self) -> None:
        repo = self._repo()
        spec = released_nano_spec()
        dest = repo / consumer.RELEASES_DIR / f"{spec['spec_id']}.json"
        dest.write_bytes(pretty_json_bytes(spec))
        overlay_path = repo / consumer.OVERLAY_FILENAME
        write_json(
            overlay_path,
            overlay_document(
                defaults={
                    "port": 8000,
                    "served_name": "nemotron-3-nano",
                    "cache_root": None,
                    "placement": None,
                }
            ),
        )
        result = run_cli(
            [
                "export-profile",
                spec["spec_id"],
                "--overlay",
                str(overlay_path),
                "--image-repo",
                "vllm/vllm-openai",
            ],
            repo=repo,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("CONF_SOURCE='spec'", result.stdout)
        self.assertIn(f"CONF_NAME='{spec['spec_id']}'", result.stdout)
        self.assertIn("SERVED_NAME='nemotron-3-nano'", result.stdout)
        self.assertIn("SPEC_DECODE_ARGS=()", result.stdout)

        missing = run_cli(
            [
                "export-profile",
                spec["spec_id"],
                "--overlay",
                str(repo / "absent.pulsar-overlay.json"),
            ],
            repo=repo,
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("absent.pulsar-overlay.json", missing.stderr)

        unknown = "a" * 64
        absent_spec = run_cli(["export-profile", unknown], repo=repo)
        self.assertEqual(absent_spec.returncode, 2)
        self.assertIn(f"{unknown}.json", absent_spec.stderr)


if __name__ == "__main__":
    unittest.main()
