#!/usr/bin/env python3
"""Deterministic fixtures for ADR 0004 evidence-capture candidate tests."""

from __future__ import annotations

import copy
import dataclasses
import pathlib
import shutil
from typing import Any

from scripts import model_identity, model_serving_release_plan
from scripts.testlib import model_serving_release_fixture as release_fixture
from scripts.testlib import model_serving_release_registry_fixture as registry_fixture
from scripts.testlib import model_validation_evidence_fixture as evidence_fixture


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CAPTURE_PROGRAMS = (
    "scripts/model-library.sh",
    "validate/run-gates.sh",
)
ATTEMPT_SPEC_KIND = "pulsar-model-serving-release-capture-attempt-spec"
LEGACY_CAPTURE_SPEC_KIND = "pulsar-model-serving-release-capture-spec"


@dataclasses.dataclass
class CaptureInputs:
    attempt: dict[str, Any]
    attempt_path: pathlib.Path
    plan_dir: pathlib.Path
    release: dict[str, Any]
    contract: dict[str, Any]
    planner_candidate_id: str


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model_identity.pretty_json_bytes(value))


def environment_for_spec(environment: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(environment)
    document.pop("supported_hardware_geometry_id", None)
    return document


def provenance_for_spec(provenance: dict[str, Any]) -> dict[str, Any]:
    document = copy.deepcopy(provenance)
    verification = document.get("verification")
    if isinstance(verification, dict):
        document["verification"] = {"status": verification["status"]}
    return document


def commands_for_spec(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for command in commands:
        item = copy.deepcopy(command)
        item.pop("version", None)
        result.append(item)
    return result


def seed_capture_repo(repo_root: pathlib.Path) -> pathlib.Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    for program in CAPTURE_PROGRAMS:
        source = REPO_ROOT / program
        dest = repo_root / program
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    registry_fixture.init_registry_root(
        repo_root / "models" / "model-serving-releases"
    )
    (repo_root / "results").mkdir(exist_ok=True)
    (repo_root / "experiments" / "model-serving-release-captures").mkdir(
        parents=True, exist_ok=True
    )
    return repo_root


def write_publishable_file(
    repo_root: pathlib.Path,
    relative: str,
    payload: dict[str, Any] | bytes,
) -> pathlib.Path:
    dest = repo_root.joinpath(*pathlib.PurePosixPath(relative).parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        dest.write_bytes(payload)
    else:
        dest.write_bytes(model_identity.pretty_json_bytes(payload))
    return dest


def _observation_for_spec(
    observation: dict[str, Any],
    *,
    source_key: str,
) -> dict[str, Any]:
    document = copy.deepcopy(observation)
    document.pop("benchmark_protocol_id", None)
    document["evidence_source_keys"] = [source_key]
    document.pop("evidence_artifact_ids", None)
    requirements = document.get("contract_requirements") or {}
    translated = {"context": None, "soak": None}
    for name in ("context", "soak"):
        block = requirements.get(name)
        if block is None:
            continue
        nested = copy.deepcopy(block)
        nested.pop("evidence_artifact_ids", None)
        nested["evidence_source_keys"] = [source_key]
        translated[name] = nested
    document["contract_requirements"] = translated
    return document


def _source_kind_for(release: dict[str, Any]) -> str:
    artifacts = release.get("model_artifact_set", {}).get("artifacts") or []
    primary = next(
        (
            item
            for item in artifacts
            if item.get("artifact_key") == "primary"
        ),
        artifacts[0] if artifacts else {},
    )
    if primary.get("kind") == "content-addressed-model":
        return "content-addressed"
    return "hf"


def write_release_plan_candidate(
    repo_root: pathlib.Path,
    release: dict[str, Any],
    contract: dict[str, Any],
    *,
    profile: str = "qwen3-1.7b",
    source_kind: str | None = None,
) -> tuple[pathlib.Path, dict[str, Any]]:
    candidate = model_serving_release_plan.build_candidate_document(
        profile=profile,
        source_kind=source_kind or _source_kind_for(release),
        release=release,
        contract=contract,
    )
    base = repo_root / "release-plans"
    dest = base / release["release_id"]
    suffix = 0
    while dest.exists() or dest.is_symlink():
        suffix += 1
        dest = base / f"{release['release_id']}-{suffix}"
    model_serving_release_plan.write_candidate_directory(
        dest,
        {
            "candidate.json": candidate,
            model_serving_release_plan.CANDIDATE_FILES["release"]: release,
            model_serving_release_plan.CANDIDATE_FILES["validation_contract"]: (
                contract
            ),
        },
    )
    return dest, candidate


def attempt_from_run(
    record: dict[str, Any],
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    source_key: str,
    repository_path: str | None,
    protected_digest: str | None = None,
    extra_protected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if repository_path is not None:
        evidence_source = {
            "source_key": source_key,
            "class": "publishable",
            "qualification_scope": record["attempt"]["qualification_scope"],
            "media_type": "application/json",
            "repository_path": repository_path,
        }
    else:
        if protected_digest is None:
            raise AssertionError("protected evidence requires a digest")
        evidence_source = {
            "source_key": source_key,
            "class": "protected",
            "qualification_scope": record["attempt"]["qualification_scope"],
            "media_type": "application/json",
            "content_sha256": protected_digest,
        }
    sources = [evidence_source]
    review_source_keys: list[str] = []
    if extra_protected is not None:
        sources.append(extra_protected)
        sources = sorted(sources, key=lambda item: item["source_key"])
        review_source_keys = [extra_protected["source_key"]]
    observations = [
        _observation_for_spec(item, source_key=source_key)
        for item in record["criterion_observations"]
    ]
    return {
        "schema_version": 1,
        "kind": ATTEMPT_SPEC_KIND,
        "release_id": release["release_id"],
        "contract_id": contract["contract_id"],
        "attempt": copy.deepcopy(record["attempt"]),
        "preparation_provenance": provenance_for_spec(
            record["preparation_provenance"]
        ),
        "observed_environment": environment_for_spec(
            record["observed_environment"]
        ),
        "commands": commands_for_spec(record["commands"]),
        "criterion_observations": observations,
        "evidence_sources": sources,
        "review_source_keys": review_source_keys,
    }


def spec_from_run(
    record: dict[str, Any],
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    source_key: str,
    repository_path: str | None,
    protected_digest: str | None = None,
    extra_protected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return attempt_from_run(
        record,
        release=release,
        contract=contract,
        source_key=source_key,
        repository_path=repository_path,
        protected_digest=protected_digest,
        extra_protected=extra_protected,
    )


def legacy_embedded_spec(
    record: dict[str, Any],
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    source_key: str,
    repository_path: str,
) -> dict[str, Any]:
    release_document = copy.deepcopy(release)
    release_document.pop("release_id", None)
    contract_document = copy.deepcopy(contract)
    contract_document.pop("contract_id", None)
    contract_document.pop("release_id", None)
    return {
        "schema_version": 1,
        "kind": LEGACY_CAPTURE_SPEC_KIND,
        "release": release_document,
        "contract": contract_document,
        "attempt": copy.deepcopy(record["attempt"]),
        "preparation_provenance": provenance_for_spec(
            record["preparation_provenance"]
        ),
        "observed_environment": environment_for_spec(
            record["observed_environment"]
        ),
        "commands": commands_for_spec(record["commands"]),
        "criterion_observations": [
            _observation_for_spec(item, source_key=source_key)
            for item in record["criterion_observations"]
        ],
        "evidence_sources": [
            {
                "source_key": source_key,
                "class": "publishable",
                "qualification_scope": record["attempt"]["qualification_scope"],
                "media_type": "application/json",
                "repository_path": repository_path,
            }
        ],
        "review_source_keys": [],
    }


def _inputs_from_record(
    record: dict[str, Any],
    *,
    repo_root: pathlib.Path,
    release: dict[str, Any],
    contract: dict[str, Any],
    source_key: str,
    repository_path: str,
    extra_fields: dict[str, Any] | None = None,
    extra_protected: dict[str, Any] | None = None,
    attempt_name: str,
) -> CaptureInputs:
    plan_dir, candidate = write_release_plan_candidate(
        repo_root, release, contract
    )
    attempt = attempt_from_run(
        record,
        release=release,
        contract=contract,
        source_key=source_key,
        repository_path=repository_path,
        extra_protected=extra_protected,
    )
    if extra_fields:
        attempt.update(extra_fields)
    attempt_path = repo_root / "capture-specs" / f"{attempt_name}.json"
    write_json(attempt_path, attempt)
    return CaptureInputs(
        attempt=attempt,
        attempt_path=attempt_path,
        plan_dir=plan_dir,
        release=release,
        contract=contract,
        planner_candidate_id=candidate["candidate_id"],
    )


def review_protected_source(
    *,
    source_key: str = "provenance-review",
    label: str = "provenance-security-review",
) -> dict[str, Any]:
    return {
        "source_key": source_key,
        "class": "protected",
        "qualification_scope": "release-promotion",
        "media_type": "application/json",
        "content_sha256": evidence_fixture.digest(f"content:{label}"),
    }


def passing_criterion_spec(
    criterion_id: str,
    *,
    repo_root: pathlib.Path,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    metrics: list[dict[str, str]] | None = None,
    attempt_completion: str = "completed",
    observation_completion: str | None = None,
    observation_reason: str | None = None,
    extra_fields: dict[str, Any] | None = None,
    extra_protected: dict[str, Any] | None = None,
) -> CaptureInputs:
    release = release or release_fixture.build_release()
    contract = contract or release_fixture.build_contract(release=release)
    complete = attempt_completion == "completed" and (
        observation_completion in {None, "complete"}
    )
    record = evidence_fixture.build_run_for_criterion(
        criterion_id,
        release=release,
        contract=contract,
        metrics=metrics,
        attempt_completion=attempt_completion,
        observation_completion=observation_completion,
        observation_reason=observation_reason,
        include_context_requirement=True,
        context_completion="complete" if complete else "inconclusive",
        include_soak_requirement=True,
        soak_completion="complete" if complete else "inconclusive",
    )
    relative = f"results/capture-fixture/{criterion_id}.json"
    write_publishable_file(
        repo_root,
        relative,
        {"criterion_id": criterion_id, "kind": "publishable-capture-fixture"},
    )
    return _inputs_from_record(
        record,
        repo_root=repo_root,
        release=release,
        contract=contract,
        source_key=criterion_id,
        repository_path=relative,
        extra_fields=extra_fields,
        extra_protected=extra_protected,
        attempt_name=criterion_id,
    )


def failing_measurement_spec(
    repo_root: pathlib.Path,
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> CaptureInputs:
    return passing_criterion_spec(
        "accuracy-gsm8k",
        repo_root=repo_root,
        release=release,
        contract=contract,
        metrics=[{"metric": "accuracy", "value": "0.1", "unit": "ratio"}],
    )


def incomplete_attempt_spec(
    repo_root: pathlib.Path,
    *,
    completion: str,
    reason: str,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
) -> CaptureInputs:
    return passing_criterion_spec(
        "latency-ttft",
        repo_root=repo_root,
        release=release,
        contract=contract,
        attempt_completion=completion,
        observation_completion="inconclusive",
        observation_reason=reason,
    )


def prebarrier_spec(
    repo_root: pathlib.Path,
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    extra_protected: dict[str, Any] | None = None,
) -> CaptureInputs:
    release = release or release_fixture.build_release()
    contract = contract or release_fixture.build_contract(release=release)
    record, _artifacts = evidence_fixture.build_prequalification_failure(
        release=release, contract=contract
    )
    relative = "results/capture-fixture/preparation-failure.json"
    write_publishable_file(
        repo_root,
        relative,
        {"kind": "preparation-failure"},
    )
    return _inputs_from_record(
        record,
        repo_root=repo_root,
        release=release,
        contract=contract,
        source_key="preparation-failure",
        repository_path=relative,
        extra_protected=extra_protected,
        attempt_name="preparation-failure",
    )


def write_spec(
    repo_root: pathlib.Path, name: str, spec: dict[str, Any]
) -> pathlib.Path:
    path = repo_root / "capture-specs" / f"{name}.json"
    write_json(path, spec)
    return path
