#!/usr/bin/env python3
"""Build a measured ADR 0017 release spec from a current profile plus receipt.

Bash sources the conf and passes the same projected fields as
``append_loaded_profile_contract_args``. Profile-to-identity mapping lives in
``scripts/release_consumer.py`` (lab imports stack). This module owns
verification, gap reports, and JSON output. It does not write ``releases/``,
the catalog, or profile status.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from release_spec import (  # noqa: E402
    ReleaseSpecError,
    pretty_json_bytes,
    spec_id_for,
    verify_spec,
)
from release_spec.schema import KIND, SCHEMA_VERSION  # noqa: E402

try:
    from scripts.model_library_receipt import (
        SourceAttestedAcquisitionError,
        validate_source_attested_acquisition_receipt,
    )
    from scripts.release_consumer import (
        ReleaseConsumerError,
        argv_from_identity,
        build_profile_identity,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script invocation
    from model_library_receipt import (  # type: ignore[no-redef]
        SourceAttestedAcquisitionError,
        validate_source_attested_acquisition_receipt,
    )
    from release_consumer import (  # type: ignore[no-redef]
        ReleaseConsumerError,
        argv_from_identity,
        build_profile_identity,
    )

TRUSTED_OUTPUT_DIRS = ("releases", "models")


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
GAP_REPORT_KIND = "pulsar-release-spec-gap-report"
GAP_REPORT_SCHEMA_VERSION = 1
GAP_CLASSES = frozenset({"expected", "lossy", "blocking"})
GAP_ENTRY_KEYS = ("class", "field", "reason", "section", "source")
EXPECTED_EMPTY_SECTIONS = (
    ("measurements", "WP1.3 generator emits empty measurements"),
    ("baselines", "WP1.3 generator emits empty baselines"),
    ("evidence", "WP1.3 generator emits empty evidence"),
    ("review", "measured specs carry an empty review object"),
)
EXPECTED_OVERLAY_FIELDS = (
    ("SERVED_NAME", "deployment overlay (ADR 0017 decision 5)"),
    ("PORT", "deployment overlay (ADR 0017 decision 5)"),
)
EXPECTED_DISPLAY_FIELDS = (
    ("STATUS", "display-only profile field; not spec review.status"),
    ("NOTES", "display-only profile field; not spec review.status"),
    ("FIRST_RUN_CANDIDATE", "display-only profile field; not spec review.status"),
    ("FAMILY_RECOMMENDED", "display-only profile field; not spec review.status"),
    ("RECOMMENDED_SPEC", "display-only profile field; not spec review.status"),
    ("PROFILE_FAMILY", "display-only profile field; not spec review.status"),
    ("VARIANT_LABEL", "display-only profile field; not spec review.status"),
    ("PROFILE_PURPOSE", "display-only profile field; not spec review.status"),
    ("MODEL_SERVING_RELEASE_ID", "display-only profile field; not spec review.status"),
)
LOSSY_CAPACITY_FIELDS = (
    ("WEIGHTS_GIB", "capacity advisory; not identity"),
    ("WEIGHTS_RAM_GIB", "capacity advisory; not identity"),
    ("KV_GIB", "capacity advisory; not identity"),
    ("OVERHEAD_GIB", "capacity advisory; not identity"),
    ("MEM_MIN_FREE_GIB", "capacity advisory; not identity"),
)
LOSSY_TOPOLOGY_FIELDS = (
    ("TOPOLOGY_CLASS", "topology extra; spec geometry uses fabric, not this field"),
    ("MIN_RAILS_PER_PAIR", "topology extra; spec geometry uses fabric, not this field"),
)


class ReleaseSpecGenerateError(ValueError):
    """The requested spec cannot be generated from the supplied inputs."""


def fail(message: str) -> None:
    raise ReleaseSpecGenerateError(message)


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
    except ReleaseSpecGenerateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")


def _gap(
    *,
    class_name: str,
    section: str,
    field: str,
    source: str,
    reason: str,
) -> dict[str, str]:
    if class_name not in GAP_CLASSES:
        fail(f"gap class {class_name!r} is unsupported")
    return {
        "class": class_name,
        "section": section,
        "field": field,
        "source": source,
        "reason": reason,
    }


def sort_gaps(gaps: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(gaps, key=lambda item: (item["class"], item["section"], item["field"]))


def conf_assignment_names(conf_path: pathlib.Path) -> set[str]:
    """Return conf keys assigned outside comments."""
    names: set[str] = set()
    try:
        text = conf_path.read_text(encoding="utf-8")
    except OSError:
        return names
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, _value = line.partition("=")
        if separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            names.add(name)
    return names


def expected_and_lossy_gaps(
    *,
    profile: str,
    draft_path: pathlib.Path | None,
) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    for section, reason in EXPECTED_EMPTY_SECTIONS:
        gaps.append(
            _gap(
                class_name="expected",
                section=section,
                field=section,
                source="none",
                reason=reason,
            )
        )
    for field, reason in EXPECTED_OVERLAY_FIELDS + EXPECTED_DISPLAY_FIELDS:
        gaps.append(
            _gap(
                class_name="expected",
                section="profile",
                field=field,
                source=f"conf:{field}",
                reason=reason,
            )
        )
    assigned: set[str] = set()
    if draft_path is not None:
        assigned = conf_assignment_names(draft_path)
    for field, reason in LOSSY_CAPACITY_FIELDS + LOSSY_TOPOLOGY_FIELDS:
        if field in assigned:
            gaps.append(
                _gap(
                    class_name="lossy",
                    section="profile",
                    field=field,
                    source=f"conf:{field}",
                    reason=reason,
                )
            )
    return gaps


def build_gap_report(
    *,
    profile: str,
    spec_decode: bool,
    spec_id: str | None,
    gaps: list[dict[str, str]],
) -> dict[str, Any]:
    generated = spec_id is not None
    if generated and SHA256_HEX_RE.fullmatch(spec_id or "") is None:
        fail("gap report spec_id must be a 64-character lowercase hex digest")
    if not generated and spec_id is not None:
        fail("gap report spec_id must be null when generation failed")
    ordered = sort_gaps(gaps)
    for item in ordered:
        if tuple(sorted(item)) != tuple(sorted(GAP_ENTRY_KEYS)):
            fail("gap entry fields differ")
    return {
        "schema_version": GAP_REPORT_SCHEMA_VERSION,
        "kind": GAP_REPORT_KIND,
        "profile": profile,
        "spec_decode": spec_decode,
        "generated": generated,
        "spec_id": spec_id,
        "gaps": ordered,
    }


def _load_receipt_identity(
    receipt_path: str | pathlib.Path | None,
) -> tuple[str | None, str | None, list[dict[str, Any]] | None, list[dict[str, str]]]:
    """Return (model_id, snapshot_revision, files, blocking_gaps) from a receipt.

    The receipt must pass the schema owner's full validation
    (`model_library_receipt.validate_source_attested_acquisition_receipt`):
    kind, source inventory, identity and approval links, and `receipt_id`.
    A bare file list is not a receipt and never becomes a spec manifest.
    """

    def blocked(reason: str, *, field: str = "snapshot_manifest") -> tuple[None, None, None, list[dict[str, str]]]:
        return None, None, None, [
            _gap(
                class_name="blocking",
                section="identity",
                field=field,
                source="receipt",
                reason=reason,
            )
        ]

    missing = "download receipt is required; refusing to synthesize a snapshot manifest"
    if receipt_path is None:
        return blocked(missing)
    path = pathlib.Path(receipt_path)
    if not path.is_file() or path.is_symlink():
        return blocked(missing)
    try:
        document = load_json(path)
    except ReleaseSpecGenerateError as exc:
        return blocked(str(exc))
    try:
        receipt = validate_source_attested_acquisition_receipt(document)
    except SourceAttestedAcquisitionError as exc:
        return blocked(f"download receipt failed validation: {exc}")
    revision = receipt["snapshot_revision"]
    if COMMIT_RE.fullmatch(revision) is None:
        return blocked(
            "receipt snapshot_revision must be a 40-character lowercase hex commit",
            field="snapshot_revision",
        )
    return receipt["model_id"], revision, list(receipt["observed_manifest"]["files"]), []


def build_spec_from_profile(
    *,
    profile: str,
    model_id: str,
    image: str,
    nodes: int,
    gpu_mem_util: str,
    engine_args: list[str],
    container_env: list[str],
    spec_decode_args: list[str],
    platform_id: str,
    stack_version: str,
    spec_decode: bool,
    receipt_path: str | pathlib.Path | None,
    repo_root: str | pathlib.Path | None = None,
    draft_path: str | pathlib.Path | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return ``(spec, gap_report)``. Spec is None when generation is blocked.

    ``draft_path`` is the conf-format draft the profile fields came from; its
    assigned capacity fields are reported as lossy gaps. ``repo_root`` is kept
    for callers that pass it and is not read here.
    """
    del repo_root
    draft = pathlib.Path(draft_path) if draft_path is not None else None
    gaps = expected_and_lossy_gaps(profile=profile, draft_path=draft)
    blocking: list[dict[str, str]] = []

    receipt_model, revision, files, receipt_blocking = _load_receipt_identity(
        receipt_path
    )
    blocking.extend(receipt_blocking)
    identity, identity_blocking = build_profile_identity(
        model_id=model_id,
        image=image,
        nodes=nodes,
        gpu_mem_util=gpu_mem_util,
        engine_args=list(engine_args),
        container_env=list(container_env),
        spec_decode_args=list(spec_decode_args),
        spec_decode=spec_decode,
        platform_id=platform_id,
        snapshot_revision=revision,
        files=files,
        receipt_model_id=receipt_model,
    )
    blocking.extend(identity_blocking)
    gaps.extend(blocking)
    if identity is None:
        return None, build_gap_report(
            profile=profile,
            spec_decode=spec_decode,
            spec_id=None,
            gaps=gaps,
        )

    try:
        spec_id = spec_id_for(identity)
        document = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "spec_id": spec_id,
            "state": "measured",
            "identity": identity,
            "launch_contract": {
                "stack_version": stack_version,
                "argv": argv_from_identity(identity),
            },
            "measurements": [],
            "baselines": [],
            "evidence": [],
            "review": {},
        }
        spec = verify_spec(document)
    except (ReleaseSpecError, ReleaseConsumerError) as exc:
        gaps.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="identity",
                source="generator",
                reason=str(exc),
            )
        )
        return None, build_gap_report(
            profile=profile,
            spec_decode=spec_decode,
            spec_id=None,
            gaps=gaps,
        )
    return spec, build_gap_report(
        profile=profile,
        spec_decode=spec_decode,
        spec_id=spec["spec_id"],
        gaps=gaps,
    )


