#!/usr/bin/env python3
"""Temporary registry roots and object writers for ADR 0004 persistence tests."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from pathlib import PurePosixPath
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_serving_release  # noqa: E402
from scripts.testlib import model_serving_release_fixture as release_fixture  # noqa: E402
from scripts.testlib import model_validation_evidence_fixture as evidence_fixture  # noqa: E402


NAMESPACES = (
    "descriptors",
    "contracts",
    "run-records",
    "evidence-bundles",
    "decisions",
)


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_namespace_object(
    registry_root: pathlib.Path,
    namespace: str,
    object_id: str,
    document: dict[str, Any],
) -> pathlib.Path:
    path = registry_root / namespace / f"{object_id}.json"
    write_json(path, document)
    return path


def write_readme(directory: pathlib.Path, text: str = "test registry\n") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "README.md").write_text(text, encoding="utf-8")


def init_registry_root(path: pathlib.Path) -> pathlib.Path:
    write_readme(path, "test Model Serving Release registry\n")
    for namespace in NAMESPACES:
        write_readme(path / namespace, f"test {namespace}\n")
    return path


def publishable_artifact_bytes(artifact: dict[str, Any]) -> bytes:
    relative = artifact["location"]["value"]
    label = PurePosixPath(relative).name.removesuffix(".json")
    data = f"content:{label}".encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    if digest != artifact["content"]["sha256"]:
        raise AssertionError(
            f"fixture artifact bytes do not match digest for {relative}"
        )
    return data


def write_publishable_artifacts(
    repo_root: pathlib.Path, artifacts: list[dict[str, Any]]
) -> None:
    for artifact in artifacts:
        if artifact.get("visibility") != "publishable":
            continue
        dest = repo_root.joinpath(*PurePosixPath(artifact["location"]["value"]).parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(publishable_artifact_bytes(artifact))


def write_release(
    registry_root: pathlib.Path, release: dict[str, Any]
) -> dict[str, Any]:
    write_namespace_object(
        registry_root, "descriptors", release["release_id"], release
    )
    return release


def write_contract(
    registry_root: pathlib.Path, contract: dict[str, Any]
) -> dict[str, Any]:
    write_namespace_object(
        registry_root, "contracts", contract["contract_id"], contract
    )
    return contract


def write_run(
    registry_root: pathlib.Path, record: dict[str, Any]
) -> dict[str, Any]:
    write_namespace_object(
        registry_root, "run-records", record["run_record_id"], record
    )
    return record


def write_bundle(
    registry_root: pathlib.Path, bundle: dict[str, Any]
) -> dict[str, Any]:
    write_namespace_object(
        registry_root, "evidence-bundles", bundle["bundle_id"], bundle
    )
    return bundle


def write_decision(
    registry_root: pathlib.Path, decision: dict[str, Any]
) -> dict[str, Any]:
    write_namespace_object(
        registry_root, "decisions", decision["decision_id"], decision
    )
    return decision


def write_source_objects(
    registry_root: pathlib.Path,
    repo_root: pathlib.Path,
    source: dict[str, Any],
    *,
    write_artifacts: bool = True,
) -> None:
    write_release(registry_root, source["release"])
    write_contract(registry_root, source["contract"])
    for record in source["run_records"]:
        write_run(registry_root, record)
    write_bundle(registry_root, source["evidence_bundle"])
    write_decision(registry_root, source["decision"])
    if write_artifacts:
        write_publishable_artifacts(
            repo_root, source["evidence_bundle"]["evidence_artifacts"]
        )
    for prior in source.get("prior_decision_sources") or []:
        write_source_objects(
            registry_root, repo_root, prior, write_artifacts=False
        )


def build_happy_source() -> dict[str, Any]:
    release = evidence_fixture.build_release()
    contract = evidence_fixture.build_contract(release=release)
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
    decision = evidence_fixture.build_decision(
        release=release,
        contract=contract,
        artifacts=artifacts,
        run_records=runs,
        bundle=bundle,
    )
    return evidence_fixture.evidence_source(
        release=release,
        contract=contract,
        bundle=bundle,
        run_records=runs,
        decision=decision,
    )


def build_alternate_contract(release: dict[str, Any]) -> dict[str, Any]:
    return model_serving_release.build_validation_contract(
        release=release,
        criteria=release_fixture.criteria(),
        context_requirement={
            "status": "required",
            "criterion_ids": ["accuracy-gsm8k"],
            "minimum_tokens": 65536,
            "depths": ["0.95", "0.05", "0.50"],
        },
        soak_requirement={
            "status": "required",
            "criterion_id": "stability-soak",
            "minimum_duration_seconds": 9000,
            "concurrency": 5,
            "maximum_request_errors": 0,
        },
        relative_performance=model_serving_release.no_comparable_predecessor(),
    )


def populate_happy_registry(
    registry_root: pathlib.Path, repo_root: pathlib.Path
) -> dict[str, Any]:
    init_registry_root(registry_root)
    source = build_happy_source()
    write_source_objects(registry_root, repo_root, source)
    return source
