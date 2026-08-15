#!/usr/bin/env python3
"""Build and verify unreviewed ADR-0004 release-planning candidates.

This module turns one sourced profile, one complete content manifest, one
explicit runtime/hardware envelope, and frozen criteria into the pure release
and Validation Contract objects. It cannot issue a decision, change status,
or write the trusted registry.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

try:
    from scripts import (
        immutable_descriptor_dir,
        model_identity,
        model_library,
        model_serving_release,
        terminal_format,
    )
except ModuleNotFoundError:
    import immutable_descriptor_dir  # type: ignore[no-redef]
    import model_identity  # type: ignore[no-redef]
    import model_library  # type: ignore[no-redef]
    import model_serving_release  # type: ignore[no-redef]
    import terminal_format  # type: ignore[no-redef]


CANDIDATE_SCHEMA_VERSION = 1
CANDIDATE_KIND = "pulsar-model-serving-release-plan-candidate"
ENVELOPE_SCHEMA_VERSION = 1
ENVELOPE_KIND = "pulsar-model-serving-release-runtime-envelope"
CANDIDATE_FILES = {
    "release": "release.json",
    "validation_contract": "validation-contract.json",
}
STRUCTURED_PROFILE_FLAGS = {
    "--gpu-memory-utilization",
    "--pipeline-parallel-size",
    "--tensor-parallel-size",
    "-pp",
    "-tp",
}


class ModelServingReleasePlanError(ValueError):
    """The requested release plan is unsafe, ambiguous, or inconsistent."""


def fail(message: str) -> None:
    raise ModelServingReleasePlanError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            fail(f"JSON object contains duplicate key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    fail(f"JSON contains unsupported constant {value}")


def load_json(path: str | pathlib.Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(
                handle,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")


def atomic_write_json(path: pathlib.Path, value: Any) -> None:
    raw = model_identity.pretty_json_bytes(value)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=False,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
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


def validate_candidate_location(
    path: pathlib.Path,
    *,
    repo_root: pathlib.Path,
) -> pathlib.Path:
    path = path.resolve(strict=False)
    trusted_root = (repo_root / "models" / "model-serving-releases").resolve()
    experiment_root = (repo_root / "experiments" / "model-onboarding").resolve()
    if path in {pathlib.Path("/"), repo_root, trusted_root, experiment_root}:
        fail(f"candidate output directory is too broad: {path}")
    if path_within(path, (repo_root / "models").resolve()):
        fail("release-plan candidates cannot be written under models/")
    if path_within(path, repo_root) and not path_within(path, experiment_root):
        fail(
            "repository-local candidates must live under "
            "experiments/model-onboarding/"
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
        ordered_names = [name for name in documents if name != "candidate.json"]
        if "candidate.json" in documents:
            ordered_names.append("candidate.json")
        for name in ordered_names:
            if pathlib.PurePosixPath(name).name != name:
                fail(f"candidate filename is unsafe: {name}")
            atomic_write_json(staging / name, documents[name])
        os.rename(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _required_fields(
    value: Any,
    fields: set[str],
    *,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        fail(f"{label} fields are invalid")
    return value


def validate_runtime_envelope(value: Any) -> dict[str, Any]:
    envelope = _required_fields(
        value,
        {
            "schema_version",
            "kind",
            "runtime_image_identity",
            "supported_hardware_geometry",
        },
        label="runtime envelope",
    )
    if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
        fail("runtime envelope schema_version is unsupported")
    if envelope.get("kind") != ENVELOPE_KIND:
        fail("runtime envelope kind is invalid")
    model_serving_release.validate_runtime_image_identity(
        envelope.get("runtime_image_identity")
    )
    model_serving_release.validate_supported_hardware_geometry(
        envelope.get("supported_hardware_geometry")
    )
    return envelope


def validate_criteria_input(value: Any) -> dict[str, Any]:
    return _required_fields(
        value,
        {
            "criteria",
            "context_requirement",
            "soak_requirement",
            "relative_performance",
        },
        label="criteria input",
    )


def _profile_parallelism(engine_args: list[str]) -> tuple[int, int, list[str]]:
    values = {"--tensor-parallel-size": 1, "--pipeline-parallel-size": 1}
    seen: set[str] = set()
    remaining: list[str] = []
    index = 0
    while index < len(engine_args):
        item = engine_args[index]
        matched = next(
            (
                flag
                for flag in STRUCTURED_PROFILE_FLAGS
                if item == flag or item.startswith(flag + "=")
            ),
            None,
        )
        if matched is None:
            remaining.append(item)
            index += 1
            continue
        canonical = {
            "-tp": "--tensor-parallel-size",
            "-pp": "--pipeline-parallel-size",
        }.get(matched, matched)
        if canonical in seen:
            fail(f"profile repeats structured engine argument {canonical}")
        seen.add(canonical)
        if "=" in item:
            raw = item.split("=", 1)[1]
        else:
            index += 1
            if index >= len(engine_args):
                fail(f"profile {item} requires a value")
            raw = engine_args[index]
        if canonical in values:
            try:
                parsed = int(raw)
            except ValueError:
                fail(f"profile {item} must be an integer")
            if parsed < 1:
                fail(f"profile {item} must be positive")
            values[canonical] = parsed
        elif canonical == "--gpu-memory-utilization":
            fail("profile engine_args duplicate GPU_MEM_UTIL")
        index += 1
    return (
        values["--tensor-parallel-size"],
        values["--pipeline-parallel-size"],
        remaining,
    )


def _profile_image_digest(image: str) -> str:
    match = model_identity.IMAGE_DIGEST_RE.search(image or "")
    if match is None:
        fail("profile image must be pinned by @sha256 digest")
    return "sha256:" + match.group(1)


def _primary_artifact(
    *,
    source_kind: str,
    profile_model_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    manifest = model_library.validate_snapshot_manifest(manifest)
    manifest_id = manifest["manifest_id"]
    if source_kind == "hf":
        if manifest["model_id"] != profile_model_id:
            fail("Hugging Face manifest model_id differs from the profile")
        revision = manifest["snapshot_revision"]
        if model_identity.HF_COMMIT_RE.fullmatch(revision) is None:
            fail("Hugging Face manifest revision must be an exact commit")
        return {
            "artifact_key": "primary",
            "kind": "huggingface-snapshot",
            "model_id": profile_model_id,
            "revision_kind": "huggingface-commit",
            "snapshot_revision": revision,
            "manifest": {
                "scheme": model_identity.SNAPSHOT_INTEGRITY_SCHEME,
                "manifest_id": manifest_id,
            },
        }
    if source_kind != "content-addressed":
        fail("profile source kind is unsupported")
    model_serving_release.validate_public_string_value(
        manifest["model_id"], label="content-addressed model artifact_id"
    )
    model_serving_release.validate_public_string_value(
        manifest["snapshot_revision"],
        label="content-addressed model revision",
    )
    return {
        "artifact_key": "primary",
        "kind": "content-addressed-model",
        "artifact_id": manifest["model_id"],
        "revision": manifest["snapshot_revision"],
        "manifest": {
            "scheme": model_identity.SNAPSHOT_INTEGRITY_SCHEME,
            "manifest_id": manifest_id,
        },
    }


def _additional_artifacts_and_bindings(
    artifact_paths: list[str],
    binding_values: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    artifacts: list[dict[str, Any]] = []
    for path in artifact_paths:
        artifact = load_json(path)
        if not isinstance(artifact, dict):
            fail(f"additional artifact must be an object: {path}")
        artifacts.append(artifact)
    bindings: list[dict[str, str]] = []
    for raw in binding_values:
        artifact_key, separator, use = raw.partition("=")
        if not separator or not artifact_key or not use:
            fail("artifact bindings must use ARTIFACT_KEY=USE")
        if artifact_key == "primary" or use == "primary-model":
            fail("the planner owns the one primary-model binding")
        bindings.append({"artifact_key": artifact_key, "use": use})
    artifact_keys = [item.get("artifact_key") for item in artifacts]
    binding_keys = [item["artifact_key"] for item in bindings]
    if any(not isinstance(item, str) for item in artifact_keys):
        fail("additional artifact_key must be a string")
    if (
        set(artifact_keys) != set(binding_keys)
        or len(artifact_keys) != len(set(artifact_keys))
        or len(binding_keys) != len(set(binding_keys))
    ):
        fail("each additional artifact requires exactly one artifact binding")
    return artifacts, bindings


def _artifact_reference_map(
    values: list[str],
    *,
    artifact_keys: set[str],
) -> dict[str, str]:
    references: dict[str, str] = {}
    source_values: set[str] = set()
    for raw in values:
        artifact_key, separator, source_value = raw.partition("=")
        if not separator or not artifact_key or not source_value:
            fail("artifact references must use ARTIFACT_KEY=PROFILE_REFERENCE")
        if artifact_key not in artifact_keys:
            fail("artifact reference names an unknown additional artifact")
        if artifact_key in references or source_value in source_values:
            fail("artifact references must use unique keys and profile values")
        references[artifact_key] = source_value
        source_values.add(source_value)
    return references


def _replace_json_references(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_json_references(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_json_references(item, replacements)
            for key, item in value.items()
        }
    return value


def _normalize_profile_argument(
    value: str,
    *,
    replacements: dict[str, str],
) -> str:
    if value in replacements:
        return replacements[value]
    flag, separator, flag_value = value.partition("=")
    if separator and flag_value in replacements:
        return f"{flag}={replacements[flag_value]}"
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    normalized = _replace_json_references(decoded, replacements)
    if normalized == decoded:
        return value
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _normalize_profile_arguments(
    values: list[str],
    *,
    reference_map: dict[str, str],
) -> list[str]:
    replacements = {source: key for key, source in reference_map.items()}
    return [
        _normalize_profile_argument(value, replacements=replacements)
        for value in values
    ]


def _json_contains_reference(value: Any, source_value: str) -> bool:
    if isinstance(value, str):
        return value == source_value
    if isinstance(value, list):
        return any(_json_contains_reference(item, source_value) for item in value)
    if isinstance(value, dict):
        return any(
            _json_contains_reference(item, source_value)
            for item in value.values()
        )
    return False


def _argument_contains_reference(value: str, source_value: str) -> bool:
    if value == source_value or value.partition("=")[2] == source_value:
        return True
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return False
    return _json_contains_reference(decoded, source_value)


def _validate_reference_usage(
    reference_map: dict[str, str],
    *,
    argument_groups: tuple[list[str], ...],
) -> None:
    for source_value in reference_map.values():
        if not any(
            _argument_contains_reference(argument, source_value)
            for group in argument_groups
            for argument in group
        ):
            fail("artifact reference does not match a profile argument")


def _validate_profile_envelope(
    args: argparse.Namespace,
    *,
    runtime: dict[str, Any],
    geometry: dict[str, Any],
    tensor_parallel_size: int,
    pipeline_parallel_size: int,
) -> None:
    if runtime["image"]["digest"] != _profile_image_digest(args.image):
        fail("runtime envelope image digest differs from the profile")
    expected = {
        "node_count": args.nodes,
        "accelerator_count": args.nodes,
        "tensor_parallel_size": tensor_parallel_size,
        "pipeline_parallel_size": pipeline_parallel_size,
        "topology_class": args.topology_class,
        "minimum_rails_per_pair": args.min_rails_per_pair,
    }
    for field, value in expected.items():
        if geometry[field] != value:
            fail(f"runtime envelope geometry {field} differs from the profile")
    if geometry["accelerators_per_node"] != 1:
        fail("Pulsar profiles currently require one accelerator per node")


def build_release_and_contract(
    args: argparse.Namespace,
    *,
    manifest: dict[str, Any],
    envelope: dict[str, Any],
    criteria_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    primary = _primary_artifact(
        source_kind=args.source_kind,
        profile_model_id=args.model_id,
        manifest=manifest,
    )
    additional_artifacts, additional_bindings = _additional_artifacts_and_bindings(
        args.artifact,
        args.artifact_binding,
    )
    reference_map = _artifact_reference_map(
        args.artifact_reference,
        artifact_keys={item["artifact_key"] for item in additional_artifacts},
    )
    _validate_reference_usage(
        reference_map,
        argument_groups=(args.engine_arg, args.container_env, args.spec_decode_arg),
    )
    artifact_set = model_serving_release.build_model_artifact_set(
        [primary, *additional_artifacts]
    )
    normalized_engine_args = _normalize_profile_arguments(
        args.engine_arg,
        reference_map=reference_map,
    )
    normalized_container_env = _normalize_profile_arguments(
        args.container_env,
        reference_map=reference_map,
    )
    normalized_spec_decode_args = _normalize_profile_arguments(
        args.spec_decode_arg,
        reference_map=reference_map,
    )
    tp, pp, remaining_engine_args = _profile_parallelism(normalized_engine_args)
    runtime = copy.deepcopy(envelope["runtime_image_identity"])
    geometry = copy.deepcopy(envelope["supported_hardware_geometry"])
    _validate_profile_envelope(
        args,
        runtime=runtime,
        geometry=geometry,
        tensor_parallel_size=tp,
        pipeline_parallel_size=pp,
    )
    recipe = model_serving_release.build_serving_recipe(
        artifact_bindings=[
            {"artifact_key": "primary", "use": "primary-model"},
            *additional_bindings,
        ],
        engine_args=remaining_engine_args,
        container_env=normalized_container_env,
        gpu_memory_utilization=args.gpu_mem_util,
        spec_decode_args=normalized_spec_decode_args,
        spec_decode_enabled_by_default=bool(args.recommended_spec),
        model_access_contract=args.model_access_contract,
        tensor_parallel_size=tp,
        pipeline_parallel_size=pp,
        weights_ram_gib=args.weights_ram_gib or None,
        kv_gib=args.kv_gib or None,
        overhead_gib=args.overhead_gib or None,
        mem_min_free_gib=args.mem_min_free_gib or None,
    )
    release = model_serving_release.build_model_serving_release(
        model_artifact_set=artifact_set,
        serving_recipe=recipe,
        runtime_image_identity=runtime,
        supported_hardware_geometry=geometry,
    )
    contract = model_serving_release.build_validation_contract(
        release=release,
        criteria=criteria_input["criteria"],
        context_requirement=criteria_input["context_requirement"],
        soak_requirement=criteria_input["soak_requirement"],
        relative_performance=criteria_input["relative_performance"],
    )
    return release, contract


def candidate_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidate.items() if key != "candidate_id"}


def candidate_id(candidate: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(candidate_identity(candidate))


def build_candidate_document(
    *,
    profile: str,
    source_kind: str,
    release: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "kind": CANDIDATE_KIND,
        "state": "unreviewed",
        "authority": "none",
        "profile": profile,
        "source_kind": source_kind,
        "release_id": release["release_id"],
        "contract_id": contract["contract_id"],
        "files": CANDIDATE_FILES,
        "review": {"privacy": "pending", "promotion": "not-authorized"},
    }
    candidate["candidate_id"] = candidate_id(candidate)
    return validate_candidate_document(candidate)


def validate_candidate_document(candidate: Any) -> dict[str, Any]:
    candidate = _required_fields(
        candidate,
        {
            "schema_version",
            "kind",
            "state",
            "authority",
            "profile",
            "source_kind",
            "release_id",
            "contract_id",
            "files",
            "review",
            "candidate_id",
        },
        label="release-plan candidate",
    )
    if candidate.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        fail("release-plan candidate schema_version is unsupported")
    if candidate.get("kind") != CANDIDATE_KIND:
        fail("release-plan candidate kind is invalid")
    if candidate.get("state") != "unreviewed" or candidate.get("authority") != "none":
        fail("release-plan candidate cannot claim reviewed authority")
    profile = candidate.get("profile")
    if (
        not isinstance(profile, str)
        or model_identity.SAFE_REV.fullmatch(profile) is None
    ):
        fail("release-plan candidate profile is invalid")
    if candidate.get("source_kind") not in {"hf", "content-addressed"}:
        fail("release-plan candidate source_kind is invalid")
    for field in ("release_id", "contract_id", "candidate_id"):
        value = candidate.get(field)
        if (
            not isinstance(value, str)
            or model_identity.SHA256_HEX_RE.fullmatch(value) is None
        ):
            fail(f"release-plan candidate {field} is invalid")
    if candidate.get("files") != CANDIDATE_FILES:
        fail("release-plan candidate file map is invalid")
    if candidate.get("review") != {
        "privacy": "pending",
        "promotion": "not-authorized",
    }:
        fail("release-plan candidate review state is invalid")
    if candidate["candidate_id"] != candidate_id(candidate):
        fail("release-plan candidate identity mismatch")
    return candidate


@dataclass(frozen=True)
class VerifiedReleasePlanCandidate:
    candidate: dict[str, Any]
    release: dict[str, Any]
    contract: dict[str, Any]


def load_verified_release_plan_candidate(
    candidate_dir: str | pathlib.Path,
) -> VerifiedReleasePlanCandidate:
    """Load a hardened, schema-validated unreviewed release-plan candidate.

    Shared filesystem primitives enforce the immutable directory. This module
    still owns candidate, release, and contract schema validation.
    """
    dest = pathlib.Path(candidate_dir)
    if not dest.is_absolute():
        dest = pathlib.Path.cwd() / dest
    dest_fd = None
    snapshot = None
    try:
        dest_fd, snapshot = immutable_descriptor_dir.open_and_scan_immutable_directory(
            dest,
            allowed_subdirs=set(),
            label="release-plan candidate",
        )
        expected_files = {"candidate.json", *CANDIDATE_FILES.values()}
        observed = snapshot.relative_names()
        if observed != expected_files:
            fail("candidate directory file set is invalid")
        candidate = validate_candidate_document(
            immutable_descriptor_dir.parse_strict_json(
                snapshot.file_bytes("candidate.json"),
                label="release-plan candidate.json",
            )
        )
        release = model_serving_release.validate_model_serving_release(
            immutable_descriptor_dir.parse_strict_json(
                snapshot.file_bytes(CANDIDATE_FILES["release"]),
                label="release-plan release.json",
            )
        )
        contract = model_serving_release.validate_validation_contract(
            immutable_descriptor_dir.parse_strict_json(
                snapshot.file_bytes(CANDIDATE_FILES["validation_contract"]),
                label="release-plan validation-contract.json",
            ),
            expected_release=release,
        )
        if candidate["release_id"] != release["release_id"]:
            fail("release-plan candidate release_id differs from release.json")
        if candidate["contract_id"] != contract["contract_id"]:
            fail(
                "release-plan candidate contract_id differs from "
                "validation-contract.json"
            )
        if candidate["files"] != CANDIDATE_FILES:
            fail("release-plan candidate file map is invalid")
        immutable_descriptor_dir.recheck_immutable_directory(
            dest_fd,
            snapshot,
            immutable_descriptor_dir.safe_absolute(
                dest, label="release-plan candidate directory"
            ),
            label="release-plan candidate",
        )
        return VerifiedReleasePlanCandidate(
            candidate=candidate,
            release=release,
            contract=contract,
        )
    except immutable_descriptor_dir.ImmutableDescriptorDirectoryError as exc:
        fail(str(exc))
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))
    finally:
        if snapshot is not None:
            snapshot.close()
        immutable_descriptor_dir.close_quietly(dest_fd)


def _load_candidate_directory(
    candidate_dir: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    verified = load_verified_release_plan_candidate(candidate_dir)
    return verified.candidate, verified.release, verified.contract


def _verify_current_profile(args: argparse.Namespace, release: dict[str, Any]) -> None:
    if release["runtime_image_identity"]["image"]["digest"] != _profile_image_digest(
        args.image
    ):
        fail("stored release image digest differs from the current profile")
    recipe = release["serving_recipe"]
    artifact_keys = {
        item["artifact_key"]
        for item in release["model_artifact_set"]["artifacts"]
        if item["artifact_key"] != "primary"
    }
    reference_map = _artifact_reference_map(
        args.artifact_reference,
        artifact_keys=artifact_keys,
    )
    _validate_reference_usage(
        reference_map,
        argument_groups=(args.engine_arg, args.container_env, args.spec_decode_arg),
    )
    normalized_engine_args = _normalize_profile_arguments(
        args.engine_arg,
        reference_map=reference_map,
    )
    normalized_container_env = _normalize_profile_arguments(
        args.container_env,
        reference_map=reference_map,
    )
    normalized_spec_decode_args = _normalize_profile_arguments(
        args.spec_decode_arg,
        reference_map=reference_map,
    )
    tp, pp, remaining = _profile_parallelism(normalized_engine_args)
    rebuilt_recipe = model_serving_release.build_serving_recipe(
        artifact_bindings=recipe["artifact_bindings"],
        engine_args=remaining,
        container_env=sorted(normalized_container_env),
        gpu_memory_utilization=args.gpu_mem_util,
        spec_decode_args=normalized_spec_decode_args,
        spec_decode_enabled_by_default=bool(args.recommended_spec),
        model_access_contract=args.model_access_contract,
        tensor_parallel_size=tp,
        pipeline_parallel_size=pp,
        weights_ram_gib=args.weights_ram_gib or None,
        kv_gib=args.kv_gib or None,
        overhead_gib=args.overhead_gib or None,
        mem_min_free_gib=args.mem_min_free_gib or None,
    )
    if rebuilt_recipe != recipe:
        fail("stored release serving recipe differs from the current profile")
    geometry = release["supported_hardware_geometry"]
    expected_geometry = {
        "node_count": args.nodes,
        "accelerator_count": args.nodes,
        "tensor_parallel_size": tp,
        "pipeline_parallel_size": pp,
        "topology_class": args.topology_class,
        "minimum_rails_per_pair": args.min_rails_per_pair,
    }
    for field, value in expected_geometry.items():
        if geometry[field] != value:
            fail(f"stored release geometry {field} differs from the current profile")
    primary_key = next(
        item["artifact_key"]
        for item in recipe["artifact_bindings"]
        if item["use"] == "primary-model"
    )
    artifacts = {
        item["artifact_key"]: item
        for item in release["model_artifact_set"]["artifacts"]
    }
    primary = artifacts[primary_key]
    if args.source_kind == "hf" and (
        primary["kind"] != "huggingface-snapshot"
        or primary["model_id"] != args.model_id
    ):
        fail("stored primary model differs from the current Hugging Face profile")
    if (
        args.source_kind == "content-addressed"
        and primary["kind"] != "content-addressed-model"
    ):
        fail("stored primary model is not source-neutral content-addressed identity")


def render_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    writer = terminal_format.TerminalWriter()
    writer.emit("ADR 0004 release-plan candidate")
    writer.blank()
    for label, key in (
        ("State", "state"),
        ("Authority", "authority"),
        ("Profile", "profile"),
        ("Release", "release_id"),
        ("Contract", "contract_id"),
        ("Candidate", "candidate_id"),
        ("Output", "output"),
        ("Verification", "verification"),
        ("Promotion", "promotion"),
    ):
        if key in result:
            writer.field(label, result[key])


def cmd_build(args: argparse.Namespace) -> int:
    repo_root = pathlib.Path(args.repo_root).resolve()
    manifest = model_library.validate_snapshot_manifest(
        load_json(args.artifact_manifest)
    )
    envelope = validate_runtime_envelope(load_json(args.runtime_envelope))
    criteria_input = validate_criteria_input(load_json(args.criteria))
    release, contract = build_release_and_contract(
        args,
        manifest=manifest,
        envelope=envelope,
        criteria_input=criteria_input,
    )
    candidate = build_candidate_document(
        profile=args.profile,
        source_kind=args.source_kind,
        release=release,
        contract=contract,
    )
    output_dir = pathlib.Path(args.output_dir) if args.output_dir else (
        repo_root
        / "experiments"
        / "model-onboarding"
        / args.profile
        / release["release_id"]
    )
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    output_dir = validate_candidate_location(output_dir, repo_root=repo_root)
    write_candidate_directory(
        output_dir,
        {
            "candidate.json": candidate,
            CANDIDATE_FILES["release"]: release,
            CANDIDATE_FILES["validation_contract"]: contract,
        },
    )
    render_result(
        {
            **candidate,
            "output": (
                output_dir.relative_to(repo_root).as_posix()
                if path_within(output_dir, repo_root)
                else str(output_dir)
            ),
            "promotion": "not-authorized",
        },
        json_output=args.json,
    )
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    supplied_candidate_dir = pathlib.Path(args.candidate_dir)
    if supplied_candidate_dir.is_symlink():
        fail("candidate directory must not be a symlink")
    candidate, release, contract = _load_candidate_directory(supplied_candidate_dir)
    if candidate["profile"] != args.profile:
        fail("release-plan candidate profile differs from the requested profile")
    if candidate["source_kind"] != args.source_kind:
        fail("release-plan candidate source kind differs from the current profile")
    _verify_current_profile(args, release)
    render_result(
        {
            **candidate,
            "promotion": "not-authorized",
            "verification": "passed",
        },
        json_output=args.json,
    )
    return 0


def add_profile_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--source-kind",
        choices=("hf", "content-addressed"),
        required=True,
    )
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
    parser.add_argument(
        "--model-access-contract",
        choices=tuple(sorted(model_serving_release.MODEL_ACCESS_CONTRACTS)),
        required=True,
    )
    parser.add_argument(
        "--artifact-reference",
        action="append",
        default=[],
        help="ARTIFACT_KEY=PROFILE_REFERENCE; normalized but never persisted",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build unreviewed source-neutral Model Serving Release plans"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Build an unreviewed release plan")
    add_profile_arguments(build)
    build.add_argument("--artifact-manifest", required=True)
    build.add_argument("--runtime-envelope", required=True)
    build.add_argument("--criteria", required=True)
    build.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Additional ADR-0004 artifact object; repeat as needed",
    )
    build.add_argument(
        "--artifact-binding",
        action="append",
        default=[],
        help="ARTIFACT_KEY=USE for each additional artifact",
    )
    build.add_argument("--output-dir", default="")
    build.add_argument("--json", action="store_true")
    build.set_defaults(func=cmd_build)
    verify = subparsers.add_parser("verify", help="Verify an unreviewed release plan")
    add_profile_arguments(verify)
    verify.add_argument("--candidate-dir", required=True)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        ModelServingReleasePlanError,
        immutable_descriptor_dir.ImmutableDescriptorDirectoryError,
        model_identity.ModelIdentityError,
        model_library.ModelLibraryError,
        model_serving_release.ModelServingReleaseError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
