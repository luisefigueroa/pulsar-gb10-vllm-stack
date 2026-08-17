#!/usr/bin/env python3
"""Maintainer ADR 0004 issuance staging.

Deterministically turn one independently verified unreviewed evidence-capture
candidate plus an explicit review declaration into a proposal of
content-addressed Model Serving Release registry objects and any
privacy-cleared publishable evidence.

This module is not a trust authority. A successful local plan or stage does
not review, merge, authorize serving, or prove physical behavior. Repository
review and merge remain the trust event. Schema ownership stays in the pure
ADR 0004 modules. The registry module remains the read-only loader and
graph inspector.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts import (
        immutable_descriptor_dir,
        model_identity,
        model_serving_release,
        model_serving_release_capture,
        model_serving_release_registry,
        model_validation_evidence,
        terminal_format,
    )
except ModuleNotFoundError:
    import immutable_descriptor_dir  # type: ignore[no-redef]
    import model_identity  # type: ignore[no-redef]
    import model_serving_release  # type: ignore[no-redef]
    import model_serving_release_capture  # type: ignore[no-redef]
    import model_serving_release_registry  # type: ignore[no-redef]
    import model_validation_evidence  # type: ignore[no-redef]
    import terminal_format  # type: ignore[no-redef]


ISSUE_OUTPUT_SCHEMA_VERSION = 1
REVIEW_DECLARATION_SCHEMA_VERSION = 1
REVIEW_DECLARATION_KIND = "pulsar-model-serving-release-issue-review"
REGISTRY_RELATIVE = model_serving_release_capture.REGISTRY_RELATIVE
HEX64_RE = model_serving_release_capture.HEX64_RE
ABS_PATH_RE = model_serving_release_capture.ABS_PATH_RE
SAFE_RELATIVE_FILE_RE = model_serving_release_capture.SAFE_RELATIVE_FILE_RE
DEFAULT_BRANCH_NAMES = {"main", "master"}
FILE_CREATE_MODE = 0o644

REVIEW_DECLARATION_FIELDS = {
    "schema_version",
    "kind",
    "candidate_id",
    "artifacts",
    "provenance_security_review",
    "criterion_exclusions",
    "expected_status",
    "reviewer",
    "reviewed_at",
    "review_reference",
    "supersedes_decision_ids",
}
ARTIFACT_REVIEW_FIELDS = {"artifact_id", "privacy_review"}
EXCLUSION_FIELDS = {
    "criterion_id",
    "run_record_id",
    "reason",
    "review_evidence_artifact_ids",
}
PROVENANCE_COMPONENT_FIELDS = set(
    model_validation_evidence.PROVENANCE_REVIEW_COMPONENTS
)

TRUST_NOTES = (
    "Staged objects are not trusted until repository review and merge.",
    "A successful local command does not establish trust and does not "
    "prove that repository review occurred.",
    "Validation status is advisory and never permits or blocks serving.",
    "This workflow does not edit a model profile or bind "
    "MODEL_SERVING_RELEASE_ID.",
    "A matching review_reference cannot prove that review occurred.",
    "This workflow makes no physical DGX claim.",
)

HELP_LINES = (
    "Stage reviewed ADR 0004 Model Serving Release registry proposals",
    "",
    "Usage:",
    "scripts/model-serving-release-issue.sh plan --candidate-dir DIR --review-file FILE [--json]",
    "scripts/model-serving-release-issue.sh stage --candidate-dir DIR --review-file FILE [--json]",
    "",
    "Maintainer safety:",
    "plan is a read-only exact preview. stage writes a proposal on a clean "
    "non-default branch.",
    "Staged objects are not trusted until repository review and merge.",
    "The existing schema modules derive the decision status. The review "
    "file's expected status is an assertion and must match.",
    "This command does not edit a model profile, authorize serving, or "
    "make a physical DGX claim.",
    "Accepts repository-review:<privacy-safe-id>, pr:<id>, and commit:<hash>. "
    "That syntax cannot prove review.",
)

AFTER_FILE_WRITE_HOOK = None
AFTER_PLAN_HOOK = None


class ModelServingReleaseIssueError(ValueError):
    """An issuance plan or stage operation is unsafe or invalid."""


def fail(message: str) -> None:
    raise ModelServingReleaseIssueError(message)


def encode_json(value: Any) -> bytes:
    return model_identity.pretty_json_bytes(value)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_object(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        missing = sorted(fields - set(value)) if isinstance(value, dict) else []
        extra = sorted(set(value) - fields) if isinstance(value, dict) else []
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return value


def _sha256_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        fail(f"{label} must be a SHA-256 digest")
    return value


def _screen_public(value: Any, *, label: str) -> None:
    try:
        model_serving_release._validate_public_json(value, label=label)
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))


def _validate_reviewer(value: Any) -> str:
    try:
        return model_validation_evidence._validate_decision_reviewer(value)
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))


def _validate_review_reference(value: Any) -> str:
    try:
        return model_validation_evidence._validate_decision_review_reference(value)
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))


def validate_review_declaration(value: Any) -> dict[str, Any]:
    """Validate the closed issuer review declaration. It is not an ADR object."""
    document = _require_object(
        value, REVIEW_DECLARATION_FIELDS, label="issue review declaration"
    )
    for key, item in document.items():
        if key == "review_reference":
            continue
        _screen_public(item, label=f"issue review declaration.{key}")
    if document.get("schema_version") != REVIEW_DECLARATION_SCHEMA_VERSION:
        fail("issue review declaration schema_version is unsupported")
    if document.get("kind") != REVIEW_DECLARATION_KIND:
        fail("issue review declaration kind is invalid")
    _sha256_id(document.get("candidate_id"), label="issue review candidate_id")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        fail("issue review artifacts must be a non-empty list")
    seen: list[str] = []
    validated_artifacts: list[dict[str, Any]] = []
    for index, item in enumerate(artifacts):
        artifact = _require_object(
            item,
            ARTIFACT_REVIEW_FIELDS,
            label=f"issue review artifacts[{index}]",
        )
        artifact_id = _sha256_id(
            artifact.get("artifact_id"),
            label=f"issue review artifacts[{index}].artifact_id",
        )
        privacy = artifact.get("privacy_review")
        if privacy not in model_validation_evidence.PRIVACY_REVIEW_RESULTS:
            fail("issue review artifact privacy_review is unsupported")
        seen.append(artifact_id)
        validated_artifacts.append(
            {"artifact_id": artifact_id, "privacy_review": privacy}
        )
    if seen != sorted(seen) or len(seen) != len(set(seen)):
        fail("issue review artifacts must be sorted by unique artifact_id")
    components = _require_object(
        document.get("provenance_security_review"),
        PROVENANCE_COMPONENT_FIELDS,
        label="issue review provenance_security_review",
    )
    for name in model_validation_evidence.PROVENANCE_REVIEW_COMPONENTS:
        if components.get(name) not in (
            model_validation_evidence.REVIEW_COMPONENT_RESULTS
        ):
            fail(f"issue review provenance_security_review.{name} is unsupported")
    exclusions = document.get("criterion_exclusions")
    if not isinstance(exclusions, list):
        fail("issue review criterion_exclusions must be a list")
    validated_exclusions: list[dict[str, Any]] = []
    exclusion_keys: list[tuple[str, str]] = []
    for index, item in enumerate(exclusions):
        exclusion = _require_object(
            item,
            EXCLUSION_FIELDS,
            label=f"issue review criterion_exclusions[{index}]",
        )
        criterion_id = model_validation_evidence._safe_identifier(
            exclusion.get("criterion_id"),
            label=f"issue review criterion_exclusions[{index}].criterion_id",
        )
        run_id = _sha256_id(
            exclusion.get("run_record_id"),
            label=f"issue review criterion_exclusions[{index}].run_record_id",
        )
        reason = model_validation_evidence._nonempty_string(
            exclusion.get("reason"),
            label=f"issue review criterion_exclusions[{index}].reason",
        )
        evidence_ids = exclusion.get("review_evidence_artifact_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            fail("issue review exclusion must cite review evidence")
        mapped_ids = [
            _sha256_id(
                artifact_id,
                label=(
                    f"issue review criterion_exclusions[{index}]."
                    "review_evidence_artifact_ids"
                ),
            )
            for artifact_id in evidence_ids
        ]
        if mapped_ids != sorted(mapped_ids) or len(mapped_ids) != len(set(mapped_ids)):
            fail("issue review exclusion evidence IDs must be sorted and unique")
        exclusion_keys.append((criterion_id, run_id))
        validated_exclusions.append(
            {
                "criterion_id": criterion_id,
                "run_record_id": run_id,
                "reason": reason,
                "review_evidence_artifact_ids": mapped_ids,
            }
        )
    if exclusion_keys != sorted(exclusion_keys) or len(exclusion_keys) != len(
        set(exclusion_keys)
    ):
        fail("issue review criterion_exclusions must be sorted and unique")
    expected_status = document.get("expected_status")
    if expected_status not in model_validation_evidence.BASE_VALIDATION_STATUSES:
        fail("issue review expected_status is unsupported")
    reviewer = _validate_reviewer(document.get("reviewer"))
    try:
        model_validation_evidence._parse_rfc3339_utc(
            document.get("reviewed_at"),
            label="issue review reviewed_at",
        )
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))
    review_reference = _validate_review_reference(document.get("review_reference"))
    supersedes = document.get("supersedes_decision_ids")
    if not isinstance(supersedes, list):
        fail("issue review supersedes_decision_ids must be a list")
    supersede_ids = [
        _sha256_id(item, label="issue review supersedes_decision_ids")
        for item in supersedes
    ]
    if supersede_ids != sorted(supersede_ids) or len(supersede_ids) != len(
        set(supersede_ids)
    ):
        fail("issue review supersedes_decision_ids must be sorted and unique")
    return {
        "schema_version": REVIEW_DECLARATION_SCHEMA_VERSION,
        "kind": REVIEW_DECLARATION_KIND,
        "candidate_id": document["candidate_id"],
        "artifacts": validated_artifacts,
        "provenance_security_review": {
            name: components[name]
            for name in model_validation_evidence.PROVENANCE_REVIEW_COMPONENTS
        },
        "criterion_exclusions": validated_exclusions,
        "expected_status": expected_status,
        "reviewer": reviewer,
        "reviewed_at": document["reviewed_at"],
        "review_reference": review_reference,
        "supersedes_decision_ids": supersede_ids,
    }


def load_review_declaration(path: Path) -> dict[str, Any]:
    raw = immutable_descriptor_dir.read_absolute_file(
        path, label="issue review declaration"
    )
    parsed = immutable_descriptor_dir.parse_strict_json(
        raw, label="issue review declaration"
    )
    return validate_review_declaration(parsed)


def registry_object_path(namespace: str, object_id: str) -> str:
    if namespace not in model_serving_release_registry.NAMESPACE_SPECS:
        fail("registry namespace is unsupported")
    if HEX64_RE.fullmatch(object_id) is None:
        fail("registry object id is invalid")
    return f"{REGISTRY_RELATIVE}/{namespace}/{object_id}.json"


def _remap_artifact_id(
    artifact_id: str, mapping: dict[str, str], *, label: str
) -> str:
    try:
        return mapping[artifact_id]
    except KeyError:
        fail(f"{label} references an artifact outside the candidate")


def _remap_artifact_ids(
    values: Any, mapping: dict[str, str], *, label: str
) -> list[str]:
    if not isinstance(values, list):
        fail(f"{label} must be a list")
    remapped = [
        _remap_artifact_id(item, mapping, label=label) for item in values
    ]
    return sorted(remapped)


def _remap_observation(
    observation: dict[str, Any], mapping: dict[str, str]
) -> dict[str, Any]:
    result = copy.deepcopy(observation)
    result["evidence_artifact_ids"] = _remap_artifact_ids(
        result.get("evidence_artifact_ids"),
        mapping,
        label="criterion observation evidence",
    )
    requirements = result.get("contract_requirements")
    if not isinstance(requirements, dict):
        return result
    for name in ("context", "soak"):
        block = requirements.get(name)
        if not isinstance(block, dict):
            continue
        if "evidence_artifact_ids" in block:
            block["evidence_artifact_ids"] = _remap_artifact_ids(
                block.get("evidence_artifact_ids"),
                mapping,
                label=f"{name} requirement evidence",
            )
    return result


def _reviewed_artifact(
    artifact: dict[str, Any],
    *,
    privacy_review: str,
) -> dict[str, Any]:
    visibility = artifact["visibility"]
    location = artifact["location"]
    location_kind = location["kind"]
    location_value = location["value"]
    if privacy_review != "passed" and visibility == "publishable":
        visibility = "protected"
        location_kind = "protected-content-addressed"
        location_value = "sha256:" + artifact["content"]["sha256"]
    try:
        return model_validation_evidence.build_evidence_artifact(
            location_kind=location_kind,
            location_value=location_value,
            content_sha256=artifact["content"]["sha256"],
            media_type=artifact["content"]["media_type"],
            qualification_scope=artifact["qualification_scope"],
            visibility=visibility,
            privacy_review=privacy_review,
        )
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))


def _refuse_candidate_path_leak(
    value: Any,
    *,
    candidate_dir: Path,
    repo_root: Path,
    label: str,
) -> None:
    encoded = encode_json(value) if not isinstance(value, (bytes, bytearray)) else bytes(value)
    text = encoded.decode("utf-8")
    markers = (
        str(candidate_dir),
        str(candidate_dir.resolve()) if candidate_dir.exists() else "",
        str(repo_root),
        str(repo_root.resolve()),
    )
    for marker in markers:
        if marker and marker in text:
            fail(f"{label} would persist a local candidate or repository path")
    if ABS_PATH_RE.search(text):
        fail(f"{label} would persist an absolute path")


def materialize_reviewed_objects(
    candidate: model_serving_release_capture.BuiltCapture,
    review: dict[str, Any],
    *,
    registry: model_serving_release_registry.RegistryGraph,
) -> tuple[
    list[dict[str, Any]],
    dict[str, str],
    list[dict[str, Any]],
    dict[str, str],
    dict[str, Any],
    dict[str, str],
    dict[str, Any],
]:
    original_artifacts = {
        item["artifact_id"]: item
        for item in candidate.bundle["evidence_artifacts"]
    }
    declared = {item["artifact_id"] for item in review["artifacts"]}
    if declared != set(original_artifacts):
        missing = sorted(set(original_artifacts) - declared)
        extra = sorted(declared - set(original_artifacts))
        fail(
            "issue review must cover the candidate artifact set exactly "
            f"(missing={missing}, extra={extra})"
        )
    privacy_by_id = {
        item["artifact_id"]: item["privacy_review"] for item in review["artifacts"]
    }
    reviewed_artifacts = [
        _reviewed_artifact(
            original_artifacts[artifact_id],
            privacy_review=privacy_by_id[artifact_id],
        )
        for artifact_id in sorted(original_artifacts)
    ]
    artifact_id_map = {
        old_id: reviewed["artifact_id"]
        for old_id, reviewed in zip(
            sorted(original_artifacts), reviewed_artifacts, strict=True
        )
    }
    reviewed_by_new = {item["artifact_id"]: item for item in reviewed_artifacts}
    rebuilt_records: list[dict[str, Any]] = []
    run_record_id_map: dict[str, str] = {}
    try:
        for record in candidate.run_records:
            remapped_ids = _remap_artifact_ids(
                record["evidence_artifact_ids"],
                artifact_id_map,
                label="run-record evidence",
            )
            remapped_observations = [
                _remap_observation(item, artifact_id_map)
                for item in record["criterion_observations"]
            ]
            rebuilt = model_validation_evidence.build_validation_run_record(
                release=candidate.release,
                contract=candidate.contract,
                attempt=copy.deepcopy(record["attempt"]),
                preparation_provenance=copy.deepcopy(
                    record["preparation_provenance"]
                ),
                observed_environment=copy.deepcopy(record["observed_environment"]),
                commands=copy.deepcopy(record["commands"]),
                criterion_observations=remapped_observations,
                evidence_artifacts=[
                    reviewed_by_new[item] for item in remapped_ids
                ],
                evidence_artifact_ids=remapped_ids,
            )
            run_record_id_map[record["run_record_id"]] = rebuilt["run_record_id"]
            rebuilt_records.append(rebuilt)
        rebuilt_records.sort(key=lambda item: item["run_record_id"])
        review_ids = _remap_artifact_ids(
            candidate.bundle["review_evidence_artifact_ids"],
            artifact_id_map,
            label="bundle review evidence",
        )
        rebuilt_bundle = model_validation_evidence.build_validation_evidence_bundle(
            release=candidate.release,
            contract=candidate.contract,
            run_records=rebuilt_records,
            evidence_artifacts=reviewed_artifacts,
            review_evidence_artifact_ids=review_ids,
        )
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))
    remapped_exclusions: list[dict[str, Any]] = []
    for exclusion in review["criterion_exclusions"]:
        old_run = exclusion["run_record_id"]
        if old_run not in run_record_id_map:
            fail("issue review exclusion names a run outside the candidate")
        remapped_exclusions.append(
            {
                "criterion_id": exclusion["criterion_id"],
                "run_record_id": run_record_id_map[old_run],
                "reason": exclusion["reason"],
                "review_evidence_artifact_ids": _remap_artifact_ids(
                    exclusion["review_evidence_artifact_ids"],
                    artifact_id_map,
                    label="exclusion review evidence",
                ),
            }
        )
    direct_priors: list[dict[str, Any]] = []
    lineage: list[dict[str, Any]] = []
    seen_lineage: set[str] = set()
    for decision_id in review["supersedes_decision_ids"]:
        if decision_id not in registry.decisions:
            fail(
                "superseded decision is not stored in the verified tracked "
                "registry"
            )
        prior = registry.decisions[decision_id]
        direct_priors.append(prior)
        source = model_serving_release_registry.decision_source_set(
            registry, prior
        )
        for ancestor in model_validation_evidence.prior_decisions_from_source_set(
            source
        ):
            ancestor_id = ancestor["decision_id"]
            if ancestor_id in seen_lineage:
                continue
            seen_lineage.add(ancestor_id)
            lineage.append(ancestor)
    predecessor_registry = model_serving_release_registry.predecessor_source_sets(
        registry, candidate.contract
    )
    provenance = {
        **review["provenance_security_review"],
        "evidence_artifact_ids": review_ids,
    }
    try:
        decision = model_validation_evidence.build_validation_decision(
            release=candidate.release,
            contract=candidate.contract,
            evidence_bundle=rebuilt_bundle,
            run_records=rebuilt_records,
            criterion_exclusions=remapped_exclusions,
            predecessor_evidence_registry=predecessor_registry,
            provenance_security_review=provenance,
            status=review["expected_status"],
            reviewer=review["reviewer"],
            reviewed_at=review["reviewed_at"],
            review_reference=review["review_reference"],
            supersedes_decisions=direct_priors,
            supersession_lineage=lineage,
        )
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))
    bundle_id_map = {candidate.bundle["bundle_id"]: rebuilt_bundle["bundle_id"]}
    return (
        reviewed_artifacts,
        artifact_id_map,
        rebuilt_records,
        run_record_id_map,
        rebuilt_bundle,
        bundle_id_map,
        decision,
    )


def _safe_relative(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not SAFE_RELATIVE_FILE_RE.fullmatch(value):
        fail(f"{label} is not a safe repository-relative path")
    if value.startswith(".") or "/." in value:
        fail(f"{label} is not a safe repository-relative path")
    parts = PurePosixPath(value).parts
    if any(part in immutable_descriptor_dir.UNSAFE_PATH_COMPONENTS for part in parts):
        fail(f"{label} is not a safe repository-relative path")
    if parts and parts[0] == ".git":
        fail(f"{label} must not write under .git")
    return value


def _destination_parts(repo_root: Path, relative: str) -> tuple[str, ...]:
    repo = immutable_descriptor_dir.safe_absolute(
        repo_root, label="repository root"
    )
    return immutable_descriptor_dir.lexical_parts(
        repo, label="repository root"
    ) + immutable_descriptor_dir.lexical_parts(
        PurePosixPath(relative), label="destination"
    )


def inspect_destination(repo_root: Path, relative: str) -> bytes | None:
    parts = _destination_parts(repo_root, relative)
    try:
        parent_fd = immutable_descriptor_dir.open_directory_from_root(
            parts[:-1], label=f"destination {relative}", create=False
        )
    except immutable_descriptor_dir.ImmutableDescriptorDirectoryError as exc:
        if "is missing" in str(exc):
            return None
        fail(str(exc))
    try:
        try:
            preview = immutable_descriptor_dir.stat_at(
                parent_fd, parts[-1], label=f"destination {relative}"
            )
        except immutable_descriptor_dir.ImmutableDescriptorDirectoryError as exc:
            if "is missing" in str(exc):
                return None
            fail(str(exc))
        if stat.S_ISLNK(preview.st_mode):
            fail(f"destination {relative} must not be a symlink")
        if not stat.S_ISREG(preview.st_mode):
            fail(f"destination {relative} must be a regular file")
        fd = immutable_descriptor_dir.open_at(
            parent_fd,
            parts[-1],
            flags=immutable_descriptor_dir.file_read_flags(),
            label=f"destination {relative}",
        )
        try:
            return immutable_descriptor_dir.stable_read_fd(
                fd,
                preview=preview,
                label=f"destination {relative}",
            )
        finally:
            immutable_descriptor_dir.close_quietly(fd)
    finally:
        immutable_descriptor_dir.close_quietly(parent_fd)


def planned_file_action(
    repo_root: Path, relative: str, data: bytes
) -> str:
    existing = inspect_destination(repo_root, relative)
    if existing is None:
        return "create"
    if existing == data:
        return "reuse"
    fail(f"destination {relative} already exists with different bytes")


def write_planned_file(repo_root: Path, relative: str, data: bytes) -> str:
    action = planned_file_action(repo_root, relative, data)
    if action == "reuse":
        return action
    parts = _destination_parts(repo_root, relative)
    parent_fd = immutable_descriptor_dir.open_directory_from_root(
        parts[:-1], label=f"destination {relative}", create=False
    )
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = immutable_descriptor_dir.open_at(
                parent_fd,
                parts[-1],
                flags=flags,
                mode=FILE_CREATE_MODE,
                label=f"destination {relative}",
            )
        except immutable_descriptor_dir.ImmutableDescriptorDirectoryError:
            existing = inspect_destination(repo_root, relative)
            if existing == data:
                return "reuse"
            raise
        try:
            os.fchmod(fd, FILE_CREATE_MODE)
            immutable_descriptor_dir.write_fd(fd, data)
            os.fsync(fd)
        finally:
            immutable_descriptor_dir.close_quietly(fd)
        immutable_descriptor_dir.fsync_dir_fd(parent_fd)
    finally:
        immutable_descriptor_dir.close_quietly(parent_fd)
    hook = AFTER_FILE_WRITE_HOOK
    if hook is not None:
        hook(relative)
    return "create"


def candidate_tree_fingerprint(candidate: model_serving_release_capture.BuiltCapture) -> str:
    payload = {
        name: sha256_bytes(data) for name, data in sorted(candidate.files.items())
    }
    return sha256_bytes(encode_json(payload))


def _copy_graph(
    graph: model_serving_release_registry.RegistryGraph,
) -> model_serving_release_registry.RegistryGraph:
    return model_serving_release_registry.RegistryGraph(
        repo_root=graph.repo_root,
        registry_root=graph.registry_root,
        descriptors=dict(graph.descriptors),
        contracts=dict(graph.contracts),
        run_records=dict(graph.run_records),
        evidence_bundles=dict(graph.evidence_bundles),
        decisions=dict(graph.decisions),
    )


def _merge_graph_object(
    store: dict[str, dict[str, Any]],
    document: dict[str, Any],
    *,
    id_field: str,
    label: str,
) -> str:
    object_id = document[id_field]
    existing = store.get(object_id)
    if existing is None:
        store[object_id] = document
        return "create"
    if model_identity.canonical_json_digest(existing) != (
        model_identity.canonical_json_digest(document)
    ):
        fail(f"tracked registry {label} differs from the proposed object")
    return "reuse"


def merge_proposed_graph(
    registry: model_serving_release_registry.RegistryGraph,
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    run_records: list[dict[str, Any]],
    bundle: dict[str, Any],
    decision: dict[str, Any],
) -> model_serving_release_registry.RegistryGraph:
    graph = _copy_graph(registry)
    _merge_graph_object(
        graph.descriptors, release, id_field="release_id", label="release"
    )
    _merge_graph_object(
        graph.contracts, contract, id_field="contract_id", label="contract"
    )
    for record in run_records:
        _merge_graph_object(
            graph.run_records,
            record,
            id_field="run_record_id",
            label="run record",
        )
    _merge_graph_object(
        graph.evidence_bundles, bundle, id_field="bundle_id", label="evidence bundle"
    )
    _merge_graph_object(
        graph.decisions, decision, id_field="decision_id", label="decision"
    )
    return graph


def planned_registry_and_evidence_files(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    run_records: list[dict[str, Any]],
    bundle: dict[str, Any],
    decision: dict[str, Any],
    candidate: model_serving_release_capture.BuiltCapture,
) -> dict[str, bytes]:
    files: dict[str, bytes] = {
        registry_object_path("descriptors", release["release_id"]): encode_json(
            release
        ),
        registry_object_path("contracts", contract["contract_id"]): encode_json(
            contract
        ),
        registry_object_path("evidence-bundles", bundle["bundle_id"]): encode_json(
            bundle
        ),
        registry_object_path("decisions", decision["decision_id"]): encode_json(
            decision
        ),
    }
    for record in run_records:
        files[
            registry_object_path("run-records", record["run_record_id"])
        ] = encode_json(record)
    for artifact in bundle["evidence_artifacts"]:
        if artifact["visibility"] != "publishable":
            continue
        if artifact["privacy_review"] != "passed":
            fail("publishable reviewed evidence must have privacy_review=passed")
        relative = model_serving_release_capture.validate_publishable_repository_path(
            artifact["location"]["value"],
            label="reviewed publishable evidence location",
        )
        digest = artifact["content"]["sha256"]
        data = candidate.publishable_bytes.get(digest)
        if data is None or sha256_bytes(data) != digest:
            fail("reviewed publishable evidence bytes are missing or drifted")
        if relative in files and files[relative] != data:
            fail("reviewed publishable evidence destinations conflict")
        files[relative] = data
    return files


@dataclass
class IssuePlan:
    review: dict[str, Any]
    candidate_id: str
    candidate_fingerprint: str
    release: dict[str, Any]
    contract: dict[str, Any]
    artifacts: list[dict[str, Any]]
    artifact_id_map: dict[str, str]
    run_records: list[dict[str, Any]]
    run_record_id_map: dict[str, str]
    bundle: dict[str, Any]
    bundle_id_map: dict[str, str]
    decision: dict[str, Any]
    files: dict[str, bytes]
    file_actions: dict[str, str]
    projection: dict[str, Any]
    graph: model_serving_release_registry.RegistryGraph
    notes: list[str] = field(default_factory=lambda: list(TRUST_NOTES))


def load_registry_for_issue_plan(
    repo_root: Path,
) -> model_serving_release_registry.RegistryGraph:
    """Load a verified registry, with a narrow interrupted-stage fallback.

    Normal planning starts from the fully verified tracked registry. If an
    interrupted earlier stage left exact dependency-ordered proposal files,
    the normal loader reports the incomplete graph. The fallback scan enforces
    layout, no-follow, strict JSON, kind, and filename-to-declared-ID matching.
    The completed prospective graph must then pass content identity and every
    other registry rule before any write, and the normal ``load_registry``
    verifier runs after staging finishes.
    """
    try:
        return model_serving_release_registry.load_registry(repo_root)
    except model_serving_release_registry.ModelServingReleaseRegistryError as exc:
        full_error = exc
    try:
        return model_serving_release_registry.scan_registry(repo_root)
    except model_serving_release_registry.ModelServingReleaseRegistryError:
        fail(str(full_error))


def build_issue_plan(
    *,
    repo_root: Path,
    candidate_dir: Path,
    review: dict[str, Any],
) -> IssuePlan:
    repo = immutable_descriptor_dir.safe_absolute(
        repo_root, label="repository root"
    )
    dest = immutable_descriptor_dir.safe_absolute(
        candidate_dir,
        base=repo if not candidate_dir.is_absolute() else None,
        label="candidate directory",
    )
    registry = load_registry_for_issue_plan(repo)
    try:
        candidate = model_serving_release_capture.load_verified_candidate(
            dest, repo_root=repo
        )
    except (
        model_serving_release_capture.ModelServingReleaseCaptureError,
        model_serving_release.ModelServingReleaseError,
        model_validation_evidence.ModelValidationEvidenceError,
        immutable_descriptor_dir.ImmutableDescriptorDirectoryError,
    ) as exc:
        fail(str(exc))
    if review["candidate_id"] != candidate.manifest["candidate_id"]:
        fail("issue review candidate_id does not match the verified candidate")
    fingerprint = candidate_tree_fingerprint(candidate)
    (
        artifacts,
        artifact_id_map,
        run_records,
        run_record_id_map,
        bundle,
        bundle_id_map,
        decision,
    ) = materialize_reviewed_objects(candidate, review, registry=registry)
    files = planned_registry_and_evidence_files(
        release=candidate.release,
        contract=candidate.contract,
        run_records=run_records,
        bundle=bundle,
        decision=decision,
        candidate=candidate,
    )
    for relative in files:
        _safe_relative(relative, label="planned destination")
    file_actions = {
        relative: planned_file_action(repo, relative, data)
        for relative, data in sorted(files.items())
    }
    graph = merge_proposed_graph(
        registry,
        release=candidate.release,
        contract=candidate.contract,
        run_records=run_records,
        bundle=bundle,
        decision=decision,
    )
    try:
        model_serving_release_registry.validate_registry_graph(graph)
    except (
        model_serving_release_registry.ModelServingReleaseRegistryError,
        model_serving_release.ModelServingReleaseError,
        model_validation_evidence.ModelValidationEvidenceError,
    ) as exc:
        fail(str(exc))
    try:
        inspected = model_serving_release_registry.inspect_release(
            graph, candidate.release["release_id"]
        )
    except model_serving_release_registry.ModelServingReleaseRegistryError as exc:
        fail(str(exc))
    for label, document in (
        ("release", candidate.release),
        ("contract", candidate.contract),
        ("bundle", bundle),
        ("decision", decision),
        *[(f"run {item['run_record_id']}", item) for item in run_records],
    ):
        _refuse_candidate_path_leak(
            document, candidate_dir=dest, repo_root=repo, label=label
        )
    return IssuePlan(
        review=review,
        candidate_id=candidate.manifest["candidate_id"],
        candidate_fingerprint=fingerprint,
        release=candidate.release,
        contract=candidate.contract,
        artifacts=artifacts,
        artifact_id_map=artifact_id_map,
        run_records=run_records,
        run_record_id_map=run_record_id_map,
        bundle=bundle,
        bundle_id_map=bundle_id_map,
        decision=decision,
        files=files,
        file_actions=file_actions,
        projection=inspected["inspection"],
        graph=graph,
    )


def _git(
    repo_root: Path, *arguments: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "git command failed").strip()
        fail(detail.splitlines()[0] if detail else "git command failed")
    return result


def _git_paths_from_status(raw: str) -> list[str]:
    paths: list[str] = []
    index = 0
    data = raw
    while index < len(data):
        if index + 3 > len(data):
            break
        entry = data[index:]
        nul = entry.find("\0")
        if nul < 0:
            break
        record = entry[:nul]
        index += nul + 1
        if len(record) < 3:
            continue
        path = record[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if record.startswith("R") or record.startswith("C"):
            extra = data[index:]
            extra_nul = extra.find("\0")
            if extra_nul >= 0:
                path = extra[:extra_nul]
                index += extra_nul + 1
        if path:
            paths.append(path)
    return paths


def require_stage_git_state(
    repo_root: Path, *, planned_files: dict[str, bytes]
) -> None:
    inside = _git(repo_root, "rev-parse", "--is-inside-work-tree")
    if inside.stdout.strip() != "true":
        fail("stage requires a real Git repository")
    toplevel = Path(_git(repo_root, "rev-parse", "--show-toplevel").stdout.strip())
    repo = immutable_descriptor_dir.safe_absolute(
        repo_root, label="repository root"
    )
    if toplevel.resolve() != repo.resolve():
        fail("stage requires the repository root")
    symbolic = _git(repo_root, "symbolic-ref", "--quiet", "HEAD", check=False)
    if symbolic.returncode != 0:
        fail("stage refuses a detached HEAD")
    branch_ref = symbolic.stdout.strip()
    if not branch_ref.startswith("refs/heads/"):
        fail("stage refuses a detached HEAD")
    branch = branch_ref[len("refs/heads/") :]
    defaults = set(DEFAULT_BRANCH_NAMES)
    origin_head = _git(
        repo_root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False
    )
    if origin_head.returncode == 0:
        origin_ref = origin_head.stdout.strip()
        defaults.add(origin_ref.rsplit("/", 1)[-1])
    if branch in defaults:
        fail("stage refuses the default branch")
    status = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "-uall",
        "--ignore-submodules=all",
    )
    for relative in _git_paths_from_status(status.stdout):
        normalized = relative
        if normalized in planned_files:
            existing = inspect_destination(repo_root, normalized)
            if existing == planned_files[normalized]:
                continue
            fail(
                f"dirty path {normalized} differs from the planned proposal "
                "bytes"
            )
        fail(
            "stage requires a clean worktree except equal planned proposal "
            "files"
        )


def _write_order(relative: str) -> tuple[int, str]:
    if relative.startswith(f"{REGISTRY_RELATIVE}/descriptors/"):
        return (1, relative)
    if relative.startswith(f"{REGISTRY_RELATIVE}/contracts/"):
        return (2, relative)
    if relative.startswith(f"{REGISTRY_RELATIVE}/run-records/"):
        return (3, relative)
    if relative.startswith(f"{REGISTRY_RELATIVE}/evidence-bundles/"):
        return (4, relative)
    if relative.startswith(f"{REGISTRY_RELATIVE}/decisions/"):
        return (5, relative)
    return (0, relative)


def stage_issue_plan(plan: IssuePlan, *, repo_root: Path) -> dict[str, str]:
    require_stage_git_state(repo_root, planned_files=plan.files)
    actions: dict[str, str] = {}
    for relative, data in sorted(plan.files.items(), key=lambda item: _write_order(item[0])):
        actions[relative] = write_planned_file(repo_root, relative, data)
    try:
        # Normal stored-registry verifier: the proposal is not complete
        # until this succeeds.
        model_serving_release_registry.load_registry(repo_root)
    except (
        model_serving_release_registry.ModelServingReleaseRegistryError,
        model_serving_release.ModelServingReleaseError,
        model_validation_evidence.ModelValidationEvidenceError,
    ) as exc:
        fail(str(exc))
    return actions


def plan_payload(command: str, plan: IssuePlan) -> dict[str, Any]:
    files = [
        {
            "path": relative,
            "action": plan.file_actions[relative],
            "sha256": sha256_bytes(plan.files[relative]),
        }
        for relative in sorted(plan.files)
    ]
    return {
        "schema_version": ISSUE_OUTPUT_SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "kind": REVIEW_DECLARATION_KIND,
        "state": "proposal",
        "authority": "none",
        "trust": "untrusted-until-repository-review-and-merge",
        "promotion_authorized": False,
        "physical_claim": False,
        "candidate_id": plan.candidate_id,
        "release_id": plan.release["release_id"],
        "contract_id": plan.contract["contract_id"],
        "bundle_id": plan.bundle["bundle_id"],
        "decision_id": plan.decision["decision_id"],
        "status": plan.decision["status"],
        "status_label": model_validation_evidence.validation_status_label(
            plan.decision["status"]
        ),
        "artifact_id_map": dict(sorted(plan.artifact_id_map.items())),
        "run_record_id_map": dict(sorted(plan.run_record_id_map.items())),
        "bundle_id_map": dict(sorted(plan.bundle_id_map.items())),
        "files": files,
        "projection": plan.projection,
        "notes": list(plan.notes),
    }


def render_result(payload: dict[str, Any]) -> None:
    writer = terminal_format.TerminalWriter()
    writer.emit("ADR 0004 Model Serving Release issuance proposal")
    writer.blank()
    writer.field("Command", payload.get("command", ""))
    writer.field("State", payload.get("state", "proposal"))
    writer.field("Authority", payload.get("authority", "none"))
    writer.field("Trust", payload.get("trust", ""))
    if payload.get("ok") is False:
        writer.field("Error", payload.get("error", "issuance failed"))
        writer.blank()
        writer.emit("Notes")
        for note in payload.get("notes") or TRUST_NOTES:
            writer.emit(note, initial_indent="  ", subsequent_indent="  ")
        return
    writer.field("Release", payload.get("release_id", ""))
    writer.field("Contract", payload.get("contract_id", ""))
    writer.field("Bundle", payload.get("bundle_id", ""))
    writer.field("Decision", payload.get("decision_id", ""))
    writer.field("Status", payload.get("status_label", payload.get("status", "")))
    writer.field(
        "Promotion",
        "not authorized"
        if not payload.get("promotion_authorized")
        else "authorized",
    )
    inspection = payload.get("projection") or {}
    if inspection:
        writer.field("Inspection", inspection.get("state", ""))
    files = payload.get("files") or []
    created = [item["path"] for item in files if item.get("action") == "create"]
    reused = [item["path"] for item in files if item.get("action") == "reuse"]
    writer.field("Create", str(len(created)))
    writer.field("Reuse", str(len(reused)))
    writer.blank()
    writer.emit("Notes")
    for note in payload.get("notes") or TRUST_NOTES:
        writer.emit(note, initial_indent="  ", subsequent_indent="  ")


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def error_payload(command: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": ISSUE_OUTPUT_SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "error": message,
        "state": "proposal",
        "authority": "none",
        "trust": "untrusted-until-repository-review-and-merge",
        "promotion_authorized": False,
        "physical_claim": False,
        "notes": list(TRUST_NOTES),
    }


def _redact_repo_root(message: str, repo_root: Path) -> str:
    raw = str(repo_root)
    if raw in {"", ".", "/"}:
        return message
    prefix = raw.rstrip("/")
    message = message.replace(prefix + "/", "")
    return message.replace(prefix, "<repository-root>")


def _redact_external_argument_path(
    message: str, value: str | None, *, replacement: str
) -> str:
    if not value:
        return message
    path = Path(value)
    if not path.is_absolute():
        return message
    raw = str(path).rstrip("/")
    if raw in {"", "."}:
        return message
    message = message.replace(raw + "/", replacement + "/")
    return message.replace(raw, replacement)


def _load_inputs(args: argparse.Namespace) -> tuple[Path, Path, dict[str, Any]]:
    repo = immutable_descriptor_dir.safe_absolute(
        Path(args.repo_root), label="repository root"
    )
    if not args.candidate_dir:
        fail("usage requires --candidate-dir DIR")
    if not args.review_file:
        fail("usage requires --review-file FILE")
    candidate_dir = Path(args.candidate_dir)
    review_path = Path(args.review_file)
    if not review_path.is_absolute():
        review_path = immutable_descriptor_dir.safe_absolute(
            review_path, base=repo, label="issue review declaration"
        )
    else:
        review_path = immutable_descriptor_dir.safe_absolute(
            review_path, label="issue review declaration"
        )
    review = load_review_declaration(review_path)
    return repo, candidate_dir, review


def cmd_help(_args: argparse.Namespace) -> int:
    writer = terminal_format.TerminalWriter()
    for line in HELP_LINES:
        if line == "":
            writer.blank()
        else:
            writer.emit(line)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    repo, candidate_dir, review = _load_inputs(args)
    plan = build_issue_plan(
        repo_root=repo, candidate_dir=candidate_dir, review=review
    )
    hook = AFTER_PLAN_HOOK
    if hook is not None:
        hook(plan)
    payload = plan_payload("plan", plan)
    if args.json:
        emit_json(payload)
    else:
        render_result(payload)
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    repo, candidate_dir, review = _load_inputs(args)
    dest = immutable_descriptor_dir.safe_absolute(
        candidate_dir,
        base=repo if not candidate_dir.is_absolute() else None,
        label="candidate directory",
    )
    before = None
    try:
        before_candidate = model_serving_release_capture.load_verified_candidate(
            dest, repo_root=repo
        )
        before = candidate_tree_fingerprint(before_candidate)
    except model_serving_release_capture.ModelServingReleaseCaptureError as exc:
        fail(str(exc))
    plan = build_issue_plan(
        repo_root=repo, candidate_dir=candidate_dir, review=review
    )
    if plan.candidate_fingerprint != before:
        fail("capture candidate changed during issuance planning")
    hook = AFTER_PLAN_HOOK
    if hook is not None:
        hook(plan)
    current = model_serving_release_capture.load_verified_candidate(
        dest, repo_root=repo
    )
    if candidate_tree_fingerprint(current) != before:
        fail("capture candidate changed after issuance planning")
    actions = stage_issue_plan(plan, repo_root=repo)
    after = model_serving_release_capture.load_verified_candidate(
        dest, repo_root=repo
    )
    if candidate_tree_fingerprint(after) != before:
        fail("issuance must not mutate the capture candidate")
    plan.file_actions = actions
    payload = plan_payload("stage", plan)
    payload["state"] = "staged-proposal"
    if args.json:
        emit_json(payload)
    else:
        render_result(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Plan or stage an untrusted ADR 0004 Model Serving Release "
            "issuance proposal"
        )
    )
    parser.add_argument("--repo-root", required=True, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    help_cmd = subparsers.add_parser("help", help="Show issuance command help")
    help_cmd.set_defaults(func=cmd_help)

    plan = subparsers.add_parser(
        "plan", help="Read-only exact preview of the issuance proposal"
    )
    plan.add_argument("--candidate-dir")
    plan.add_argument("--review-file")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    stage = subparsers.add_parser(
        "stage", help="Write the issuance proposal after repeating verification"
    )
    stage.add_argument("--candidate-dir")
    stage.add_argument("--review-file")
    stage.add_argument("--json", action="store_true")
    stage.set_defaults(func=cmd_stage)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    repo_root = Path(args.repo_root)
    try:
        return int(args.func(args))
    except (
        ModelServingReleaseIssueError,
        model_serving_release_capture.ModelServingReleaseCaptureError,
        model_serving_release_registry.ModelServingReleaseRegistryError,
        model_serving_release.ModelServingReleaseError,
        model_validation_evidence.ModelValidationEvidenceError,
        model_identity.ModelIdentityError,
        immutable_descriptor_dir.ImmutableDescriptorDirectoryError,
        OSError,
    ) as exc:
        message = _redact_repo_root(str(exc), repo_root)
        message = _redact_external_argument_path(
            message,
            getattr(args, "candidate_dir", None),
            replacement="<candidate-dir>",
        )
        message = _redact_external_argument_path(
            message,
            getattr(args, "review_file", None),
            replacement="<review-file>",
        )
        if getattr(args, "json", False):
            emit_json(error_payload(command, message))
        else:
            print(
                f"model-serving-release-issue: ERROR: {message}",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
