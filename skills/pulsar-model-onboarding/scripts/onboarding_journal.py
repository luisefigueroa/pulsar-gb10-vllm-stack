#!/usr/bin/env python3
"""Skill-local append-only onboarding journal.

Orchestration recovery state only. Not a sixth ADR object, not evidence,
and not a status or issuance authority.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import model_identity, model_serving_release, terminal_format

SCHEMA_VERSION = 1
JOURNAL_KIND = "pulsar-model-onboarding-journal"
EVENT_KIND = "pulsar-model-onboarding-journal-event"
HEADER_NAME = "header.json"
EVENTS_NAME = "events.jsonl"
DEFAULT_RELATIVE_ROOT = Path("experiments") / "model-onboarding" / "workflows"
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
RAW_ENV_VALUE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x1f\x7f]")
FORBIDDEN_RELATIVE_ROOTS = (
    Path("models"),
    Path("models") / "model-serving-releases",
    Path(".git"),
    Path("skills"),
)
ALLOWED_REFERENCE_PREFIXES = (
    "experiments/model-onboarding/",
    "experiments/model-serving-release-attempts/",
    "experiments/model-serving-release-captures/",
    "experiments/release-candidates/",
    "results/",
)
ALLOWED_PHASES = {
    "journal-start",
    "criteria",
    "acquisition",
    "catalog",
    "manifest",
    "distribution",
    "verification-barrier",
    "release-plan",
    "launch",
    "measure",
    "attempt-compose",
    "capture",
    "assemble",
    "verify-candidate",
    "handoff",
    "cleanup",
    "interrupted",
    "gap",
}
ALLOWED_OUTCOMES = {
    "started",
    "confirmed",
    "refused",
    "completed",
    "frozen",
    "failed",
    "interrupted",
    "gap",
    "recorded",
    "verified",
    "cleaned",
}
ALLOWED_ID_KEYS = {
    "exact_revision",
    "release_id",
    "contract_id",
    "launch_id",
    "server_boot_id",
    "compare_attempt_id",
    "benchmark_attempt_id",
    "capture_candidate_id",
    "bundle_id",
    "run_record_id",
    "manifest_digest",
    "image_digest",
    "receipt_id",
    "source_digest",
    "approval_id",
}
BOUND_ID_KEYS = {
    "exact_revision",
    "release_id",
    "contract_id",
    "receipt_id",
    "source_digest",
    "approval_id",
}
HEADER_FIELDS = {
    "schema_version",
    "kind",
    "workflow_id",
    "profile",
    "public_model_id",
    "repository_base_commit",
    "profile_base_commit",
    "created_at",
    "header_hash",
}
EVENT_FIELDS = {
    "schema_version",
    "kind",
    "seq",
    "recorded_at",
    "phase",
    "outcome",
    "choices",
    "ids",
    "references",
    "prev_hash",
    "event_hash",
}
FORBIDDEN_OBJECT_KINDS = {
    "pulsar-model-serving-release",
    "pulsar-validation-contract",
    "pulsar-model-serving-release-run-record",
    "pulsar-model-serving-release-evidence-bundle",
    "pulsar-model-serving-release-validation-decision",
    "pulsar-model-serving-release-capture-attempt-spec",
    "pulsar-model-serving-release-capture-candidate",
    "pulsar-model-serving-release-plan-candidate",
    "pulsar-expected-model-seal",
    "pulsar-validation-bundle",
}
class OnboardingJournalError(ValueError):
    """A journal identity, privacy, or integrity failure."""


def fail(message: str) -> None:
    raise OnboardingJournalError(message)


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def digest_object(value: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(value)


def screen_public_string(value: Any, *, label: str) -> str:
    try:
        text = model_serving_release.validate_public_string_value(
            value, label=label
        )
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))
    if CONTROL_CHARACTER_RE.search(text):
        fail(f"{label} contains a control character")
    if RAW_ENV_VALUE_RE.match(text):
        fail(f"{label} contains a raw environment value")
    if any(kind in text for kind in FORBIDDEN_OBJECT_KINDS):
        fail(f"{label} embeds a release, contract, run, bundle, or decision object")
    return text


JOURNAL_PRIVATE_KEYS = model_serving_release.PRIVATE_FIELD_NAMES | {
    "endpoint",
    "node",
    "nodes",
    "password",
    "token",
    "topology",
    "topology_id",
}


def reject_private_key(key: str, *, label: str) -> None:
    if key.lower() in JOURNAL_PRIVATE_KEYS:
        fail(f"{label} contains private field {key!r}")
    if model_serving_release._is_credential_field_name(key):
        fail(f"{label} contains credential-bearing field {key!r}")


def reject_embedded_object(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        kind = value.get("kind")
        if isinstance(kind, str) and kind in FORBIDDEN_OBJECT_KINDS:
            fail(f"{label} embeds a release, contract, run, bundle, or decision object")
        for key, item in value.items():
            reject_embedded_object(item, label=f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            reject_embedded_object(item, label=f"{label}[{index}]")


def validate_workflow_id(value: Any) -> str:
    text = screen_public_string(value, label="workflow_id")
    if WORKFLOW_ID_RE.fullmatch(text) is None or text in {".", ".."}:
        fail("workflow_id is invalid")
    return text


def validate_profile(value: Any) -> str:
    text = screen_public_string(value, label="profile")
    if PROFILE_RE.fullmatch(text) is None:
        fail("profile is invalid")
    return text


def validate_public_model_id(value: Any) -> str:
    text = screen_public_string(value, label="public_model_id")
    if model_identity.HF_MODEL_ID_RE.fullmatch(text) is None:
        fail("public_model_id is invalid")
    return text


def validate_git_commit(value: Any, *, label: str) -> str:
    text = screen_public_string(value, label=label)
    if GIT_COMMIT_RE.fullmatch(text) is None:
        fail(f"{label} must be a 40-character lowercase git commit")
    return text


def validate_reference(value: Any, *, label: str) -> str:
    text = screen_public_string(value, label=label)
    if text.startswith("/") or text.startswith("~") or "\\" in text:
        fail(f"{label} must be a repository-relative path")
    parts = Path(text).parts
    if not parts or ".." in parts or parts[0] == "/":
        fail(f"{label} is not a safe repository-relative path")
    if not any(text == prefix.rstrip("/") or text.startswith(prefix)
               for prefix in ALLOWED_REFERENCE_PREFIXES):
        fail(f"{label} is not a permitted candidate or evidence reference")
    return text


def validate_choices(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        fail("choices must be an object")
    loaded: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            fail("choices has an invalid key")
        reject_private_key(key, label="choices")
        screen_public_string(key, label="choices key")
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            fail("choices contains a raw environment value")
        text = screen_public_string(item, label=f"choices.{key}")
        if text.lstrip().startswith(("{", "[")):
            try:
                structured = json.loads(text)
            except json.JSONDecodeError:
                structured = None
            if isinstance(structured, (dict, list)):
                fail(f"choices.{key} embeds a structured object")
        loaded[key] = text
    reject_embedded_object(loaded, label="choices")
    return loaded


def validate_ids(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        fail("ids must be an object")
    extra = set(value) - ALLOWED_ID_KEYS
    if extra:
        fail(f"ids has unexpected fields: {sorted(extra)}")
    loaded: dict[str, str] = {}
    for key, item in value.items():
        reject_private_key(key, label="ids")
        text = screen_public_string(item, label=f"ids.{key}")
        if key == "exact_revision":
            if model_identity.HF_COMMIT_RE.fullmatch(text) is None:
                fail("ids.exact_revision must be a Hugging Face commit")
        elif key == "image_digest":
            if re.fullmatch(r"sha256:[0-9a-f]{64}", text) is None:
                fail("ids.image_digest must be sha256:<digest>")
        elif key in {
            "release_id",
            "contract_id",
            "launch_id",
            "server_boot_id",
            "capture_candidate_id",
            "bundle_id",
            "run_record_id",
            "manifest_digest",
            "receipt_id",
            "source_digest",
            "approval_id",
        }:
            if model_identity.SHA256_HEX_RE.fullmatch(text) is None:
                fail(f"ids.{key} must be a SHA-256 hex digest")
        elif key in {"compare_attempt_id", "benchmark_attempt_id"}:
            if model_identity.SAFE_REV.fullmatch(text) is None:
                fail(f"ids.{key} must be a safe identifier")
        loaded[key] = text
    return loaded


def bound_ids(events: list[dict[str, Any]]) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for event in events:
        for key in BOUND_ID_KEYS:
            value = event["ids"].get(key)
            if value is None:
                continue
            previous = bindings.get(key)
            if previous is not None and previous != value:
                fail(f"journal rebinds {key}")
            bindings[key] = value
    return bindings


def match_bound_ids(
    events: list[dict[str, Any]], expected_ids: dict[str, str]
) -> None:
    bindings = bound_ids(events)
    for key, expected in expected_ids.items():
        if key not in BOUND_ID_KEYS:
            fail(f"resume identity cannot include mutable id {key}")
        if key not in bindings:
            fail(f"resume identity {key} is not yet bound")
        if bindings[key] != expected:
            fail(f"resume identity mismatch for {key}")


def validate_references(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        fail("references must be a list")
    return [
        validate_reference(item, label=f"references[{index}]")
        for index, item in enumerate(value)
    ]


def _lstat(path: Path):
    try:
        return path.lstat()
    except OSError as exc:
        fail(f"cannot stat {path.name}: {exc}")


def require_private_dir(path: Path, *, label: str) -> None:
    info = _lstat(path)
    if stat.S_ISLNK(info.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        fail(f"{label} must be a directory")
    if stat.S_IMODE(info.st_mode) != 0o700:
        fail(f"{label} must have mode 0700")


def require_private_file(path: Path, *, label: str) -> None:
    info = _lstat(path)
    if stat.S_ISLNK(info.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        fail(f"{label} must have mode 0600")


def reject_symlink_parents(path: Path) -> None:
    current = path
    seen: set[Path] = set()
    while current != current.parent:
        if current in seen:
            fail("journal path has a cyclic parent")
        seen.add(current)
        try:
            info = current.lstat()
        except OSError:
            current = current.parent
            continue
        if stat.S_ISLNK(info.st_mode):
            fail("journal path must not traverse a symlink")
        current = current.parent


def resolve_repo_root(value: str | None) -> Path:
    root = Path(value).resolve() if value else _REPO_ROOT
    if not root.is_dir():
        fail("repository root is not a directory")
    return root


def default_journal_dir(repo_root: Path, workflow_id: str) -> Path:
    return repo_root / DEFAULT_RELATIVE_ROOT / workflow_id


def validate_journal_location(path: Path, *, repo_root: Path) -> Path:
    reject_symlink_parents(path)
    resolved = path if path.exists() else path
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        fail(f"journal directory cannot be resolved: {exc}")
    if resolved == Path("/") or resolved == repo_root:
        fail("journal directory must not be / or the repository root")
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError:
        return resolved
    if relative == Path("."):
        fail("journal directory must not be the repository root")
    if any(relative == forbidden or forbidden in relative.parents
           for forbidden in FORBIDDEN_RELATIVE_ROOTS):
        fail("journal directory is not under a permitted local-state boundary")
    prefix_length = len(DEFAULT_RELATIVE_ROOT.parts)
    if (
        relative.parts[:prefix_length] != DEFAULT_RELATIVE_ROOT.parts
        or len(relative.parts) != prefix_length + 1
    ):
        fail(
            "in-repository journals must stay directly under "
            "experiments/model-onboarding/workflows/"
        )
    return resolved


def header_payload(header: dict[str, Any]) -> dict[str, Any]:
    return {key: header[key] for key in HEADER_FIELDS if key != "header_hash"}


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {key: event[key] for key in EVENT_FIELDS if key != "event_hash"}


def build_header(
    *,
    workflow_id: str,
    profile: str,
    public_model_id: str,
    repository_base_commit: str,
    profile_base_commit: str,
    created_at: str,
) -> dict[str, Any]:
    header = {
        "schema_version": SCHEMA_VERSION,
        "kind": JOURNAL_KIND,
        "workflow_id": workflow_id,
        "profile": profile,
        "public_model_id": public_model_id,
        "repository_base_commit": repository_base_commit,
        "profile_base_commit": profile_base_commit,
        "created_at": created_at,
    }
    header["header_hash"] = digest_object(header)
    return header


def validate_header(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("journal header must be an object")
    extra = set(value) - HEADER_FIELDS
    if extra:
        fail(f"journal header has unexpected fields: {sorted(extra)}")
    missing = HEADER_FIELDS - set(value)
    if missing:
        fail(f"journal header is missing fields: {sorted(missing)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        fail("journal header schema_version is unsupported")
    if value.get("kind") != JOURNAL_KIND:
        fail("journal header kind is invalid")
    header = {
        "schema_version": SCHEMA_VERSION,
        "kind": JOURNAL_KIND,
        "workflow_id": validate_workflow_id(value.get("workflow_id")),
        "profile": validate_profile(value.get("profile")),
        "public_model_id": validate_public_model_id(value.get("public_model_id")),
        "repository_base_commit": validate_git_commit(
            value.get("repository_base_commit"),
            label="repository_base_commit",
        ),
        "profile_base_commit": validate_git_commit(
            value.get("profile_base_commit"),
            label="profile_base_commit",
        ),
        "created_at": screen_public_string(
            value.get("created_at"), label="created_at"
        ),
        "header_hash": screen_public_string(
            value.get("header_hash"), label="header_hash"
        ),
    }
    expected = digest_object(header_payload(header))
    if header["header_hash"] != expected:
        fail("journal header hash does not match contents")
    return header


def validate_event(value: Any, *, seq: int, prev_hash: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"event {seq} must be an object")
    extra = set(value) - EVENT_FIELDS
    if extra:
        fail(f"event {seq} has unexpected fields: {sorted(extra)}")
    missing = EVENT_FIELDS - set(value)
    if missing:
        fail(f"event {seq} is missing fields: {sorted(missing)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        fail(f"event {seq} schema_version is unsupported")
    if value.get("kind") != EVENT_KIND:
        fail(f"event {seq} kind is invalid")
    if value.get("seq") != seq:
        fail(f"event sequence is broken at {seq}")
    event = {
        "schema_version": SCHEMA_VERSION,
        "kind": EVENT_KIND,
        "seq": seq,
        "recorded_at": screen_public_string(
            value.get("recorded_at"), label=f"event {seq} recorded_at"
        ),
        "phase": screen_public_string(value.get("phase"), label=f"event {seq} phase"),
        "outcome": screen_public_string(
            value.get("outcome"), label=f"event {seq} outcome"
        ),
        "choices": validate_choices(value.get("choices")),
        "ids": validate_ids(value.get("ids")),
        "references": validate_references(value.get("references")),
        "prev_hash": screen_public_string(
            value.get("prev_hash"), label=f"event {seq} prev_hash"
        ),
        "event_hash": screen_public_string(
            value.get("event_hash"), label=f"event {seq} event_hash"
        ),
    }
    if event["phase"] not in ALLOWED_PHASES:
        fail(f"event {seq} phase is unsupported")
    if event["outcome"] not in ALLOWED_OUTCOMES:
        fail(f"event {seq} outcome is unsupported")
    if event["prev_hash"] != prev_hash:
        fail(f"event {seq} hash chain is broken")
    expected = digest_object(event_payload(event))
    if event["event_hash"] != expected:
        fail(f"event {seq} hash does not match contents")
    return event


def parse_choice_args(values: list[str] | None) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            fail("choice must be KEY=VALUE")
        key, value = item.split("=", 1)
        loaded[key] = value
    return validate_choices(loaded)


def parse_id_args(values: list[str] | None) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            fail("id must be KEY=VALUE")
        key, value = item.split("=", 1)
        loaded[key] = value
    return validate_ids(loaded)


def exclusive_mkdir(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        fail("journal directory already exists")
    except OSError as exc:
        fail(f"cannot create journal directory: {exc}")


def exclusive_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        fail(f"cannot create {path.name}: {exc}")
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


def append_line(path: Path, line: str) -> None:
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        fail(f"cannot append {path.name}: {exc}")
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    dir_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def read_header_file(path: Path) -> dict[str, Any]:
    require_private_file(path, label="header.json")
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("journal header is not valid JSON")
    return validate_header(payload)


def read_events_file(path: Path, *, header_hash: str) -> list[dict[str, Any]]:
    require_private_file(path, label="events.jsonl")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        fail(f"cannot read events.jsonl: {exc}")
    if raw == b"":
        return []
    if not raw.endswith(b"\n"):
        fail("journal events are truncated")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        fail("journal events are not valid UTF-8")
    events: list[dict[str, Any]] = []
    prev_hash = header_hash
    for index, line in enumerate(text.splitlines(), start=1):
        if not line:
            fail("journal events contain an empty line")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            fail(f"event {index} is not valid JSON")
        event = validate_event(payload, seq=index, prev_hash=prev_hash)
        events.append(event)
        prev_hash = event["event_hash"]
    return events


def load_journal(journal_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    require_private_dir(journal_dir, label="journal directory")
    names = set(os.listdir(journal_dir))
    if names != {HEADER_NAME, EVENTS_NAME}:
        fail("journal directory has unexpected files")
    header = read_header_file(journal_dir / HEADER_NAME)
    events = read_events_file(
        journal_dir / EVENTS_NAME, header_hash=header["header_hash"]
    )
    bound_ids(events)
    return header, events


def match_resume_identity(
    header: dict[str, Any],
    *,
    workflow_id: str | None,
    profile: str | None,
    public_model_id: str | None,
    repository_base_commit: str | None,
    profile_base_commit: str | None,
) -> None:
    checks = {
        "workflow_id": workflow_id,
        "profile": profile,
        "public_model_id": public_model_id,
        "repository_base_commit": repository_base_commit,
        "profile_base_commit": profile_base_commit,
    }
    for key, supplied in checks.items():
        if supplied is None:
            continue
        if key == "workflow_id":
            expected = validate_workflow_id(supplied)
        elif key == "profile":
            expected = validate_profile(supplied)
        elif key == "public_model_id":
            expected = validate_public_model_id(supplied)
        else:
            expected = validate_git_commit(supplied, label=key)
        if header[key] != expected:
            fail(f"resume identity mismatch for {key}")


def initialize_journal(
    *,
    journal_dir: Path,
    workflow_id: str,
    profile: str,
    public_model_id: str,
    repository_base_commit: str,
    profile_base_commit: str,
) -> dict[str, Any]:
    parent = journal_dir.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
    if journal_dir.exists():
        fail("journal directory already exists")
    exclusive_mkdir(journal_dir)
    header = build_header(
        workflow_id=workflow_id,
        profile=profile,
        public_model_id=public_model_id,
        repository_base_commit=repository_base_commit,
        profile_base_commit=profile_base_commit,
        created_at=utc_now(),
    )
    exclusive_write(
        journal_dir / HEADER_NAME,
        model_identity.pretty_json_bytes(header),
    )
    exclusive_write(journal_dir / EVENTS_NAME, b"")
    return header


def append_event(
    journal_dir: Path,
    *,
    phase: str,
    outcome: str,
    choices: dict[str, str],
    ids: dict[str, str],
    references: list[str],
) -> dict[str, Any]:
    header, events = load_journal(journal_dir)
    bindings = bound_ids(events)
    for key in BOUND_ID_KEYS:
        if key in ids and key in bindings and ids[key] != bindings[key]:
            fail(f"journal cannot rebind {key}")
    event = {
        "schema_version": SCHEMA_VERSION,
        "kind": EVENT_KIND,
        "seq": len(events) + 1,
        "recorded_at": utc_now(),
        "phase": phase,
        "outcome": outcome,
        "choices": choices,
        "ids": ids,
        "references": references,
        "prev_hash": (
            events[-1]["event_hash"] if events else header["header_hash"]
        ),
    }
    event["event_hash"] = digest_object(event)
    validated = validate_event(
        event, seq=event["seq"], prev_hash=event["prev_hash"]
    )
    append_line(
        journal_dir / EVENTS_NAME,
        json.dumps(validated, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    )
    return validated


def journal_report(
    header: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "pulsar-model-onboarding-journal-report",
        "authority": "none",
        "evidence": False,
        "workflow_id": header["workflow_id"],
        "profile": header["profile"],
        "public_model_id": header["public_model_id"],
        "repository_base_commit": header["repository_base_commit"],
        "profile_base_commit": header["profile_base_commit"],
        "created_at": header["created_at"],
        "event_count": len(events),
        "events": [
            {
                "seq": event["seq"],
                "recorded_at": event["recorded_at"],
                "phase": event["phase"],
                "outcome": event["outcome"],
                "choices": event["choices"],
                "ids": event["ids"],
                "references": event["references"],
            }
            for event in events
        ],
    }


def emit_human(report: dict[str, Any], *, width: int | None = None) -> None:
    writer = terminal_format.TerminalWriter(width=width)
    writer.emit("Onboarding journal")
    writer.field("Workflow", report["workflow_id"], label_width=10)
    writer.field("Profile", report["profile"], label_width=10)
    writer.field("Model", report["public_model_id"], label_width=10)
    writer.field("Repo base", report["repository_base_commit"], label_width=10)
    writer.field("Events", str(report["event_count"]), label_width=10)
    writer.field("Authority", "none", label_width=10)
    writer.blank()
    if not report["events"]:
        writer.emit("No events recorded.")
        return
    writer.emit("Events")
    for event in report["events"]:
        writer.field(str(event["seq"]), f"{event['phase']}  {event['outcome']}")
        for key, value in event["choices"].items():
            writer.field(key, value, indent=2, label_width=12)
        for key, value in event["ids"].items():
            writer.field(key, value, indent=2, label_width=12)
        for reference in event["references"]:
            writer.field("ref", reference, indent=2, label_width=12)


def emit_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(model_identity.pretty_json_bytes(payload).decode("utf-8"))


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--journal-dir")
    parser.add_argument("--json", action="store_true")


def add_identity_flags(parser: argparse.ArgumentParser, *, required: bool) -> None:
    parser.add_argument("--workflow-id", required=required)
    parser.add_argument("--profile", required=required)
    parser.add_argument("--public-model-id", required=required)
    parser.add_argument("--repository-base-commit", required=required)
    parser.add_argument("--profile-base-commit", required=required)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append-only onboarding journal (recovery state, not evidence)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    initialize = sub.add_parser("initialize", help="create an exclusive journal")
    add_common_flags(initialize)
    add_identity_flags(initialize, required=True)

    append = sub.add_parser("append", help="append one recovery event")
    add_common_flags(append)
    append.add_argument("--phase", required=True)
    append.add_argument("--outcome", required=True)
    append.add_argument("--choice", action="append", default=[])
    append.add_argument("--id", action="append", default=[], dest="ids")
    append.add_argument("--reference", action="append", default=[])

    verify = sub.add_parser("verify", help="verify integrity and optional resume identity")
    add_common_flags(verify)
    add_identity_flags(verify, required=False)
    verify.add_argument(
        "--id",
        action="append",
        default=[],
        dest="ids",
        help="require an immutable exact_revision, release_id, or contract_id binding",
    )

    show = sub.add_parser("show", help="print a scan-friendly journal summary")
    add_common_flags(show)
    return parser


def resolve_command_dir(args: argparse.Namespace, *, workflow_id: str | None) -> Path:
    repo_root = resolve_repo_root(args.repo_root)
    if args.journal_dir:
        return validate_journal_location(Path(args.journal_dir), repo_root=repo_root)
    if not workflow_id:
        fail("--journal-dir or --workflow-id is required")
    return validate_journal_location(
        default_journal_dir(repo_root, workflow_id), repo_root=repo_root
    )


def command_initialize(args: argparse.Namespace) -> dict[str, Any]:
    journal_dir = resolve_command_dir(args, workflow_id=args.workflow_id)
    header = initialize_journal(
        journal_dir=journal_dir,
        workflow_id=validate_workflow_id(args.workflow_id),
        profile=validate_profile(args.profile),
        public_model_id=validate_public_model_id(args.public_model_id),
        repository_base_commit=validate_git_commit(
            args.repository_base_commit, label="repository_base_commit"
        ),
        profile_base_commit=validate_git_commit(
            args.profile_base_commit, label="profile_base_commit"
        ),
    )
    report = journal_report(header, [])
    report["journal_state"] = "initialized"
    return report


def command_append(args: argparse.Namespace) -> dict[str, Any]:
    if not args.journal_dir:
        fail("append requires --journal-dir")
    journal_dir = resolve_command_dir(args, workflow_id=None)
    event = append_event(
        journal_dir,
        phase=args.phase,
        outcome=args.outcome,
        choices=parse_choice_args(args.choice),
        ids=parse_id_args(args.ids),
        references=[
            validate_reference(item, label=f"references[{index}]")
            for index, item in enumerate(args.reference)
        ],
    )
    header, events = load_journal(journal_dir)
    report = journal_report(header, events)
    report["appended_seq"] = event["seq"]
    return report


def command_verify(args: argparse.Namespace) -> dict[str, Any]:
    journal_dir = resolve_command_dir(args, workflow_id=args.workflow_id)
    header, events = load_journal(journal_dir)
    match_resume_identity(
        header,
        workflow_id=args.workflow_id,
        profile=args.profile,
        public_model_id=args.public_model_id,
        repository_base_commit=args.repository_base_commit,
        profile_base_commit=args.profile_base_commit,
    )
    match_bound_ids(events, parse_id_args(args.ids))
    report = journal_report(header, events)
    report["verified"] = True
    return report


def command_show(args: argparse.Namespace) -> dict[str, Any]:
    if not args.journal_dir:
        fail("show requires --journal-dir")
    journal_dir = resolve_command_dir(args, workflow_id=None)
    header, events = load_journal(journal_dir)
    return journal_report(header, events)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "initialize": command_initialize,
        "append": command_append,
        "verify": command_verify,
        "show": command_show,
    }
    try:
        report = commands[args.command](args)
    except OnboardingJournalError as exc:
        print(f"onboarding-journal: {exc}", file=sys.stderr)
        return 2
    if args.json:
        emit_json(report)
    else:
        emit_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