def add_profile_projection_arguments(parser: argparse.ArgumentParser) -> None:
    """Accept the exact key set from ``append_loaded_profile_contract_args``."""
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--served-name", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--nodes", type=int, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--gpu-mem-util", required=True)
    parser.add_argument("--engine-arg", action="append", default=[])
    parser.add_argument("--container-env", action="append", default=[])
    parser.add_argument("--spec-decode-arg", action="append", default=[])
    parser.add_argument("--recommended-spec", type=int, required=True)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a measured ADR 0017 release spec from a current profile"
    )
    add_profile_projection_arguments(parser)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--stack-version", required=True)
    parser.add_argument("--platform-id", required=True)
    parser.add_argument("--spec-decode", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--gap-report")
    parser.add_argument("--draft", default="", help="conf-format draft the profile fields came from")
    return parser


def _write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty_json_bytes(value))


def _within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_output_locations(
    *,
    repo_root: pathlib.Path,
    out: pathlib.Path | None,
    gap_report: pathlib.Path | None,
) -> None:
    """Refuse drafts aimed at trusted registry directories or at each other.

    AGENTS.md: a deterministic draft has no authority; draft tools must fail
    when output targets trusted directories. `releases/` (ADR 0017 specs) and
    `models/` (profiles and the ADR 0004 registry) are trusted.
    """
    root = repo_root.resolve(strict=False)
    resolved: dict[str, pathlib.Path] = {}
    for label, candidate in (("--out", out), ("--gap-report", gap_report)):
        if candidate is None:
            continue
        target = candidate.resolve(strict=False)
        resolved[label] = target
        for name in TRUSTED_OUTPUT_DIRS:
            trusted = (root / name).resolve(strict=False)
            if target == trusted or _within(target, trusted):
                fail(f"{label} must not target the trusted {name}/ directory")
    if len(resolved) == 2 and resolved["--out"] == resolved["--gap-report"]:
        fail("--out and --gap-report must be different files")


