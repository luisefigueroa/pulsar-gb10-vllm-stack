#!/usr/bin/env python3
"""Deterministic fixtures for ADR 0004 issuance staging tests."""

from __future__ import annotations

import copy
import os
import pathlib
import subprocess
from typing import Any

from scripts import (
    model_identity,
    model_serving_release_capture as capture,
    model_validation_evidence,
)
from scripts.testlib import model_serving_release_capture_fixture as capture_fixture
from scripts.testlib import model_serving_release_registry_fixture as registry_fixture
from scripts.testlib import model_validation_evidence_fixture as evidence_fixture


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REVIEW_KIND = "pulsar-model-serving-release-issue-review"


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model_identity.pretty_json_bytes(value))


def git_env(repo_root: pathlib.Path | None = None) -> dict[str, str]:
    del repo_root
    return {
        "GIT_AUTHOR_NAME": "Issuance Fixture",
        "GIT_AUTHOR_EMAIL": "issuance-fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Issuance Fixture",
        "GIT_COMMITTER_EMAIL": "issuance-fixture@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def run_git(repo_root: pathlib.Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(git_env(repo_root))
    result = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(arguments)} failed: {result.stderr or result.stdout}"
        )
    return result


def init_git_repo(repo_root: pathlib.Path, *, branch: str = "issue/fixture") -> None:
    run_git(repo_root, "init", "-b", "main")
    run_git(repo_root, "config", "user.name", "Issuance Fixture")
    run_git(repo_root, "config", "user.email", "issuance-fixture@example.invalid")
    run_git(repo_root, "add", "-A")
    run_git(repo_root, "commit", "-m", "fixture initial commit")
    if branch != "main":
        run_git(repo_root, "checkout", "-b", branch)


def commit_all(repo_root: pathlib.Path, message: str) -> None:
    run_git(repo_root, "add", "-A")
    run_git(repo_root, "commit", "--allow-empty", "-m", message)


def seed_issue_repo(repo_root: pathlib.Path, *, git: bool = True) -> pathlib.Path:
    capture_fixture.seed_capture_repo(repo_root)
    (repo_root / ".gitignore").write_text("/experiments/\n", encoding="utf-8")
    if git:
        init_git_repo(repo_root)
    return repo_root


def persist_capture(inputs: capture_fixture.CaptureInputs, repo_root: pathlib.Path):
    built = capture.build_capture_from_plan(
        release_plan_dir=inputs.plan_dir,
        attempt_spec=inputs.attempt,
        repo_root=repo_root,
    )
    dest = capture.destination_for_layout(
        capture.default_capture_root(repo_root),
        built.layout,
        repo_root=repo_root,
    )
    capture.publish_candidate_tree(dest, built.files)
    verified = capture.load_verified_candidate(dest, repo_root=repo_root)
    verified.layout = built.layout
    return dest, verified


def assemble_persisted(
    candidates: list[capture.BuiltCapture], repo_root: pathlib.Path
) -> tuple[pathlib.Path, capture.BuiltCapture]:
    built = capture.assemble_built_candidates(candidates)
    dest = capture.destination_for_layout(
        capture.default_capture_root(repo_root),
        built.layout,
        repo_root=repo_root,
    )
    capture.publish_candidate_tree(dest, built.files)
    verified = capture.load_verified_candidate(dest, repo_root=repo_root)
    verified.layout = built.layout
    return dest, verified


def capture_criterion(
    repo_root: pathlib.Path,
    criterion_id: str,
    *,
    with_review: bool = False,
    **kwargs: Any,
) -> tuple[pathlib.Path, capture.BuiltCapture]:
    extra = capture_fixture.review_protected_source() if with_review else None
    inputs = capture_fixture.passing_criterion_spec(
        criterion_id,
        repo_root=repo_root,
        extra_protected=extra,
        **kwargs,
    )
    return persist_capture(inputs, repo_root)


def capture_prebarrier(
    repo_root: pathlib.Path, *, with_review: bool = True
) -> tuple[pathlib.Path, capture.BuiltCapture]:
    extra = capture_fixture.review_protected_source() if with_review else None
    inputs = capture_fixture.prebarrier_spec(repo_root, extra_protected=extra)
    return persist_capture(inputs, repo_root)


