#!/usr/bin/env python3
"""Receipt-indexed cold-archive jobs and presence documents (ADR 0011).

NFS/cold is archive only. Presence has no occupancy authority. vLLM never
opens these paths. This module does not scan Official Models/ layouts.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
from typing import Any

try:
    from scripts import model_identity, model_library, model_library_source_attested as source_attested
except ModuleNotFoundError:
    import model_identity  # type: ignore[no-redef]
    import model_library  # type: ignore[no-redef]
    import model_library_source_attested as source_attested  # type: ignore[no-redef]


COLD_ARCHIVE_SCHEMA_VERSION = 1
COLD_ARCHIVE_JOB_KIND = "pulsar-model-library-cold-archive-job"
COLD_ARCHIVE_PRESENCE_KIND = "pulsar-model-library-cold-archive-presence"
COLD_ARCHIVE_JOB_STATES = (
    "pending",
    "running",
    "complete",
    "failed",
    "unavailable",
)
COLD_ARCHIVE_JOB_FIELDS = {
    "schema_version",
    "kind",
    "receipt_id",
    "model_id",
    "snapshot_revision",
    "state",
    "detail",
}
COLD_ARCHIVE_PRESENCE_FIELDS = {
    "schema_version",
    "kind",
    "receipt_id",
    "model_id",
    "snapshot_revision",
    "observed_manifest_id",
    "file_count",
    "content_bytes",
    "state",
}


class ColdArchiveError(ValueError):
    """A malformed or failed receipt-indexed cold archive contract."""


def fail(message: str) -> None:
    raise ColdArchiveError(message)


def cold_archive_job_store(library_dir: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(library_dir) / "cold-archive-jobs"


def cold_archive_receipts_root(cold_root: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(cold_root) / "pulsar-receipts"


def _hex_id(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or model_identity.SHA256_HEX_RE.fullmatch(value) is None:
        fail(f"{label} is invalid")
    return value


def validate_cold_archive_job(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("cold-archive job is not an object")
    unknown = sorted(set(value) - COLD_ARCHIVE_JOB_FIELDS)
    if unknown:
        fail(f"cold-archive job has unsupported fields {unknown}")
    missing = sorted(COLD_ARCHIVE_JOB_FIELDS - set(value))
    if missing:
        fail(f"cold-archive job is missing fields {missing}")
    if value.get("schema_version") != COLD_ARCHIVE_SCHEMA_VERSION:
        fail("cold-archive job schema is unsupported")
    if value.get("kind") != COLD_ARCHIVE_JOB_KIND:
        fail("cold-archive job kind is invalid")
    if value.get("state") not in COLD_ARCHIVE_JOB_STATES:
        fail("cold-archive job state is invalid")
    if not isinstance(value.get("detail"), str):
        fail("cold-archive job detail is invalid")
    return {
        "schema_version": COLD_ARCHIVE_SCHEMA_VERSION,
        "kind": COLD_ARCHIVE_JOB_KIND,
        "receipt_id": _hex_id(value.get("receipt_id"), label="job receipt_id"),
        "model_id": source_attested._validate_hf_model_id(
            value.get("model_id"), label="job model_id"
        ),
        "snapshot_revision": source_attested._validate_commit(
            value.get("snapshot_revision"), label="job snapshot_revision"
        ),
        "state": value["state"],
        "detail": value["detail"],
    }


def validate_cold_archive_presence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("cold-archive presence is not an object")
    unknown = sorted(set(value) - COLD_ARCHIVE_PRESENCE_FIELDS)
    if unknown:
        fail(f"cold-archive presence has unsupported fields {unknown}")
    missing = sorted(COLD_ARCHIVE_PRESENCE_FIELDS - set(value))
    if missing:
        fail(f"cold-archive presence is missing fields {missing}")
    if value.get("schema_version") != COLD_ARCHIVE_SCHEMA_VERSION:
        fail("cold-archive presence schema is unsupported")
    if value.get("kind") != COLD_ARCHIVE_PRESENCE_KIND:
        fail("cold-archive presence kind is invalid")
    if value.get("state") != "complete":
        fail("cold-archive presence state is invalid")
    file_count = value.get("file_count")
    content_bytes = value.get("content_bytes")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 1:
        fail("cold-archive presence file_count is invalid")
    if (
        isinstance(content_bytes, bool)
        or not isinstance(content_bytes, int)
        or content_bytes < 0
    ):
        fail("cold-archive presence content_bytes is invalid")
    return {
        "schema_version": COLD_ARCHIVE_SCHEMA_VERSION,
        "kind": COLD_ARCHIVE_PRESENCE_KIND,
        "receipt_id": _hex_id(value.get("receipt_id"), label="presence receipt_id"),
        "model_id": source_attested._validate_hf_model_id(
            value.get("model_id"), label="presence model_id"
        ),
        "snapshot_revision": source_attested._validate_commit(
            value.get("snapshot_revision"), label="presence snapshot_revision"
        ),
        "observed_manifest_id": _hex_id(
            value.get("observed_manifest_id"), label="presence observed_manifest_id"
        ),
        "file_count": file_count,
        "content_bytes": content_bytes,
        "state": "complete",
    }


def build_cold_archive_job(
    receipt: dict[str, Any],
    *,
    state: str,
    detail: str,
) -> dict[str, Any]:
    receipt = source_attested.validate_source_attested_acquisition_receipt(receipt)
    return validate_cold_archive_job(
        {
            "schema_version": COLD_ARCHIVE_SCHEMA_VERSION,
            "kind": COLD_ARCHIVE_JOB_KIND,
            "receipt_id": receipt["receipt_id"],
            "model_id": receipt["model_id"],
            "snapshot_revision": receipt["snapshot_revision"],
            "state": state,
            "detail": detail,
        }
    )


def _job_path(library_dir: str | pathlib.Path, receipt_id: str) -> pathlib.Path:
    store = cold_archive_job_store(library_dir)
    store.mkdir(parents=True, exist_ok=True)
    return store / f"{receipt_id}.json"


def write_cold_archive_job(
    library_dir: str | pathlib.Path, job: dict[str, Any]
) -> dict[str, Any]:
    job = validate_cold_archive_job(job)
    path = _job_path(library_dir, job["receipt_id"])
    model_library.atomic_write_json(path, job)
    return job


def load_cold_archive_job(
    library_dir: str | pathlib.Path, receipt_id: str
) -> dict[str, Any] | None:
    path = _job_path(library_dir, receipt_id)
    if not path.is_file():
        return None
    return validate_cold_archive_job(model_library.load_json(path))


def enqueue_cold_archive_job(
    library_dir: str | pathlib.Path,
    receipt: dict[str, Any],
    *,
    cold_root: str | None,
) -> dict[str, Any]:
    status = model_library.cold_root_status(cold_root)
    if not status["available"]:
        job = build_cold_archive_job(
            receipt,
            state="unavailable",
            detail="cold root is not configured or not readable",
        )
    else:
        job = build_cold_archive_job(
            receipt,
            state="pending",
            detail="cold archive pending (not a serving gate)",
        )
    return write_cold_archive_job(library_dir, job)


def presence_path(cold_root: str | pathlib.Path, receipt_id: str) -> pathlib.Path:
    return cold_archive_receipts_root(cold_root) / receipt_id / "presence.json"


def archived_hub_path(cold_root: str | pathlib.Path, receipt_id: str) -> pathlib.Path:
    return cold_archive_receipts_root(cold_root) / receipt_id / "home"


def load_cold_archive_presence(
    cold_root: str | pathlib.Path, receipt_id: str
) -> dict[str, Any] | None:
    path = presence_path(cold_root, receipt_id)
    if not path.is_file():
        return None
    return validate_cold_archive_presence(model_library.load_json(path))


COLD_ALLOW_SAME_DEVICE_ENV = "PULSAR_COLD_ALLOW_SAME_DEVICE"


def archive_is_complete(
    *,
    library_dir: str | pathlib.Path,
    receipt_id: str,
    cold_root: str | None,
) -> bool:
    """Layout-only presence: presence.json plus a home/ directory.

    This is not last-home authority. Last occupancy delete must call
    ``verify_existing_archive`` and ``cold_root_is_distinct_replica``.
    """
    _ = library_dir
    if cold_root is None:
        return False
    presence = load_cold_archive_presence(cold_root, receipt_id)
    if presence is None:
        return False
    hub = archived_hub_path(cold_root, receipt_id)
    return hub.is_dir() and presence["receipt_id"] == receipt_id


def cold_root_is_distinct_replica(
    cold_root: str | pathlib.Path,
    occupancy_hub_path: str | pathlib.Path,
    *,
    occupancy_device: int | None = None,
    compare_devices: bool = False,
) -> tuple[bool, str]:
    """Reject nested or same-host same-device cold roots. Does not claim NFS.

    Path prefix is checked from path strings so a remote occupancy hub does
    not have to exist on the controller. ``st_dev`` is compared only when
    ``compare_devices`` is true (occupancy rank is the controller). Lab tests
    may set PULSAR_COLD_ALLOW_SAME_DEVICE=1.
    """
    try:
        root = pathlib.Path(cold_root).expanduser().resolve()
    except OSError as exc:
        return False, f"cannot resolve cold root ({exc})"
    occupancy = pathlib.Path(occupancy_hub_path).expanduser()
    try:
        occupancy = occupancy.resolve()
    except OSError:
        occupancy = pathlib.Path(os.path.abspath(occupancy))
    if not root.is_dir():
        return False, "cold root is not a directory"
    if not os.access(root, os.W_OK):
        return False, "cold root is not writable"
    receipts = root / "pulsar-receipts"
    if receipts.exists() and not os.access(receipts, os.W_OK):
        return False, "cold archive receipts directory is not writable"
    try:
        occupancy.relative_to(root)
        return False, "cold root contains the occupancy tree"
    except ValueError:
        pass
    try:
        root.relative_to(occupancy)
        return False, "cold root is nested under the occupancy tree"
    except ValueError:
        pass
    if not compare_devices:
        return True, ""
    if occupancy_device is None:
        return False, (
            "occupancy device is unknown; cannot prove a "
            "distinct-failure-domain cold archive"
        )
    try:
        root_dev = root.stat().st_dev
    except OSError as exc:
        return False, f"cannot inspect cold root device ({exc})"
    if int(occupancy_device) == int(root_dev) and os.environ.get(
        COLD_ALLOW_SAME_DEVICE_ENV
    ) != "1":
        return False, (
            "cold root is on the same device as occupancy; last occupancy "
            "needs a distinct-failure-domain cold archive or "
            "--allow-unarchived-last-home"
        )
    return True, ""


def resolve_last_occupancy_receipt_id(
    library_dir: str | pathlib.Path,
    *,
    model_id: str,
    snapshot_revision: str,
) -> str | None:
    """Return one receipt id for the revision, or None if the store has none.

    An unreadable store raises. Does not inspect the cold archive.
    """
    receipts = source_attested.list_source_attested_receipts_for_revision(
        library_dir,
        model_id=model_id,
        snapshot_revision=snapshot_revision,
    )
    if not receipts:
        return None
    attachment = source_attested.load_source_attested_home_attachment(
        library_dir, model_id=model_id, snapshot_revision=snapshot_revision
    )
    if attachment is not None:
        return str(attachment["receipt_id"])
    return min(item["receipt_id"] for item in receipts)


def last_occupancy_cold_archive_blocker(
    *,
    library_dir: str | pathlib.Path,
    model_id: str,
    snapshot_revision: str,
    occupancy_hub_path: str,
    allow_unarchived: bool,
    occupancy_device: int | None = None,
    occupancy_rank: int = 0,
    expected_receipt_id: str | None = None,
) -> str | None:
    """Return a blocker detail, or None if last occupancy may proceed.

    Controller-only. An unreadable receipt store raises. ``allow_unarchived``
    skips replica verify after receipts list successfully; it does not ignore
    store errors. ``expected_receipt_id`` freezes the plan's receipt.
    """
    resolved_id = resolve_last_occupancy_receipt_id(
        library_dir, model_id=model_id, snapshot_revision=snapshot_revision
    )
    if expected_receipt_id:
        if resolved_id is None:
            fail("home removal: planned source-attested receipt is missing")
        if resolved_id != expected_receipt_id:
            fail("home removal: source-attested receipt changed after the plan")
        receipt_id = expected_receipt_id
    else:
        receipt_id = resolved_id
    if not receipt_id:
        return None
    if allow_unarchived:
        return None
    receipt = source_attested.load_source_attested_receipt(library_dir, receipt_id)
    if (
        receipt["model_id"] != model_id
        or receipt["snapshot_revision"] != snapshot_revision
    ):
        fail("home removal: frozen receipt does not match the planned identity")
    cold_root = model_library.configured_cold_root()
    if cold_root is None:
        return (
            "last occupancy has no distinct-failure-domain cold archive; "
            "pass --allow-unarchived-last-home to acknowledge there is no "
            "receipt-indexed replica"
        )
    ok, detail = cold_root_is_distinct_replica(
        cold_root,
        occupancy_hub_path,
        occupancy_device=occupancy_device,
        compare_devices=int(occupancy_rank) == 0,
    )
    if not ok:
        return detail
    try:
        verify_existing_archive(cold_root, receipt)
    except (
        ColdArchiveError,
        source_attested.SourceAttestedAcquisitionError,
        model_library.ModelLibraryError,
    ) as exc:
        return (
            "last occupancy has no verified receipt-indexed cold archive "
            f"({exc}); pass --allow-unarchived-last-home to acknowledge "
            "there is no distinct-failure-domain replica"
        )
    return None


def reverify_last_home_archive(
    plan: dict[str, Any],
    *,
    library_dir: str | pathlib.Path,
) -> None:
    """Controller-only TOCTOU re-check. Raises on failure. No hub mutation."""
    target = plan.get("target") or {}
    home = target.get("home") or {}
    occupancy_class = (
        plan.get("occupancy_class") or target.get("occupancy_class") or ""
    )
    if not target.get("last_durable_home"):
        return
    if occupancy_class == model_library.INCOMPLETE_HUB_OCCUPANCY:
        return
    revision = str(target.get("revision") or "")
    if model_library._home_revision_is_unbound(revision):
        return
    if plan.get("allow_unarchived_last_home"):
        return
    if not library_dir:
        fail("home removal: library dir is required to re-verify the cold archive")
    detail = last_occupancy_cold_archive_blocker(
        library_dir=library_dir,
        model_id=str(target["model_id"]),
        snapshot_revision=revision,
        occupancy_hub_path=str(home["hub_path"]),
        allow_unarchived=False,
        occupancy_device=(plan.get("inspection") or {}).get("occupancy_device"),
        occupancy_rank=int(home.get("rank", -1)),
        expected_receipt_id=plan.get("receipt_id") or target.get("receipt_id"),
    )
    if detail:
        fail(detail)


def _hash_hub(receipt: dict[str, Any], hub: pathlib.Path) -> dict[str, Any]:
    observed = model_library.inspect_snapshot_blob_identities(
        hub,
        model_id=receipt["model_id"],
        revision=receipt["snapshot_revision"],
        allow_empty_files=True,
    )
    source_attested.verify_source_attested_home(
        receipt,
        observed["manifest"],
        model_id=receipt["model_id"],
        snapshot_revision=receipt["snapshot_revision"],
    )
    return observed["manifest"]


def publish_verified_archive(
    cold_root: str | pathlib.Path,
    receipt: dict[str, Any],
    source_hub: str | pathlib.Path,
) -> dict[str, Any]:
    """Copy a local occupancy hub into a receipt-indexed cold presence."""
    receipt = source_attested.validate_source_attested_acquisition_receipt(receipt)
    source = pathlib.Path(source_hub)
    if not source.is_dir():
        fail("cold archive source hub is missing")
    root = cold_archive_receipts_root(cold_root)
    root.mkdir(parents=True, exist_ok=True)
    final = root / receipt["receipt_id"]
    staging = root / f".{receipt['receipt_id']}.staging"
    if staging.exists() or staging.is_symlink():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    dest_hub = staging / "home"
    shutil.copytree(source, dest_hub, symlinks=False)
    manifest = _hash_hub(receipt, dest_hub)
    presence = validate_cold_archive_presence(
        {
            "schema_version": COLD_ARCHIVE_SCHEMA_VERSION,
            "kind": COLD_ARCHIVE_PRESENCE_KIND,
            "receipt_id": receipt["receipt_id"],
            "model_id": receipt["model_id"],
            "snapshot_revision": receipt["snapshot_revision"],
            "observed_manifest_id": manifest["manifest_id"],
            "file_count": manifest["file_count"],
            "content_bytes": manifest["total_bytes"],
            "state": "complete",
        }
    )
    model_library.atomic_write_json(staging / "presence.json", presence)
    if final.exists() or final.is_symlink():
        fail("cold archive destination already exists")
    os.rename(staging, final)
    return presence


def verify_existing_archive(
    cold_root: str | pathlib.Path, receipt: dict[str, Any]
) -> dict[str, Any]:
    receipt = source_attested.validate_source_attested_acquisition_receipt(receipt)
    presence = load_cold_archive_presence(cold_root, receipt["receipt_id"])
    if presence is None:
        fail("cold archive presence is missing")
    if presence["receipt_id"] != receipt["receipt_id"]:
        fail("cold archive presence receipt differs")
    hub = archived_hub_path(cold_root, receipt["receipt_id"])
    manifest = _hash_hub(receipt, hub)
    if manifest["manifest_id"] != presence["observed_manifest_id"]:
        fail("cold archive rehash differs from presence")
    return presence


def _write_json(value: Any) -> int:
    sys.stdout.write(model_identity.pretty_json_bytes(value).decode("utf-8"))
    return 0


def _read_receipt(path: str) -> dict[str, Any]:
    raw = pathlib.Path(path).read_bytes()
    return source_attested.validate_source_attested_acquisition_receipt(
        json.loads(raw.decode("utf-8"))
    )


def cmd_enqueue(args: argparse.Namespace) -> int:
    receipt = _read_receipt(args.receipt)
    job = enqueue_cold_archive_job(
        args.library_dir,
        receipt,
        cold_root=model_library.configured_cold_root() if args.cold_root == "" else (
            None if args.cold_root in {"none", "-"} else args.cold_root
        ),
    )
    return _write_json(job)


def cmd_show_job(args: argparse.Namespace) -> int:
    job = load_cold_archive_job(args.library_dir, args.receipt_id)
    if job is None and not args.allow_missing:
        fail("cold-archive job not found")
    return _write_json(job)


def cmd_set_job_state(args: argparse.Namespace) -> int:
    job = load_cold_archive_job(args.library_dir, args.receipt_id)
    if job is None:
        fail("cold-archive job not found")
    job["state"] = args.state
    job["detail"] = args.detail
    return _write_json(write_cold_archive_job(args.library_dir, job))


def cmd_publish(args: argparse.Namespace) -> int:
    receipt = _read_receipt(args.receipt)
    presence = publish_verified_archive(args.cold_root, receipt, args.source_hub)
    job = build_cold_archive_job(
        receipt, state="complete", detail="receipt-indexed cold archive complete"
    )
    write_cold_archive_job(args.library_dir, job)
    return _write_json(presence)


def cmd_complete(args: argparse.Namespace) -> int:
    complete = archive_is_complete(
        library_dir=args.library_dir,
        receipt_id=args.receipt_id,
        cold_root=args.cold_root or model_library.configured_cold_root(),
    )
    return _write_json({"complete": complete})


def cmd_verify_presence(args: argparse.Namespace) -> int:
    receipt = _read_receipt(args.receipt)
    presence = verify_existing_archive(args.cold_root, receipt)
    return _write_json(presence)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receipt-indexed cold archive helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--library-dir", required=True)
    enqueue.add_argument("--receipt", required=True)
    enqueue.add_argument("--cold-root", default="")
    enqueue.set_defaults(func=cmd_enqueue)

    show = sub.add_parser("show-job")
    show.add_argument("--library-dir", required=True)
    show.add_argument("--receipt-id", required=True)
    show.add_argument("--allow-missing", action="store_true")
    show.set_defaults(func=cmd_show_job)

    set_state = sub.add_parser("set-job-state")
    set_state.add_argument("--library-dir", required=True)
    set_state.add_argument("--receipt-id", required=True)
    set_state.add_argument("--state", required=True)
    set_state.add_argument("--detail", default="")
    set_state.set_defaults(func=cmd_set_job_state)

    publish = sub.add_parser("publish")
    publish.add_argument("--library-dir", required=True)
    publish.add_argument("--receipt", required=True)
    publish.add_argument("--cold-root", required=True)
    publish.add_argument("--source-hub", required=True)
    publish.set_defaults(func=cmd_publish)

    complete = sub.add_parser("complete")
    complete.add_argument("--library-dir", required=True)
    complete.add_argument("--receipt-id", required=True)
    complete.add_argument("--cold-root", default="")
    complete.set_defaults(func=cmd_complete)

    verify = sub.add_parser("verify-presence")
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--cold-root", required=True)
    verify.set_defaults(func=cmd_verify_presence)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        ColdArchiveError,
        source_attested.SourceAttestedAcquisitionError,
        model_library.ModelLibraryError,
        model_identity.ModelIdentityError,
    ) as exc:
        print(f"model-library: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