def cmd_from_profile(args: argparse.Namespace) -> int:
    out_path = pathlib.Path(args.out) if args.out else None
    gap_path = pathlib.Path(args.gap_report) if args.gap_report else None
    validate_output_locations(
        repo_root=pathlib.Path(args.repo_root), out=out_path, gap_report=gap_path
    )
    existing_out = out_path is not None and (out_path.exists() or out_path.is_symlink())

    spec, report = build_spec_from_profile(
        profile=args.profile,
        model_id=args.model_id,
        image=args.image,
        nodes=args.nodes,
        gpu_mem_util=args.gpu_mem_util,
        engine_args=list(args.engine_arg or []),
        container_env=list(args.container_env or []),
        spec_decode_args=list(args.spec_decode_arg or []),
        platform_id=args.platform_id,
        stack_version=args.stack_version,
        spec_decode=bool(args.spec_decode),
        receipt_path=args.receipt,
        repo_root=args.repo_root,
        draft_path=args.draft or None,
    )
    if existing_out:
        report["generated"] = False
        report["spec_id"] = None
        report["gaps"] = sort_gaps(
            list(report["gaps"])
            + [
                _gap(
                    class_name="blocking",
                    section="launch_contract",
                    field="out",
                    source="cli:--out",
                    reason="refusing to overwrite an existing spec file",
                )
            ]
        )
        spec = None

    if gap_path is not None:
        _write_json(gap_path, report)

    if spec is None:
        blocking = [item for item in report["gaps"] if item["class"] == "blocking"]
        reason = blocking[0]["reason"] if blocking else "spec generation failed"
        print(f"error: {reason}", file=sys.stderr)
        return 1

    if out_path is not None:
        _write_json(out_path, spec)
        print(f"spec_id={spec['spec_id']} state={spec['state']}")
    else:
        sys.stdout.buffer.write(pretty_json_bytes(spec))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 2
    try:
        return cmd_from_profile(args)
    except (ReleaseSpecGenerateError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
