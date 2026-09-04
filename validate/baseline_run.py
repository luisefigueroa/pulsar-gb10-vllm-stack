#!/usr/bin/env python3
"""Baseline-v1 run record and policy-derived producer arguments.

  validate/baseline_run.py run-args --policy FILE
  validate/baseline_run.py write --out FILE --spec-id ID --policy-digest D
      --lab-commit C --image-digest sha256:... --launch-contract-id ID
      --witness-before N --witness-after N --gate NAME:START:END:RC ...
      [--proposed-status stable|failed]

``run-args`` prints ``KEY=VALUE`` lines the runner passes to the producers,
derived from the verified policy so no gate parameter is typed twice.
``write`` records what one run observed: which spec, which policy digest,
which lab commit, the served image digest, the launch contract the
container carried, the boot witness before and after the gates, and each
gate's UTC window and exit code. It is run evidence beside the six
measurements; the evaluator never reads it and it assigns no status.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_VALIDATE_DIR = Path(__file__).resolve().parent
if str(_VALIDATE_DIR) not in sys.path:
    sys.path.insert(0, str(_VALIDATE_DIR))

from release_spec.schema import COMMIT_RE, SHA256_HEX_RE  # noqa: E402

from baseline_v1_policy import BaselinePolicyError, load_policy  # noqa: E402
from validator_measurement import atomic_write_bytes  # noqa: E402

RUN_KIND = "pulsar-baseline-run"
RUN_SCHEMA_VERSION = 1
GATE_NAMES = (
    "verify-snapshot-manifest",
    "serve-smoke",
    "run-gates",
    "evaluate-gsm8k",
    "validate-soak",
)
ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SELECTION_RE = re.compile(r"^sha256-order-first-(\d+)$")
IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROPOSED_STATUSES = ("stable", "failed")


class BaselineRunError(ValueError):
    """The run record or the policy-derived arguments cannot be produced."""


def fail(message: str) -> None:
    raise BaselineRunError(message)


def run_args(policy: dict[str, Any]) -> dict[str, str]:
    """Producer arguments implied by the policy, as shell-safe values."""
    values: dict[str, str] = {}
    for gate in policy["gates"]:
        criterion = gate["criterion_id"]
        thresholds = {item["metric"]: item["value"] for item in gate["thresholds"]}
        if criterion == "gsm8k-subset":
            pins = gate["pins"]
            selection = SELECTION_RE.fullmatch(pins["selection"])
            if selection is None:
                fail("gsm8k-subset selection is not sha256-order-first-N")
            values["GSM8K_DATASET_ID"] = pins["dataset_id"]
            values["GSM8K_DATASET_REVISION"] = pins["dataset_revision"]
            values["GSM8K_DATASET_SHA256"] = pins["dataset_file_sha256"]
            values["GSM8K_SUBSET"] = pins["subset"]
            values["GSM8K_SPLIT"] = pins["split"]
            values["GSM8K_SAMPLE_SIZE"] = selection.group(1)
            values["GSM8K_MAX_COMPLETION_TOKENS"] = str(pins["max_completion_tokens"])
            values["GSM8K_REASONING_MODE"] = pins["reasoning_mode"]
        elif criterion == "soak-60":
            seconds = int(thresholds["duration_seconds"])
            values["SOAK_MINUTES"] = str(-(-seconds // 60))
        elif criterion == "perf-snapshot":
            values["PERF_CONCURRENCIES"] = " ".join(
                str(level) for level in gate["required_concurrencies"]
            )
    for key in (
        "GSM8K_DATASET_ID",
        "GSM8K_DATASET_REVISION",
        "GSM8K_DATASET_SHA256",
        "GSM8K_SAMPLE_SIZE",
        "SOAK_MINUTES",
        "PERF_CONCURRENCIES",
    ):
        if key not in values:
            fail(f"policy does not determine {key}")
    for key, value in values.items():
        if re.search(r"[^A-Za-z0-9._/ :-]", value):
            fail(f"{key} value is not shell-safe")
    return values


GATE_ARG_RE = re.compile(
    r"^(?P<name>[a-z0-9-]+):(?P<started>\S{20}):(?P<ended>\S{20}):(?P<rc>-?\d+)$"
)


def _parse_gate(text: str) -> dict[str, Any]:
    match = GATE_ARG_RE.fullmatch(text)
    if match is None:
        fail("--gate must be NAME:STARTED_AT:ENDED_AT:RC with ISO-8601 UTC seconds ending in Z")
    name = match.group("name")
    started = match.group("started")
    ended = match.group("ended")
    if name not in GATE_NAMES:
        fail(f"unknown gate {name!r}")
    for stamp in (started, ended):
        if ISO_Z_RE.fullmatch(stamp) is None:
            fail(f"gate {name} timestamp {stamp!r} must be ISO-8601 UTC seconds ending in Z")
    if ended < started:
        fail(f"gate {name} ends before it starts")
    rc = int(match.group("rc"))
    if rc < 0 or rc > 255:
        fail(f"gate {name} rc must be an exit code")
    return {"name": name, "started_at": started, "ended_at": ended, "rc": rc}


def build_run_record(
    *,
    spec_id: str,
    policy_digest: str,
    lab_commit: str,
    image_digest: str,
    launch_contract_id: str,
    witness_before: int,
    witness_after: int,
    gates: list[dict[str, Any]],
    proposed_status: str | None,
) -> dict[str, Any]:
    for label, value in (("spec_id", spec_id), ("policy_digest", policy_digest), ("launch_contract_id", launch_contract_id)):
        if SHA256_HEX_RE.fullmatch(value) is None:
            fail(f"{label} must be a 64-character lowercase hex digest")
    if COMMIT_RE.fullmatch(lab_commit) is None:
        fail("lab_commit must be a 40-character lowercase hex commit")
    if IMAGE_DIGEST_RE.fullmatch(image_digest) is None:
        fail("image_digest must be sha256:<64 hex>")
    for label, value in (("witness_before", witness_before), ("witness_after", witness_after)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            fail(f"{label} must be a non-negative integer")
    names = [gate["name"] for gate in gates]
    if len(set(names)) != len(names):
        fail("a gate is recorded twice")
    if names != [name for name in GATE_NAMES if name in names]:
        fail("gates are not in policy order")
    if proposed_status is not None and proposed_status not in PROPOSED_STATUSES:
        fail("proposed_status must be stable or failed")
    record = {
        "schema_version": RUN_SCHEMA_VERSION,
        "kind": RUN_KIND,
        "spec_id": spec_id,
        "policy_digest": policy_digest,
        "lab_commit": lab_commit,
        "image_digest": image_digest,
        "launch_contract_id": launch_contract_id,
        "boot_witness": {
            "before": witness_before,
            "after": witness_after,
            "same_boot": witness_before == witness_after,
        },
        "gates": gates,
        "proposed_status": proposed_status,
    }
    return record


def pretty_bytes(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    args_parser = sub.add_parser("run-args", help="print KEY=VALUE producer arguments")
    args_parser.add_argument("--policy", required=True)
    write = sub.add_parser("write", help="write the run record")
    write.add_argument("--out", required=True)
    write.add_argument("--spec-id", required=True)
    write.add_argument("--policy-digest", required=True)
    write.add_argument("--lab-commit", required=True)
    write.add_argument("--image-digest", required=True)
    write.add_argument("--launch-contract-id", required=True)
    write.add_argument("--witness-before", type=int, required=True)
    write.add_argument("--witness-after", type=int, required=True)
    write.add_argument("--gate", action="append", default=[], help="NAME:STARTED_AT:ENDED_AT:RC")
    write.add_argument("--proposed-status", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run-args":
            policy, _digest = load_policy(args.policy)
            for key, value in run_args(policy).items():
                print(f"{key}={value}")
            return 0
        record = build_run_record(
            spec_id=args.spec_id,
            policy_digest=args.policy_digest,
            lab_commit=args.lab_commit,
            image_digest=args.image_digest,
            launch_contract_id=args.launch_contract_id,
            witness_before=args.witness_before,
            witness_after=args.witness_after,
            gates=[_parse_gate(item) for item in args.gate],
            proposed_status=args.proposed_status,
        )
        atomic_write_bytes(args.out, pretty_bytes(record))
    except (BaselineRunError, BaselinePolicyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"run record written: same_boot={record['boot_witness']['same_boot']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