def complete_passing_candidate(
    repo_root: pathlib.Path,
) -> tuple[pathlib.Path, capture.BuiltCapture]:
    built_runs: list[capture.BuiltCapture] = []
    for index, criterion_id in enumerate(sorted(evidence_fixture.PASS_METRICS)):
        _dest, built = capture_criterion(
            repo_root,
            criterion_id,
            with_review=index == 0,
        )
        del _dest
        built_runs.append(built)
    return assemble_persisted(built_runs, repo_root)


def privacy_for_artifacts(
    candidate: capture.BuiltCapture,
    *,
    result: str = "passed",
    overrides: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    overrides = overrides or {}
    return [
        {
            "artifact_id": artifact["artifact_id"],
            "privacy_review": overrides.get(artifact["artifact_id"], result),
        }
        for artifact in candidate.bundle["evidence_artifacts"]
    ]


def review_declaration(
    candidate: capture.BuiltCapture,
    *,
    expected_status: str,
    privacy: str = "passed",
    privacy_overrides: dict[str, str] | None = None,
    provenance_overrides: dict[str, str] | None = None,
    exclusions: list[dict[str, Any]] | None = None,
    reviewer: str = "fixture-maintainer",
    reviewed_at: str = "2026-08-16T00:00:00Z",
    review_reference: str = "repository-review:fixture-issue",
    supersedes_decision_ids: list[str] | None = None,
) -> dict[str, Any]:
    components = {
        "artifact_identity": "pass",
        "runtime_identity": "pass",
        "contract_frozen_before_testing": "pass",
        "evidence_privacy": "pass",
        "security": "pass",
    }
    if privacy == "failed":
        components["evidence_privacy"] = "fail"
    elif privacy == "pending":
        components["evidence_privacy"] = "pending"
    components.update(provenance_overrides or {})
    return {
        "schema_version": 1,
        "kind": REVIEW_KIND,
        "candidate_id": candidate.manifest["candidate_id"],
        "artifacts": privacy_for_artifacts(
            candidate, result=privacy, overrides=privacy_overrides
        ),
        "provenance_security_review": components,
        "criterion_exclusions": copy.deepcopy(exclusions or []),
        "expected_status": expected_status,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "review_reference": review_reference,
        "supersedes_decision_ids": list(supersedes_decision_ids or []),
    }


def write_review(
    repo_root: pathlib.Path,
    candidate: capture.BuiltCapture,
    *,
    name: str = "issue-review",
    **kwargs: Any,
) -> tuple[pathlib.Path, dict[str, Any]]:
    document = review_declaration(candidate, **kwargs)
    path = repo_root / "reviews" / f"{name}.json"
    write_json(path, document)
    return path, document


def write_predecessor_registry(
    repo_root: pathlib.Path,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = source or evidence_fixture.build_predecessor_source()
    registry_root = repo_root / "models" / "model-serving-releases"
    registry_fixture.write_source_objects(registry_root, repo_root, source)
    return source


def candidate_bytes(dest: pathlib.Path) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for path in dest.rglob("*"):
        if path.is_file() and not path.is_symlink():
            payload[str(path.relative_to(dest))] = path.read_bytes()
    return payload


def first_review_artifact_id(candidate: capture.BuiltCapture) -> str:
    ids = candidate.bundle["review_evidence_artifact_ids"]
    if not ids:
        raise AssertionError("candidate has no review evidence")
    return ids[0]


def exclusion_for(
    candidate: capture.BuiltCapture,
    criterion_id: str,
    *,
    reason: str = "reviewed-protocol-deviation",
) -> dict[str, Any]:
    for record in candidate.run_records:
        if any(
            item["criterion_id"] == criterion_id
            for item in record["criterion_observations"]
        ):
            return {
                "criterion_id": criterion_id,
                "run_record_id": record["run_record_id"],
                "reason": reason,
                "review_evidence_artifact_ids": [
                    first_review_artifact_id(candidate)
                ],
            }
    raise AssertionError(f"candidate has no observation for {criterion_id}")
