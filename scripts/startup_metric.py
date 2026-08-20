#!/usr/bin/env python3
"""No-overwrite launch-to-first-health startup evidence (schema 2).

Extracted from the retired weight_fabric tool (ADR 0006). The model library
is the only weight-distribution mechanism, so new records always carry
weight_source=library-hot; the field stays in the record for continuity with
historical schema-2 evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile
from typing import Any

SAFE_PROFILE = re.compile(r"^[A-Za-z0-9._-]+$")


class StartupMetricError(ValueError):
    """Raised when startup evidence would be incomplete or mislabeled."""


def fail(message: str) -> None:
    raise StartupMetricError(message)


def clean_text(value: Any, field: str) -> str:
    text = str(value or "")
    if not text or any(char in text for char in ("\0", "\t", "\r", "\n")):
        fail(f"{field}: missing or contains control characters")
    return text


def profile_name(value: Any) -> str:
    text = clean_text(value, "profile")
    if not SAFE_PROFILE.fullmatch(text):
        fail("profile: use letters, numbers, dot, underscore, or hyphen")
    return text


def bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        fail(f"{field}: expected an integer")
    if result < minimum or result > maximum:
        fail(f"{field}: expected {minimum}..{maximum}")
    return result


def positive_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        fail(f"{field}: expected a number")
    if not 0 < result <= 1024 * 1024:
        fail(f"{field}: expected a positive value")
    return result


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def atomic_write_json(
    value: dict[str, Any], destination: str, mode: int
) -> None:
    path = pathlib.Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def command_startup_metric(args: argparse.Namespace) -> None:
    topology_id = clean_text(args.topology_id, "topology_id")
    if not re.fullmatch(r"[0-9a-f]{64}", topology_id):
        fail("startup metric: invalid topology identity")
    if args.weight_source != "library-hot":
        fail("startup metric: only library-hot evidence is recordable")
    configuration_id = getattr(args, "configuration_id", None) or None
    if configuration_id is not None:
        fail("startup metric: library-hot content is not a fabric config")
    content_id = args.content_id or None
    content_digest = args.content_digest or None
    transport = args.transport or None
    integrity_scheme = args.integrity_scheme or None
    model_revision = args.model_revision or None
    identity_status = args.identity_status or None
    model_seal_id = args.model_seal_id or None
    validation_bundle_id = args.validation_bundle_id or None
    runtime_model_path = args.runtime_model_path or None
    owner_node_id = args.owner_node_id or None
    if owner_node_id is not None:
        owner_node_id = clean_text(owner_node_id, "owner_node_id")
    if owner_node_id is None:
        fail("startup metric: library-hot evidence requires a home owner")
    if content_id is None or not re.fullmatch(r"[0-9a-f]{12}", content_id):
        fail("startup metric: invalid library-hot content identity")
    if content_digest is None or not re.fullmatch(
        r"[0-9a-f]{64}", content_digest
    ):
        fail("startup metric: invalid library-hot content digest")
    # nfs-rdma stays accepted read-side: hot instances prepared before the
    # one-shot experiment was retired may still carry it in their stamps.
    if transport not in ("ssh-control", "ssh-roce", "nfs-rdma"):
        fail("startup metric: invalid library-hot transport")
    if integrity_scheme != "sha256-snapshot-manifest-v1":
        fail("startup metric: invalid library-hot integrity scheme")
    if args.cache_state != "sealed-hot":
        fail("startup metric: library-hot cache state must be sealed-hot")
    if model_revision is None or not re.fullmatch(
        r"[A-Za-z0-9._-]+", model_revision
    ):
        fail("startup metric: invalid library-hot model revision")
    if identity_status not in ("match", "legacy-unsealed", "unvalidated"):
        fail("startup metric: invalid library-hot identity status")
    if runtime_model_path is None or not runtime_model_path.endswith(
        f"/snapshots/{model_revision}"
    ):
        fail("startup metric: runtime model path is not the exact revision")
    if identity_status == "match":
        if not re.fullmatch(r"[0-9a-f]{40,64}", model_revision):
            fail("startup metric: matched revision is not an immutable commit")
        for name, value in (
            ("model seal", model_seal_id),
            ("validation bundle", validation_bundle_id),
        ):
            if value is None or not re.fullmatch(r"[0-9a-f]{64}", value):
                fail(f"startup metric: invalid {name} identity")
    elif model_seal_id is not None or validation_bundle_id is not None:
        fail("startup metric: unsealed identity cannot claim seal provenance")
    destination = pathlib.Path(args.output)
    if destination == pathlib.Path("/") or destination.exists():
        fail("startup metric: output must be a new bounded path")
    tag = profile_name(args.tag) if args.tag else None
    record = {
        "schema_version": 2,
        "kind": "container-launch-to-first-health",
        "profile": profile_name(args.profile),
        "model": clean_text(args.model, "model"),
        "weight_source": args.weight_source,
        "nodes": bounded_int(args.nodes, "nodes", 2, 255),
        "topology_id": topology_id,
        "configuration_id": None,
        "content_id": content_id,
        "content_digest": content_digest,
        "transport": transport,
        "integrity_scheme": integrity_scheme,
        "model_revision": model_revision,
        "identity_status": identity_status,
        "model_seal_id": model_seal_id,
        "validation_bundle_id": validation_bundle_id,
        "runtime_model_path": runtime_model_path,
        "owner_node_fingerprint": (
            fingerprint(owner_node_id) if owner_node_id else None
        ),
        "tag": tag,
        "cache_state": args.cache_state,
        "started_at": clean_text(args.started_at, "started_at"),
        "first_healthy_at": clean_text(
            args.first_healthy_at, "first_healthy_at"
        ),
        "time_to_first_healthy_seconds": positive_float(
            args.elapsed_seconds, "elapsed_seconds"
        ),
    }
    atomic_write_json(record, args.output, 0o644)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    startup_metric = subparsers.add_parser(
        "startup-metric",
        help="write no-overwrite launch-to-first-health evidence",
    )
    startup_metric.add_argument("--output", required=True)
    startup_metric.add_argument("--profile", required=True)
    startup_metric.add_argument("--model", required=True)
    startup_metric.add_argument(
        "--weight-source",
        choices=("library-hot",),
        required=True,
    )
    startup_metric.add_argument("--nodes", type=int, required=True)
    startup_metric.add_argument("--topology-id", required=True)
    startup_metric.add_argument("--configuration-id")
    startup_metric.add_argument("--owner-node-id")
    startup_metric.add_argument("--content-id")
    startup_metric.add_argument("--content-digest")
    startup_metric.add_argument(
        "--transport",
        choices=("ssh-control", "ssh-roce", "nfs-rdma"),
    )
    startup_metric.add_argument("--integrity-scheme")
    startup_metric.add_argument(
        "--identity-status",
        choices=("match", "legacy-unsealed", "unvalidated"),
    )
    startup_metric.add_argument("--model-revision")
    startup_metric.add_argument("--model-seal-id")
    startup_metric.add_argument("--validation-bundle-id")
    startup_metric.add_argument("--runtime-model-path")
    startup_metric.add_argument("--tag")
    startup_metric.add_argument(
        "--cache-state",
        choices=("cold", "warm", "sealed-hot", "unspecified"),
        default="unspecified",
    )
    startup_metric.add_argument("--started-at", required=True)
    startup_metric.add_argument("--first-healthy-at", required=True)
    startup_metric.add_argument(
        "--elapsed-seconds", type=float, required=True
    )
    startup_metric.set_defaults(func=command_startup_metric)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except StartupMetricError as error:
        print(f"startup-metric: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
