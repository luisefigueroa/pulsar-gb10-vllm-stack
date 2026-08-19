#!/usr/bin/env python3
"""Maintainer-only assembly and verification of model release candidates.

Release candidates are deliberately untrusted. This tool can describe a
normalized profile contract, hash an exact local Hugging Face snapshot, and
assemble internally consistent candidate documents. It cannot write reviewed
repository trust roots or promote a profile.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile
from typing import Any

try:
    from scripts import model_identity, model_library
except ModuleNotFoundError:
    import model_identity  # type: ignore[no-redef]
    import model_library  # type: ignore[no-redef]


CANDIDATE_SCHEMA_VERSION = 1
CANDIDATE_KIND = "pulsar-model-release-candidate"
PLAN_KIND = "pulsar-model-release-plan"
CANDIDATE_FILES = {
    "snapshot_manifest": "snapshot-manifest.json",
    "validation_bundle": "validation-bundle.json",
    "expected_model_seal": "expected-model-seal.json",
}


class ModelReleaseError(ValueError):
    """A release-candidate operation is unsafe or incomplete."""


def fail(message: str) -> None:
    raise ModelReleaseError(message)


def load_json(path: str | pathlib.Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")


def atomic_write_json(path: pathlib.Path, value: Any) -> None:
    raw = json.dumps(value, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_from_repo(value: str, repo_root: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve(strict=False)


def display_path(path: pathlib.Path, repo_root: pathlib.Path) -> str:
    if path_within(path, repo_root):
        return path.relative_to(repo_root).as_posix()
    return str(path)


def validate_candidate_location(
    path: pathlib.Path,
    *,
    repo_root: pathlib.Path,
) -> pathlib.Path:
    path = path.resolve(strict=False)
    models_root = (repo_root / "models").resolve()
    experiment_root = (repo_root / "experiments" / "release-candidates").resolve()
    if path in {pathlib.Path("/"), repo_root, models_root, experiment_root}:
        fail(f"candidate output directory is too broad: {path}")
    if path_within(path, models_root):
        fail("release candidates cannot be written under models/")
    if path_within(path, repo_root) and not path_within(path, experiment_root):
        fail(
            "repository-local candidates must live under "
            "experiments/release-candidates/"
        )
    return path


def write_candidate_directory(
    output_dir: pathlib.Path,
    documents: dict[str, Any],
) -> None:
    if output_dir.exists() or output_dir.is_symlink():
        fail(f"candidate output already exists; refusing overwrite: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = pathlib.Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            dir=str(output_dir.parent),
        )
    )
    os.chmod(staging, 0o700)
    try:
        for name, value in documents.items():
            if pathlib.PurePosixPath(name).name != name:
                fail(f"candidate filename is unsafe: {name}")
            atomic_write_json(staging / name, value)
        os.rename(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def profile_contract_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return model_identity.build_profile_contract(
        model_id=args.model_id,
        served_name=args.served_name,
        image=args.image,
        nodes=args.nodes,
        port=args.port,
        gpu_mem_util=args.gpu_mem_util,
        engine_args=args.engine_arg,
        container_env=args.container_env,
        spec_decode_args=args.spec_decode_arg,
        recommended_spec=bool(args.recommended_spec),
        profile_purpose=args.profile_purpose,
        topology_class=args.topology_class,
        min_rails_per_pair=args.min_rails_per_pair,
        weights_gib=args.weights_gib or None,
        weights_ram_gib=args.weights_ram_gib or None,
        kv_gib=args.kv_gib or None,
        overhead_gib=args.overhead_gib or None,
        mem_min_free_gib=args.mem_min_free_gib or None,
    )


def default_candidate_path(
    repo_root: pathlib.Path,
    *,
    profile: str,
    revision: str,
    leaf: str,
) -> pathlib.Path:
    return (
        repo_root
        / "experiments"
        / "release-candidates"
        / profile
        / revision
        / leaf
    )


def require_exact_revision(revision: str) -> str:
    if model_identity.HF_COMMIT_RE.fullmatch(revision or "") is None:
        fail("release candidate revision must be an exact 40-64 hex HF commit")
    return revision


def validate_evidence(
    references: list[str],
    *,
    repo_root: pathlib.Path,
) -> list[str]:
    if not references:
        fail("release candidate requires at least one evidence reference")
    checked: list[str] = []
    for reference in references:
        pure = pathlib.PurePosixPath(reference)
        if pure.is_absolute() or ".." in pure.parts or "\\" in reference:
            fail(f"evidence must be repository-relative: {reference!r}")
        path = (repo_root / pathlib.Path(reference)).resolve()
        if not path_within(path, repo_root):
            fail(f"evidence escapes repository: {reference!r}")
        if not path.is_file() or path.is_symlink():
            fail(f"evidence is missing or not a regular file: {reference}")
        checked.append(reference)
    if len(set(checked)) != len(checked):
        fail("release candidate evidence contains duplicates")
    return checked


def load_external_artifacts(
    paths: list[str],
    *,
    repo_root: pathlib.Path,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for reference in paths:
        path = resolve_from_repo(reference, repo_root)
        if not path.is_file() or path.is_symlink():
            fail(
                "external artifact descriptor is missing or not a regular file: "
                f"{reference}"
            )
        value = load_json(path)
        if not isinstance(value, dict):
            fail(f"external artifact descriptor must be an object: {reference}")
        artifacts.append(value)
    return artifacts


def candidate_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "candidate_id"}


def candidate_id(candidate: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(candidate_identity(candidate))


def build_candidate_document(
    *,
    profile: str,
    model_id: str,
    snapshot_revision: str,
    manifest_id: str,
    bundle_id: str,
    seal_id: str,
    profile_contract_id: str,
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "kind": CANDIDATE_KIND,
        "state": "unreviewed",
        "authority": "none",
        "profile": profile,
        "model_id": model_id,
        "snapshot_revision": snapshot_revision,
        "manifest_id": manifest_id,
        "validation_bundle_id": bundle_id,
        "expected_model_seal_id": seal_id,
        "profile_contract_id": profile_contract_id,
        "files": CANDIDATE_FILES,
        "review": {
            "privacy": "pending",
            "promotion": "not-authorized",
        },
    }
    candidate["candidate_id"] = candidate_id(candidate)
    return validate_candidate_document(candidate)


def validate_candidate_document(candidate: Any) -> dict[str, Any]:
    required = {
        "schema_version",
        "kind",
        "state",
        "authority",
        "profile",
        "model_id",
        "snapshot_revision",
        "manifest_id",
        "validation_bundle_id",
        "expected_model_seal_id",
        "profile_contract_id",
        "files",
        "review",
        "candidate_id",
    }
    if not isinstance(candidate, dict) or set(candidate) != required:
        fail("release candidate descriptor fields are invalid")
    if candidate.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        fail("release candidate schema_version is unsupported")
    if candidate.get("kind") != CANDIDATE_KIND:
        fail("release candidate kind is invalid")
    if candidate.get("state") != "unreviewed" or candidate.get("authority") != "none":
        fail("release candidate cannot claim reviewed authority")
    profile = candidate.get("profile")
    if (
        not isinstance(profile, str)
        or model_identity.SAFE_REV.fullmatch(profile) is None
    ):
        fail("release candidate profile is invalid")
    model_id = candidate.get("model_id")
    if not isinstance(model_id, str) or model_identity.HF_MODEL_ID_RE.fullmatch(
        model_id
    ) is None:
        fail("release candidate model_id is invalid")
    require_exact_revision(candidate.get("snapshot_revision"))
    for field in (
        "manifest_id",
        "validation_bundle_id",
        "expected_model_seal_id",
        "profile_contract_id",
        "candidate_id",
    ):
        value = candidate.get(field)
        if not isinstance(value, str) or model_identity.SHA256_HEX_RE.fullmatch(
            value
        ) is None:
            fail(f"release candidate {field} is invalid")
    if candidate.get("files") != CANDIDATE_FILES:
        fail("release candidate file map is invalid")
    if candidate.get("review") != {
        "privacy": "pending",
        "promotion": "not-authorized",
    }:
        fail("release candidate review state is invalid")
    if candidate["candidate_id"] != candidate_id(candidate):
        fail("release candidate identity mismatch")
    return candidate


def cmd_plan(args: argparse.Namespace) -> int:
    repo_root = pathlib.Path(args.repo_root).resolve()
    contract = profile_contract_from_args(args)
    output_root = (repo_root / "experiments" / "release-candidates").resolve()
    result = {
        "schema_version": 1,
        "kind": PLAN_KIND,
        "state": "candidate-only",
        "authority": "none",
        "profile": args.profile,
        "model_id": args.model_id,
        "profile_contract": contract,
        "profile_contract_id": model_identity.canonical_json_digest(contract),
        "candidate_output_root": display_path(output_root, repo_root),
        "trusted_write_enabled": False,
        "required_inputs": [
            "exact-huggingface-commit",
            "complete-snapshot-manifest",
            "repository-evidence",
            "issuer-and-issued-at",
            "maintainer-review",
        ],
    }
    render_result(result, json_output=args.json)
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    repo_root = pathlib.Path(args.repo_root).resolve()
    revision = require_exact_revision(args.revision)
    output_dir = resolve_from_repo(
        args.output_dir
        or str(
            default_candidate_path(
                repo_root,
                profile=args.profile,
                revision=revision,
                leaf="observed",
            )
        ),
        repo_root,
    )
    output_dir = validate_candidate_location(output_dir, repo_root=repo_root)
    manifest = model_library.build_snapshot_manifest(
        args.hub_path,
        model_id=args.model_id,
        revision=revision,
        allow_empty_files=True,
    )
    manifest = model_library.validate_snapshot_manifest(manifest)
    if manifest["snapshot_revision"] != revision:
        fail("observed manifest revision differs from requested commit")
    verification = model_library.verify_snapshot_manifest(
        args.hub_path,
        manifest,
        metadata_only=False,
    )
    write_candidate_directory(
        output_dir,
        {CANDIDATE_FILES["snapshot_manifest"]: manifest},
    )
    result = {
        "schema_version": 1,
        "kind": "pulsar-model-release-observed-manifest",
        "state": "observed-unreviewed",
        "authority": "none",
        "profile": args.profile,
        "model_id": args.model_id,
        "snapshot_revision": revision,
        "manifest_id": manifest["manifest_id"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "verification_mode": verification["mode"],
        "bytes_hashed": verification["bytes_hashed"],
        "output": display_path(
            output_dir / CANDIDATE_FILES["snapshot_manifest"], repo_root
        ),
    }
    render_result(result, json_output=args.json)
    return 0


def load_release_manifest(
    path: pathlib.Path,
    *,
    model_id: str,
) -> dict[str, Any]:
    manifest = model_library.validate_snapshot_manifest(load_json(path))
    if manifest["model_id"] != model_id:
        fail("snapshot manifest model_id differs from profile")
    require_exact_revision(manifest["snapshot_revision"])
    return manifest


def cmd_assemble(args: argparse.Namespace) -> int:
    repo_root = pathlib.Path(args.repo_root).resolve()
    manifest_path = resolve_from_repo(args.manifest, repo_root)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        fail(f"snapshot manifest is missing or not a regular file: {manifest_path}")
    manifest = load_release_manifest(manifest_path, model_id=args.model_id)
    revision = manifest["snapshot_revision"]
    output_dir = resolve_from_repo(
        args.output_dir
        or str(
            default_candidate_path(
                repo_root,
                profile=args.profile,
                revision=revision,
                leaf="release-candidate",
            )
        ),
        repo_root,
    )
    output_dir = validate_candidate_location(output_dir, repo_root=repo_root)
    evidence = validate_evidence(args.evidence, repo_root=repo_root)
    contract = profile_contract_from_args(args)
    external_artifacts = load_external_artifacts(
        args.external_artifact,
        repo_root=repo_root,
    )
    primary = {
        "role": "primary",
        "model_id": args.model_id,
        "revision_kind": "huggingface-commit",
        "snapshot_revision": revision,
        "manifest": {
            "scheme": model_identity.SNAPSHOT_INTEGRITY_SCHEME,
            "manifest_id": manifest["manifest_id"],
        },
    }
    bundle = model_identity.build_validation_bundle(
        profile=args.profile,
        models=[primary],
        external_artifacts=external_artifacts,
        profile_contract=contract,
        evidence=evidence,
        issuer=args.issuer,
        issued_at=args.issued_at,
    )
    seal = model_identity.build_expected_model_seal(
        profile=args.profile,
        model_id=args.model_id,
        snapshot_revision=revision,
        manifest_id=manifest["manifest_id"],
        validation_bundle=bundle,
    )
    contract_id = model_identity.canonical_json_digest(contract)
    candidate = build_candidate_document(
        profile=args.profile,
        model_id=args.model_id,
        snapshot_revision=revision,
        manifest_id=manifest["manifest_id"],
        bundle_id=bundle["bundle_id"],
        seal_id=seal["seal_id"],
        profile_contract_id=contract_id,
    )
    write_candidate_directory(
        output_dir,
        {
            CANDIDATE_FILES["snapshot_manifest"]: manifest,
            CANDIDATE_FILES["validation_bundle"]: bundle,
            CANDIDATE_FILES["expected_model_seal"]: seal,
            "candidate.json": candidate,
        },
    )
    result = candidate_summary(candidate, output_dir=output_dir, repo_root=repo_root)
    render_result(result, json_output=args.json)
    return 0


def candidate_summary(
    candidate: dict[str, Any],
    *,
    output_dir: pathlib.Path,
    repo_root: pathlib.Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "pulsar-model-release-candidate-result",
        "ok": True,
        "state": "candidate-match",
        "authority": "none",
        "profile": candidate["profile"],
        "model_id": candidate["model_id"],
        "snapshot_revision": candidate["snapshot_revision"],
        "manifest_id": candidate["manifest_id"],
        "validation_bundle_id": candidate["validation_bundle_id"],
        "expected_model_seal_id": candidate["expected_model_seal_id"],
        "profile_contract_id": candidate["profile_contract_id"],
        "candidate_id": candidate["candidate_id"],
        "privacy_review": candidate["review"]["privacy"],
        "promotion": candidate["review"]["promotion"],
        "output": display_path(output_dir, repo_root),
    }


def cmd_verify_candidate(args: argparse.Namespace) -> int:
    repo_root = pathlib.Path(args.repo_root).resolve()
    candidate_dir = validate_candidate_location(
        resolve_from_repo(args.candidate_dir, repo_root),
        repo_root=repo_root,
    )
    if not candidate_dir.is_dir() or candidate_dir.is_symlink():
        fail(f"candidate directory is missing or unsafe: {candidate_dir}")
    expected_names = set(CANDIDATE_FILES.values()) | {"candidate.json"}
    try:
        observed_names = {item.name for item in candidate_dir.iterdir()}
    except OSError as exc:
        fail(f"cannot inspect candidate directory {candidate_dir}: {exc}")
    if observed_names != expected_names:
        fail(
            "candidate directory file set differs "
            f"(missing={sorted(expected_names - observed_names)}, "
            f"extra={sorted(observed_names - expected_names)})"
        )
    for name in expected_names:
        path = candidate_dir / name
        if not path.is_file() or path.is_symlink():
            fail(f"candidate file is missing or unsafe: {name}")

    candidate = validate_candidate_document(load_json(candidate_dir / "candidate.json"))
    if candidate["profile"] != args.profile or candidate["model_id"] != args.model_id:
        fail("release candidate identity differs from sourced profile")
    manifest = load_release_manifest(
        candidate_dir / CANDIDATE_FILES["snapshot_manifest"],
        model_id=args.model_id,
    )
    seal = model_identity.validate_expected_model_seal(
        load_json(candidate_dir / CANDIDATE_FILES["expected_model_seal"]),
        profile=args.profile,
        model_id=args.model_id,
    )
    contract = profile_contract_from_args(args)
    bundle = model_identity.validate_validation_bundle(
        load_json(candidate_dir / CANDIDATE_FILES["validation_bundle"]),
        profile=args.profile,
        expected_seal=seal,
        expected_profile_contract=contract,
    )
    cross_checks = {
        "snapshot_revision": manifest["snapshot_revision"],
        "manifest_id": manifest["manifest_id"],
        "validation_bundle_id": bundle["bundle_id"],
        "expected_model_seal_id": seal["seal_id"],
        "profile_contract_id": model_identity.canonical_json_digest(contract),
    }
    for field, observed in cross_checks.items():
        if candidate[field] != observed:
            fail(f"release candidate {field} differs from candidate documents")
    validate_evidence(bundle["evidence"], repo_root=repo_root)
    result = candidate_summary(
        candidate,
        output_dir=candidate_dir,
        repo_root=repo_root,
    )
    render_result(result, json_output=args.json)
    return 0


def render_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(f"state       {result['state']}")
    print(f"authority   {result['authority']}")
    print(f"profile     {result['profile']}")
    if "snapshot_revision" in result:
        print(f"revision    {result['snapshot_revision']}")
    if "manifest_id" in result:
        print(f"manifest    {result['manifest_id']}")
    if "validation_bundle_id" in result:
        print(f"bundle      {result['validation_bundle_id']}")
    if "expected_model_seal_id" in result:
        print(f"seal        {result['expected_model_seal_id']}")
    if "profile_contract_id" in result:
        print(f"contract    {result['profile_contract_id']}")
    if "candidate_id" in result:
        print(f"candidate   {result['candidate_id']}")
    if "output" in result:
        print(f"output      {result['output']}")
    if "candidate_output_root" in result:
        print(f"output root {result['candidate_output_root']}")
    if result.get("promotion"):
        print(f"promotion   {result['promotion']}")


def add_profile_contract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--served-name", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--gpu-mem-util", required=True)
    parser.add_argument("--engine-arg", action="append", default=[])
    parser.add_argument("--container-env", action="append", default=[])
    parser.add_argument("--spec-decode-arg", action="append", default=[])
    parser.add_argument("--recommended-spec", type=int, choices=(0, 1), required=True)
    parser.add_argument(
        "--profile-purpose",
        choices=("serving", "diagnostic"),
        required=True,
    )
    parser.add_argument("--topology-class", required=True)
    parser.add_argument("--min-rails-per-pair", type=int, required=True)
    parser.add_argument("--weights-gib", default="")
    parser.add_argument("--weights-ram-gib", default="")
    parser.add_argument("--kv-gib", default="")
    parser.add_argument("--overhead-gib", default="")
    parser.add_argument("--mem-min-free-gib", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build untrusted Pulsar model release candidates"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Show the normalized release contract")
    add_profile_contract_arguments(plan)
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    manifest = subparsers.add_parser(
        "manifest",
        help="Hash one exact local Hugging Face snapshot",
    )
    add_profile_contract_arguments(manifest)
    manifest.add_argument("--hub-path", required=True)
    manifest.add_argument("--revision", required=True)
    manifest.add_argument("--output-dir", default="")
    manifest.add_argument("--json", action="store_true")
    manifest.set_defaults(func=cmd_manifest)

    assemble = subparsers.add_parser(
        "assemble",
        help="Assemble an unreviewed seal and validation-bundle candidate",
    )
    add_profile_contract_arguments(assemble)
    assemble.add_argument("--manifest", required=True)
    assemble.add_argument("--issuer", required=True)
    assemble.add_argument("--issued-at", required=True)
    assemble.add_argument("--evidence", action="append", default=[])
    assemble.add_argument("--external-artifact", action="append", default=[])
    assemble.add_argument("--output-dir", default="")
    assemble.add_argument("--json", action="store_true")
    assemble.set_defaults(func=cmd_assemble)

    verify = subparsers.add_parser(
        "verify-candidate",
        help="Verify candidate documents against the current profile",
    )
    add_profile_contract_arguments(verify)
    verify.add_argument("--candidate-dir", required=True)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify_candidate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        ModelReleaseError,
        model_identity.ModelIdentityError,
        model_library.ModelLibraryError,
        OSError,
    ) as exc:
        print(f"model-release: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
