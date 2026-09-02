#!/usr/bin/env python3
"""Build a measured ADR 0017 release spec from a current profile plus receipt.

Bash sources the conf and passes the same projected fields as
``append_loaded_profile_contract_args``. This module owns mapping, verification,
and JSON output. It does not write ``releases/``, the catalog, or profile status.
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
    build_snapshot_manifest,
    identity_block,
    normalize_container_env,
    normalize_engine_args,
    pretty_json_bytes,
    spec_id_for,
    verify_spec,
)
from release_spec.schema import (  # noqa: E402
    FABRIC_LOCAL,
    FABRIC_ROCE_V2,
    FORBIDDEN_ENGINE_FLAGS,
    KIND,
    SCHEMA_VERSION,
)

try:
    from scripts.model_identity import IMAGE_DIGEST_RE
except ModuleNotFoundError:  # pragma: no cover - direct script invocation
    from model_identity import IMAGE_DIGEST_RE  # type: ignore[no-redef]


COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
GAP_REPORT_KIND = "pulsar-release-spec-gap-report"
GAP_REPORT_SCHEMA_VERSION = 1
GAP_CLASSES = frozenset({"expected", "lossy", "blocking"})
GAP_ENTRY_KEYS = ("class", "field", "reason", "section", "source")
STRUCTURED_PROFILE_FLAGS = (
    "--gpu-memory-utilization",
    "--pipeline-parallel-size",
    "--tensor-parallel-size",
    "-pp",
    "-tp",
)
PARALLELISM_CANONICAL = {
    "--tensor-parallel-size": "--tensor-parallel-size",
    "-tp": "--tensor-parallel-size",
    "--pipeline-parallel-size": "--pipeline-parallel-size",
    "-pp": "--pipeline-parallel-size",
}
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


def profile_image_digest(image: str) -> str:
    """Return ``sha256:<hex>`` from a conf IMAGE pin; fail if unpinned."""
    match = IMAGE_DIGEST_RE.search(image or "")
    if match is None:
        fail("profile image must be pinned by @sha256 digest")
    return "sha256:" + match.group(1)


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
    repo_root: pathlib.Path | None,
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
    if repo_root is not None:
        assigned = conf_assignment_names(repo_root / "models" / f"{profile}.conf")
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


def _flag_and_value(item: str, index: int, tokens: list[str]) -> tuple[str, str, int] | None:
    for flag in STRUCTURED_PROFILE_FLAGS:
        if item == flag:
            if index + 1 >= len(tokens):
                fail(f"profile {item} requires a value")
            return flag, tokens[index + 1], index + 2
        prefix = flag + "="
        if item.startswith(prefix):
            return flag, item[len(prefix) :], index + 1
    return None


def strip_profile_parallelism(engine_args: list[str]) -> tuple[int, int, list[str]]:
    """Mirror ``_profile_parallelism``: tp/pp default 1; GPU_MEM_UTIL must not repeat."""
    values = {"--tensor-parallel-size": 1, "--pipeline-parallel-size": 1}
    seen: set[str] = set()
    remaining: list[str] = []
    index = 0
    while index < len(engine_args):
        item = engine_args[index]
        matched = _flag_and_value(item, index, engine_args)
        if matched is None:
            remaining.append(item)
            index += 1
            continue
        flag, raw, next_index = matched
        canonical = PARALLELISM_CANONICAL.get(flag, flag)
        if canonical in seen:
            fail(f"profile repeats structured engine argument {canonical}")
        seen.add(canonical)
        if canonical in values:
            try:
                parsed = int(raw)
            except ValueError:
                fail(f"profile {item} must be an integer")
            if parsed < 1:
                fail(f"profile {item} must be positive")
            values[canonical] = parsed
        elif canonical == "--gpu-memory-utilization":
            fail("profile engine_args duplicate GPU_MEM_UTIL")
        index = next_index
    return (
        values["--tensor-parallel-size"],
        values["--pipeline-parallel-size"],
        remaining,
    )


def _forbidden_flag(token: str) -> str | None:
    for flag in FORBIDDEN_ENGINE_FLAGS:
        if token == flag or token.startswith(flag + "="):
            return flag
    return None


def _load_receipt_identity(
    receipt_path: str | pathlib.Path | None,
) -> tuple[str | None, str | None, list[dict[str, Any]] | None, list[dict[str, str]]]:
    blocking: list[dict[str, str]] = []
    if receipt_path is None:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="snapshot_manifest",
                source="receipt",
                reason=(
                    "download receipt is required; refusing to synthesize a "
                    "snapshot manifest"
                ),
            )
        )
        return None, None, None, blocking
    path = pathlib.Path(receipt_path)
    if not path.is_file() or path.is_symlink():
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="snapshot_manifest",
                source="receipt",
                reason=(
                    "download receipt is required; refusing to synthesize a "
                    "snapshot manifest"
                ),
            )
        )
        return None, None, None, blocking
    try:
        document = load_json(path)
    except ReleaseSpecGenerateError as exc:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="snapshot_manifest",
                source="receipt",
                reason=str(exc),
            )
        )
        return None, None, None, blocking
    if not isinstance(document, dict):
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="snapshot_manifest",
                source="receipt",
                reason="download receipt must be a JSON object",
            )
        )
        return None, None, None, blocking
    observed = document.get("observed_manifest")
    if not isinstance(observed, dict):
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="snapshot_manifest",
                source="receipt",
                reason="receipt observed_manifest is required",
            )
        )
        return None, None, None, blocking
    files = observed.get("files")
    if not isinstance(files, list):
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="snapshot_manifest",
                source="receipt",
                reason="receipt observed_manifest.files is required",
            )
        )
        return None, None, None, blocking
    receipt_model = document.get("model_id")
    if receipt_model is None:
        receipt_model = observed.get("model_id")
    revision = document.get("snapshot_revision")
    if revision is None:
        revision = observed.get("snapshot_revision")
    if not isinstance(receipt_model, str) or not receipt_model:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="model_id",
                source="receipt",
                reason="receipt model_id is required",
            )
        )
        receipt_model = None
    if not isinstance(revision, str) or COMMIT_RE.fullmatch(revision) is None:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="snapshot_revision",
                source="receipt",
                reason="receipt snapshot_revision must be a 40-character lowercase hex commit",
            )
        )
        revision = None
    observed_model = observed.get("model_id")
    if (
        isinstance(observed_model, str)
        and observed_model
        and receipt_model is not None
        and observed_model != receipt_model
    ):
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="model_id",
                source="receipt",
                reason="receipt observed_manifest.model_id differs from receipt model_id",
            )
        )
    return receipt_model, revision, files, blocking


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
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return ``(spec, gap_report)``. Spec is None when generation is blocked."""
    root = pathlib.Path(repo_root) if repo_root is not None else None
    gaps = expected_and_lossy_gaps(profile=profile, repo_root=root)
    blocking: list[dict[str, str]] = []

    receipt_model, revision, files, receipt_blocking = _load_receipt_identity(
        receipt_path
    )
    blocking.extend(receipt_blocking)
    if receipt_model is not None and receipt_model != model_id:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="model_id",
                source="receipt",
                reason="receipt model_id differs from the profile MODEL",
            )
        )

    digest: str | None = None
    try:
        digest = profile_image_digest(image)
    except ReleaseSpecGenerateError as exc:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="image",
                source="conf:IMAGE",
                reason=str(exc),
            )
        )

    if spec_decode and not spec_decode_args:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="engine_args",
                source="conf:SPEC_DECODE_ARGS",
                reason="profile has no SPEC_DECODE_ARGS; refusing --spec-decode",
            )
        )

    remaining: list[str] | None = None
    tensor_parallel = 1
    pipeline_parallel = 1
    try:
        normalized = normalize_engine_args(list(engine_args), path="identity.engine_args")
        tensor_parallel, pipeline_parallel, remaining = strip_profile_parallelism(
            normalized
        )
    except (ReleaseSpecError, ReleaseSpecGenerateError) as exc:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="engine_args",
                source="conf:ENGINE_ARGS",
                reason=str(exc),
            )
        )
        remaining = None

    def _reject_forbidden(tokens: list[str]) -> str | None:
        for token in tokens:
            flag = _forbidden_flag(token)
            if flag is not None:
                return flag
        return None

    if remaining is not None:
        forbidden = _reject_forbidden(remaining)
        if forbidden is not None:
            blocking.append(
                _gap(
                    class_name="blocking",
                    section="identity",
                    field="engine_args",
                    source="conf:ENGINE_ARGS",
                    reason=(
                        f"profile ENGINE_ARGS must not include {forbidden} "
                        "(geometry or deployment overlay owns this flag)"
                    ),
                )
            )
            remaining = None
        elif any(
            token == "--gpu-memory-utilization"
            or token.startswith("--gpu-memory-utilization=")
            for token in remaining
        ):
            blocking.append(
                _gap(
                    class_name="blocking",
                    section="identity",
                    field="engine_args",
                    source="conf:ENGINE_ARGS",
                    reason="profile engine_args duplicate GPU_MEM_UTIL",
                )
            )
            remaining = None

    if not isinstance(gpu_mem_util, str) or not gpu_mem_util:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="engine_args",
                source="conf:GPU_MEM_UTIL",
                reason="GPU_MEM_UTIL must be a non-empty string",
            )
        )
        remaining = None

    if remaining is not None:
        remaining = [
            *remaining,
            "--gpu-memory-utilization",
            gpu_mem_util,
        ]
        if spec_decode and spec_decode_args:
            remaining.extend(list(spec_decode_args))
        try:
            remaining = normalize_engine_args(
                remaining, path="identity.engine_args"
            )
        except ReleaseSpecError as exc:
            blocking.append(
                _gap(
                    class_name="blocking",
                    section="identity",
                    field="engine_args",
                    source="conf:ENGINE_ARGS",
                    reason=str(exc),
                )
            )
            remaining = None

    if remaining is not None:
        forbidden = _reject_forbidden(remaining)
        if forbidden is not None:
            blocking.append(
                _gap(
                    class_name="blocking",
                    section="identity",
                    field="engine_args",
                    source="conf:ENGINE_ARGS",
                    reason=(
                        f"profile ENGINE_ARGS must not include {forbidden} "
                        "(geometry or deployment overlay owns this flag)"
                    ),
                )
            )
            remaining = None

    if remaining is not None and tensor_parallel * pipeline_parallel != nodes:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="geometry",
                source="conf:NODES",
                reason="tp * pp must equal nodes",
            )
        )

    env_tokens: list[str] | None
    try:
        env_tokens = normalize_container_env(
            list(container_env), path="identity.container_env"
        )
    except ReleaseSpecError as exc:
        blocking.append(
            _gap(
                class_name="blocking",
                section="identity",
                field="container_env",
                source="conf:CONTAINER_ENV",
                reason=str(exc),
            )
        )
        env_tokens = None

    manifest: dict[str, Any] | None = None
    if files is not None and revision is not None and not any(
        item["field"] == "model_id" and item["class"] == "blocking" for item in blocking
    ):
        try:
            manifest = build_snapshot_manifest(
                model_id=model_id,
                snapshot_revision=revision,
                files=files,
            )
        except ReleaseSpecError as exc:
            blocking.append(
                _gap(
                    class_name="blocking",
                    section="identity",
                    field="snapshot_manifest",
                    source="receipt",
                    reason=str(exc),
                )
            )

    gaps.extend(blocking)
    if blocking:
        return None, build_gap_report(
            profile=profile,
            spec_decode=spec_decode,
            spec_id=None,
            gaps=gaps,
        )

    assert remaining is not None
    assert env_tokens is not None
    assert digest is not None
    assert manifest is not None
    assert revision is not None

    identity = {
        "model_id": model_id,
        "snapshot_revision": revision,
        "snapshot_manifest": manifest,
        "engine_args": remaining,
        "container_env": env_tokens,
        "image": {"digest": digest},
        "geometry": {
            "platform_id": platform_id,
            "nodes": nodes,
            "tp": tensor_parallel,
            "pp": pipeline_parallel,
            "fabric": FABRIC_LOCAL if nodes == 1 else FABRIC_ROCE_V2,
        },
    }
    try:
        identity = identity_block(identity)
        spec_id = spec_id_for(identity)
        argv = [
            *identity["engine_args"],
            "--tensor-parallel-size",
            str(identity["geometry"]["tp"]),
            "--pipeline-parallel-size",
            str(identity["geometry"]["pp"]),
        ]
        document = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "spec_id": spec_id,
            "state": "measured",
            "identity": identity,
            "launch_contract": {
                "stack_version": stack_version,
                "argv": argv,
            },
            "measurements": [],
            "baselines": [],
            "evidence": [],
            "review": {},
        }
        spec = verify_spec(document)
    except ReleaseSpecError as exc:
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
    return parser


def _write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty_json_bytes(value))


def cmd_from_profile(args: argparse.Namespace) -> int:
    out_path = pathlib.Path(args.out) if args.out else None
    gap_path = pathlib.Path(args.gap_report) if args.gap_report else None
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
