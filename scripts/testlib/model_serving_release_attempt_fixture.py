#!/usr/bin/env python3
"""Fixtures for ADR 0004 attempt-composition tests."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from scripts import model_identity
from scripts.testlib import model_serving_release_capture_fixture as capture_fixture
from scripts.testlib import model_serving_release_fixture as release_fixture
from scripts.testlib import model_validation_evidence_fixture as evidence_fixture

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "validate") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "validate"))

from validator_measurement import (  # noqa: E402
    build_benchmark_measurement,
    build_compare_measurement,
    build_resource_measurement,
    empty_benchmark_level,
)
ATTEMPT_PROGRAMS = (
    "validate/compare_captures.py",
    "validate/bench_serve.py",
    "scripts/model-serving-experiment-monitor.sh",
    "validate/gsm8k_eval.py",
    "validate/soak.py",
    "validate/validator_measurement.py",
)
COMPARE_RESULT = "results/attempt-fixture/compare-captures.json"
BENCH_RESULT = "results/attempt-fixture/benchmark-serving.json"
COMPARE_RESOURCE_RESULT = "results/attempt-fixture/compare-resources.json"
BENCH_RESOURCE_RESULT = "results/attempt-fixture/benchmark-resources.json"


def seed_attempt_repo(repo_root: Path) -> Path:
    capture_fixture.seed_capture_repo(repo_root)
    (repo_root / "experiments" / "model-serving-release-attempts").mkdir(
        parents=True, exist_ok=True
    )
    for program in ATTEMPT_PROGRAMS:
        source = REPO_ROOT / program
        dest = repo_root / program
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    return repo_root


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model_identity.pretty_json_bytes(value))


def attempt_context(
    *,
    release: dict[str, Any] | None = None,
    compare_path: str = COMPARE_RESULT,
    bench_path: str = BENCH_RESULT,
) -> dict[str, Any]:
    release = release or release_fixture.build_release()
    provenance = capture_fixture.provenance_for_spec(
        evidence_fixture.build_preparation_provenance(release=release)
    )
    environment = capture_fixture.environment_for_spec(
        evidence_fixture.build_observed_environment(release=release)
    )
    return {
        "schema_version": 1,
        "kind": "pulsar-model-serving-release-attempt-context",
        "preparation_provenance": provenance,
        "observed_environment": environment,
        "attempts": {
            "compare-captures": {
                "attempt_id": "attempt-strict-same-boot",
                "started_at": "2026-08-14T12:00:00Z",
                "ended_at": "2026-08-14T12:05:00Z",
            },
            "benchmark-serving": {
                "attempt_id": "attempt-benchmark-serving",
                "started_at": "2026-08-14T12:05:00Z",
                "ended_at": "2026-08-14T12:15:00Z",
            },
        },
        "command_environment": [
            {"kind": "secret-reference", "name": "VLLM_API_KEY"}
        ],
        "command_site_options": [],
        "evidence_sources": {
            "compare-captures": {
                "source_key": "compare-captures",
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": compare_path,
            },
            "benchmark-serving": {
                "source_key": "benchmark-serving",
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": bench_path,
            },
        },
        "resource_diagnostic_sources": {
            "compare-captures": {
                "source_key": "resource-compare-captures",
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": COMPARE_RESOURCE_RESULT,
            },
            "benchmark-serving": {
                "source_key": "resource-benchmark-serving",
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": BENCH_RESOURCE_RESULT,
            },
        },
    }


def resource_measurement(
    *, release: dict[str, Any], started_at: str, ended_at: str, duration: str
) -> dict[str, Any]:
    node_count = release["supported_hardware_geometry"]["node_count"]
    labels = ["single"] if node_count == 1 else [str(item) for item in range(node_count)]
    ranks = [
        {
            "rank": rank,
            "collection_status": "complete",
            "sample_count": 2,
            "workload_sample_count": 2,
            "mem_available_min_bytes": 1000000000,
            "swap_used_max_bytes": 0,
            "node_memory_pressure_some_total_delta_us": 0,
            "workload_memory_current_max_bytes": 500000000,
            "workload_memory_peak_start_bytes": 400000000,
            "workload_memory_peak_end_bytes": 500000000,
            "workload_swap_current_max_bytes": 0,
            "oom_delta": 0,
            "oom_kill_delta": 0,
        }
        for rank in labels
    ]
    return build_resource_measurement(
        completion="complete",
        reason="completed",
        payload={
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration,
            "qualification_scope": "model-qualification",
            "sample_interval_seconds": "1",
            "expected_rank_count": node_count,
            "observed_rank_count": node_count,
            "sample_count": 2 * node_count,
            "ranks": ranks,
        },
    )


def complete_compare_measurement(
    *,
    sample_count: int = 30,
    identical_record_count: int = 30,
    exact_text_count: int = 30,
    diagnostic_verdict: str = "identical",
    hard_disagreement_count: int = 0,
) -> dict[str, Any]:
    return build_compare_measurement(
        completion="complete",
        reason="completed",
        payload={
            "sample_count": sample_count,
            "identical_record_count": identical_record_count,
            "exact_text_count": exact_text_count,
            "mean_prefix_match": "1" if identical_record_count == sample_count else "0.5",
            "min_prefix_match": "1" if identical_record_count == sample_count else "0.5",
            "max_matched_prefix_logprob_delta": (
                "0" if identical_record_count == sample_count else "0.05"
            ),
            "hard_disagreement_count": hard_disagreement_count,
            "diagnostic_verdict": diagnostic_verdict,
            "source_digests": {"a": "a" * 64, "b": "b" * 64},
        },
    )


def complete_bench_level(
    *,
    concurrency: int = 8,
    measured_request_count: int = 100,
    ttft_p50_ms: str = "20",
    ttft_p95_ms: str = "88",
    decode_tps_p50: str = "10",
    aggregate_tps: str = "25",
    wall_s: str = "4",
) -> dict[str, Any]:
    return {
        "concurrency": concurrency,
        "requested_request_count": measured_request_count,
        "measured_request_count": measured_request_count,
        "completion": "complete",
        "reason": "completed",
        "ttft_p50_ms": ttft_p50_ms,
        "ttft_p95_ms": ttft_p95_ms,
        "decode_tps_p50": decode_tps_p50,
        "aggregate_tps": aggregate_tps,
        "wall_s": wall_s,
    }


def complete_bench_measurement(
    *,
    levels: list[dict[str, Any]] | None = None,
    input_tokens: int = 512,
    output_tokens: int = 256,
    prompt_style: str = "synthetic",
    explicit_request_count: int | None = 100,
    completion: str = "complete",
    reason: str = "completed",
) -> dict[str, Any]:
    return build_benchmark_measurement(
        completion=completion,
        reason=reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        prompt_style=prompt_style,
        explicit_request_count=explicit_request_count,
        levels=levels or [complete_bench_level()],
    )


def incomplete_compare_measurement() -> dict[str, Any]:
    return build_compare_measurement(
        completion="incomplete",
        reason="unusable-input",
    )


def incomplete_bench_measurement() -> dict[str, Any]:
    return build_benchmark_measurement(
        completion="incomplete",
        reason="measured-incomplete",
        input_tokens=512,
        output_tokens=256,
        prompt_style="synthetic",
        explicit_request_count=100,
        levels=[
            empty_benchmark_level(
                concurrency=8,
                requested_request_count=100,
                reason="measured-incomplete",
            )
        ],
    )


def write_default_measurements(
    repo_root: Path,
    *,
    compare: dict[str, Any] | None = None,
    bench: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    compare_path = repo_root.joinpath(*Path(COMPARE_RESULT).parts)
    bench_path = repo_root.joinpath(*Path(BENCH_RESULT).parts)
    write_json(compare_path, compare or complete_compare_measurement())
    write_json(bench_path, bench or complete_bench_measurement())
    release = release_fixture.build_release()
    write_json(
        repo_root.joinpath(*Path(COMPARE_RESOURCE_RESULT).parts),
        resource_measurement(
            release=release,
            started_at="2026-08-14T12:00:00Z",
            ended_at="2026-08-14T12:05:00Z",
            duration="300",
        ),
    )
    write_json(
        repo_root.joinpath(*Path(BENCH_RESOURCE_RESULT).parts),
        resource_measurement(
            release=release,
            started_at="2026-08-14T12:05:00Z",
            ended_at="2026-08-14T12:15:00Z",
            duration="600",
        ),
    )
    return compare_path, bench_path


def prepare_compose_inputs(
    repo_root: Path,
    *,
    compare: dict[str, Any] | None = None,
    bench: dict[str, Any] | None = None,
    write_compare: bool = True,
    write_bench: bool = True,
) -> dict[str, Any]:
    release = release_fixture.build_release()
    contract = release_fixture.build_contract(release=release)
    plan_dir, candidate = capture_fixture.write_release_plan_candidate(
        repo_root, release, contract
    )
    compare_path = repo_root.joinpath(*Path(COMPARE_RESULT).parts)
    bench_path = repo_root.joinpath(*Path(BENCH_RESULT).parts)
    if write_compare:
        write_json(compare_path, compare or complete_compare_measurement())
    if write_bench:
        write_json(bench_path, bench or complete_bench_measurement())
    write_json(
        repo_root.joinpath(*Path(COMPARE_RESOURCE_RESULT).parts),
        resource_measurement(
            release=release,
            started_at="2026-08-14T12:00:00Z",
            ended_at="2026-08-14T12:05:00Z",
            duration="300",
        ),
    )
    write_json(
        repo_root.joinpath(*Path(BENCH_RESOURCE_RESULT).parts),
        resource_measurement(
            release=release,
            started_at="2026-08-14T12:05:00Z",
            ended_at="2026-08-14T12:15:00Z",
            duration="600",
        ),
    )
    context = attempt_context(release=release)
    context_path = repo_root / "attempt-context.json"
    write_json(context_path, context)
    return {
        "release": release,
        "contract": contract,
        "plan_dir": plan_dir,
        "planner_candidate_id": candidate["candidate_id"],
        "context": context,
        "context_path": context_path,
        "compare_path": compare_path,
        "bench_path": bench_path,
    }
