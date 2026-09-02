#!/usr/bin/env python3
"""Stack-side ADR 0017 consumer: releases index, overlay, launch-contract comparison.

This module owns the ``releases/`` index, the deployment overlay schema, and
the projector from a loaded profile to a comparable launch contract. It
imports ``release_spec`` and does not write ``releases/``, start a server, or
change catalog display. Lab generator code imports the projector from here.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any, Iterable, Sequence

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from release_spec import (  # noqa: E402
    ReleaseSpecError,
    build_snapshot_manifest,
    identity_block,
    load_spec,
    normalize_container_env,
    normalize_engine_args,
    pretty_json_bytes,
    spec_id_for,
)
from release_spec.schema import (  # noqa: E402
    FABRIC_LOCAL,
    FABRIC_ROCE_V2,
    FORBIDDEN_ENGINE_FLAGS,
    SHA256_HEX_RE,
    require_public_string,
)

try:
    from scripts.terminal_format import TerminalWriter
except ModuleNotFoundError:  # pragma: no cover - direct script invocation
    from terminal_format import TerminalWriter  # type: ignore[no-redef]

# Copied from scripts/model_identity.py (do not import it).
IMAGE_PIN_RE = re.compile(r"@sha256:([0-9a-f]{64})$")

RELEASES_DIR = "releases"
OVERLAY_KIND = "pulsar-deployment-overlay"
OVERLAY_SCHEMA_VERSION = 1
OVERLAY_FILENAME = ".pulsar-overlay.json"
OVERLAY_TOP_KEYS = frozenset({"schema_version", "kind", "defaults", "specs"})
OVERLAY_ENTRY_KEYS = frozenset(
    {"port", "served_name", "cache_root", "placement"}
)
OVERLAY_PLACEMENT_KEYS = frozenset({"node_id"})
RECIPE_OVERLAY_KEYS = frozenset(
    {
        "engine_args",
        "image",
        "geometry",
        "container_env",
        "extra_env",
        "vllm_extra_args",
        "gpu_mem_util",
        "tp",
        "pp",
        "nodes",
        "fabric",
        "platform_id",
    }
)
COMPARABLE_FIELDS = ("argv", "container_env", "geometry", "image_digest")
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

USAGE = (
    "usage: python3 scripts/release_consumer.py list|verify|show [spec_id] "
    "[--repo-root R] [--json]"
)


class ReleaseConsumerError(ValueError):
    """A releases index, overlay, or profile projection is invalid."""


def fail(message: str) -> None:
    raise ReleaseConsumerError(message)


def blocking_gap(
    *,
    field: str,
    source: str,
    reason: str,
    section: str = "identity",
) -> dict[str, str]:
    return {
        "class": "blocking",
        "section": section,
        "field": field,
        "source": source,
        "reason": reason,
    }


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            fail(f"JSON object contains duplicate key {key!r}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    fail(f"JSON contains unsupported constant {value}")


def _reject_floats(value: Any, *, path: str) -> None:
    if isinstance(value, float):
        fail(f"{path} must not be a JSON float")
    if isinstance(value, list):
        for index, item in enumerate(value):
            child = f"{path}[{index}]" if path else f"[{index}]"
            _reject_floats(item, path=child)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            _reject_floats(item, path=child)


def load_json(path: str | pathlib.Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(
                handle,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
    except ReleaseConsumerError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")


def profile_image_digest(image: str) -> str:
    """Return ``sha256:<hex>`` from a conf IMAGE pin; fail if unpinned."""
    match = IMAGE_PIN_RE.search(image or "")
    if match is None:
        fail("profile image must be pinned by @sha256 digest")
    return "sha256:" + match.group(1)


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


def _reject_forbidden(tokens: list[str]) -> str | None:
    for token in tokens:
        flag = _forbidden_flag(token)
        if flag is not None:
            return flag
    return None


def build_profile_identity(
    *,
    model_id: str,
    image: str,
    nodes: int,
    gpu_mem_util: str,
    engine_args: list[str],
    container_env: list[str],
    spec_decode_args: list[str],
    spec_decode: bool,
    platform_id: str,
    snapshot_revision: str | None,
    files: list[dict[str, Any]] | None,
    receipt_model_id: str | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Return ``(identity, blocking_gaps)``. Identity is None when blocked."""
    blocking: list[dict[str, str]] = []
    if receipt_model_id is not None and receipt_model_id != model_id:
        blocking.append(
            blocking_gap(
                field="model_id",
                source="receipt",
                reason="receipt model_id differs from the profile MODEL",
            )
        )

    digest: str | None = None
    try:
        digest = profile_image_digest(image)
    except ReleaseConsumerError as exc:
        blocking.append(
            blocking_gap(
                field="image",
                source="conf:IMAGE",
                reason=str(exc),
            )
        )

    if spec_decode and not spec_decode_args:
        blocking.append(
            blocking_gap(
                field="engine_args",
                source="conf:SPEC_DECODE_ARGS",
                reason="profile has no SPEC_DECODE_ARGS; refusing --spec-decode",
            )
        )

    remaining: list[str] | None = None
    tensor_parallel = 1
    pipeline_parallel = 1
    try:
        normalized = normalize_engine_args(
            list(engine_args), path="identity.engine_args"
        )
        tensor_parallel, pipeline_parallel, remaining = strip_profile_parallelism(
            normalized
        )
    except (ReleaseSpecError, ReleaseConsumerError) as exc:
        blocking.append(
            blocking_gap(
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
                blocking_gap(
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
                blocking_gap(
                    field="engine_args",
                    source="conf:ENGINE_ARGS",
                    reason="profile engine_args duplicate GPU_MEM_UTIL",
                )
            )
            remaining = None

    if not isinstance(gpu_mem_util, str) or not gpu_mem_util:
        blocking.append(
            blocking_gap(
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
                blocking_gap(
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
                blocking_gap(
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
            blocking_gap(
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
            blocking_gap(
                field="container_env",
                source="conf:CONTAINER_ENV",
                reason=str(exc),
            )
        )
        env_tokens = None

    manifest: dict[str, Any] | None = None
    if (
        files is not None
        and snapshot_revision is not None
        and not any(
            item["field"] == "model_id" and item["class"] == "blocking"
            for item in blocking
        )
    ):
        try:
            manifest = build_snapshot_manifest(
                model_id=model_id,
                snapshot_revision=snapshot_revision,
                files=files,
            )
        except ReleaseSpecError as exc:
            blocking.append(
                blocking_gap(
                    field="snapshot_manifest",
                    source="receipt",
                    reason=str(exc),
                )
            )

    if blocking:
        return None, blocking
    if (
        remaining is None
        or env_tokens is None
        or digest is None
        or manifest is None
        or snapshot_revision is None
    ):
        return None, blocking
    identity = {
        "model_id": model_id,
        "snapshot_revision": snapshot_revision,
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
    except ReleaseSpecError as exc:
        return None, [
            blocking_gap(
                field="identity",
                source="generator",
                reason=str(exc),
            )
        ]
    return identity, []


def profile_identity(
    *,
    model_id: str,
    image: str,
    nodes: int,
    gpu_mem_util: str,
    engine_args: list[str],
    container_env: list[str],
    spec_decode_args: list[str],
    spec_decode: bool,
    platform_id: str,
    snapshot_revision: str | None,
    files: list[dict[str, Any]] | None,
    receipt_model_id: str | None = None,
) -> dict[str, Any]:
    """Return the canonical identity or raise ``ReleaseConsumerError``."""
    identity, gaps = build_profile_identity(
        model_id=model_id,
        image=image,
        nodes=nodes,
        gpu_mem_util=gpu_mem_util,
        engine_args=engine_args,
        container_env=container_env,
        spec_decode_args=spec_decode_args,
        spec_decode=spec_decode,
        platform_id=platform_id,
        snapshot_revision=snapshot_revision,
        files=files,
        receipt_model_id=receipt_model_id,
    )
    if identity is None:
        reason = next(
            (item["reason"] for item in gaps if item["class"] == "blocking"),
            "profile identity failed",
        )
        fail(reason)
    return identity


def argv_from_identity(
    identity: dict[str, Any],
    *,
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Recipe argv: engine_args plus tp/pp tokens, then live extra args."""
    return [
        *list(identity["engine_args"]),
        "--tensor-parallel-size",
        str(identity["geometry"]["tp"]),
        "--pipeline-parallel-size",
        str(identity["geometry"]["pp"]),
        *list(extra_args),
    ]


def comparable_contract_from_identity(
    identity: dict[str, Any],
    *,
    extra_args: Sequence[str] = (),
    extra_env: Sequence[str] = (),
) -> dict[str, Any]:
    """Comparable launch contract for ADR 0017 decision 5 (plus container env)."""
    return {
        "argv": argv_from_identity(identity, extra_args=extra_args),
        "container_env": [*list(identity["container_env"]), *list(extra_env)],
        "image_digest": identity["image"]["digest"],
        "geometry": dict(identity["geometry"]),
    }


def comparable_contract_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    identity = spec["identity"]
    return {
        "argv": list(spec["launch_contract"]["argv"]),
        "container_env": list(identity["container_env"]),
        "image_digest": identity["image"]["digest"],
        "geometry": dict(identity["geometry"]),
    }


def compare_contracts(
    computed: dict[str, Any],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Return ``{"result": "equal"|"differs", "fields": [...]}``."""
    fields: list[str] = []
    for name in COMPARABLE_FIELDS:
        if computed.get(name) != expected.get(name):
            fields.append(name)
    if fields:
        return {"result": "differs", "fields": fields}
    return {"result": "equal", "fields": []}


def profile_identities(
    *,
    model_id: str,
    image: str,
    nodes: int,
    gpu_mem_util: str,
    engine_args: list[str],
    container_env: list[str],
    spec_decode_args: list[str],
    platform_id: str,
    snapshot_revision: str | None,
    files: list[dict[str, Any]] | None,
    receipt_model_id: str | None = None,
    recommended_spec: bool = False,
) -> list[dict[str, Any]]:
    """Return one entry per spec-decode variant for a profile.

    ``default`` is true for the auto-policy variant: MTP when
    ``recommended_spec`` is true, otherwise the non-MTP identity.
    """
    variants = [False]
    if spec_decode_args:
        variants.append(True)
    rows: list[dict[str, Any]] = []
    for spec_decode in variants:
        identity = profile_identity(
            model_id=model_id,
            image=image,
            nodes=nodes,
            gpu_mem_util=gpu_mem_util,
            engine_args=engine_args,
            container_env=container_env,
            spec_decode_args=spec_decode_args,
            spec_decode=spec_decode,
            platform_id=platform_id,
            snapshot_revision=snapshot_revision,
            files=files,
            receipt_model_id=receipt_model_id,
        )
        default = spec_decode if recommended_spec else not spec_decode
        rows.append(
            {
                "spec_decode": spec_decode,
                "spec_id": spec_id_for(identity),
                "contract": comparable_contract_from_identity(identity),
                "default": default,
            }
        )
    return rows


def _releases_root(repo_root: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(repo_root) / RELEASES_DIR


def _require_spec_id(spec_id: str) -> str:
    if not isinstance(spec_id, str) or SHA256_HEX_RE.fullmatch(spec_id) is None:
        fail("spec_id must be a 64-character lowercase hex digest")
    return spec_id


def _load_released_file(path: pathlib.Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        fail(f"{path}: release file must be a regular file")
    try:
        spec = load_spec(path)
    except ReleaseSpecError as exc:
        fail(f"{path}: {exc}")
    if spec["state"] != "released":
        fail(f"{path}: state must be 'released', not {spec['state']!r}")
    stem = path.stem
    if stem != spec["spec_id"]:
        fail(
            f"{path}: filename stem {stem!r} must equal spec_id "
            f"{spec['spec_id']!r}"
        )
    return spec


def load_release(repo_root: str | pathlib.Path, spec_id: str) -> dict[str, Any]:
    """Load one released spec; fail without fallback on any mismatch."""
    digest = _require_spec_id(spec_id)
    path = _releases_root(repo_root) / f"{digest}.json"
    if not path.exists():
        fail(f"{path}: released spec is missing")
    return _load_released_file(path)


def _review_fields(spec: dict[str, Any]) -> tuple[str | None, str | None]:
    review = spec.get("review") or {}
    status = review.get("status")
    reviewed_at = review.get("reviewed_at")
    return (
        str(status) if status else None,
        str(reviewed_at) if reviewed_at else None,
    )


def list_releases(repo_root: str | pathlib.Path) -> list[dict[str, Any]]:
    """Return sorted released-spec rows. A bad file fails the listing."""
    root = _releases_root(repo_root)
    if not root.exists():
        fail(f"{root}: releases directory is missing")
    if not root.is_dir() or root.is_symlink():
        fail(f"{root}: releases must be a directory")
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        spec = _load_released_file(path)
        status, reviewed_at = _review_fields(spec)
        rows.append(
            {
                "spec_id": spec["spec_id"],
                "model_id": spec["identity"]["model_id"],
                "state": spec["state"],
                "review_status": status,
                "reviewed_at": reviewed_at,
                "path": f"{RELEASES_DIR}/{path.name}",
            }
        )
    rows.sort(key=lambda item: item["spec_id"])
    return rows


def _reject_recipe_keys(keys: Iterable[str], *, path: str) -> None:
    for key in keys:
        if key in RECIPE_OVERLAY_KEYS:
            fail(f"{path}: overlay must not name recipe field {key}")


def _require_overlay_object(
    value: Any,
    allowed: frozenset[str],
    *,
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{path} must be an object")
    _reject_recipe_keys(value, path=path)
    extra = sorted(set(value) - set(allowed))
    missing = sorted(set(allowed) - set(value))
    if extra:
        fail(f"{path}: unknown key {extra[0]}")
    if missing:
        fail(f"{path} fields differ (missing={missing}, extra={extra})")
    return value


def _optional_public_string(value: Any, *, path: str) -> str | None:
    if value is None:
        return None
    try:
        return require_public_string(value, path=path)
    except ReleaseSpecError as exc:
        fail(str(exc))


def _optional_site_string(value: Any, *, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        fail(f"{path} must be a non-empty string")
    return value


def _overlay_port(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        fail(f"{path} must be an integer")
    if value < 1 or value > 65535:
        fail(f"{path} must be a TCP port")
    return value


def _overlay_placement(value: Any, *, path: str) -> dict[str, str] | None:
    if value is None:
        return None
    obj = _require_overlay_object(value, OVERLAY_PLACEMENT_KEYS, path=path)
    node_id = _optional_site_string(obj.get("node_id"), path=f"{path}.node_id")
    if node_id is None:
        fail(f"{path}.node_id must be a non-empty string")
    return {"node_id": node_id}


def _overlay_entry(value: Any, *, path: str) -> dict[str, Any]:
    obj = _require_overlay_object(value, OVERLAY_ENTRY_KEYS, path=path)
    return {
        "port": _overlay_port(obj.get("port"), path=f"{path}.port"),
        "served_name": _optional_public_string(
            obj.get("served_name"), path=f"{path}.served_name"
        ),
        "cache_root": _optional_site_string(
            obj.get("cache_root"), path=f"{path}.cache_root"
        ),
        "placement": _overlay_placement(
            obj.get("placement"), path=f"{path}.placement"
        ),
    }


def load_overlay(path: str | pathlib.Path) -> dict[str, Any]:
    """Load a closed deployment overlay. Fail without fallback on any extra key."""
    overlay_path = pathlib.Path(path)
    if overlay_path.is_symlink() or not overlay_path.is_file():
        fail(f"{overlay_path}: overlay must be a regular file")
    document = load_json(overlay_path)
    _reject_floats(document, path="overlay")
    obj = _require_overlay_object(document, OVERLAY_TOP_KEYS, path="overlay")
    schema_version = obj.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != OVERLAY_SCHEMA_VERSION:
        fail("overlay.schema_version must be 1")
    if obj.get("kind") != OVERLAY_KIND:
        fail(f"overlay.kind must be {OVERLAY_KIND!r}")
    defaults = _overlay_entry(obj.get("defaults"), path="overlay.defaults")
    specs_raw = obj.get("specs")
    if not isinstance(specs_raw, dict):
        fail("overlay.specs must be an object")
    _reject_recipe_keys(specs_raw, path="overlay.specs")
    specs: dict[str, dict[str, Any]] = {}
    for key, item in specs_raw.items():
        if not isinstance(key, str) or SHA256_HEX_RE.fullmatch(key) is None:
            fail(
                "overlay.specs keys must be 64-character lowercase hex spec ids"
            )
        specs[key] = _overlay_entry(item, path=f"overlay.specs.{key}")
    return {
        "schema_version": OVERLAY_SCHEMA_VERSION,
        "kind": OVERLAY_KIND,
        "defaults": defaults,
        "specs": specs,
    }


def overlay_for_spec(
    overlay: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Merge defaults and per-spec overlay. ``served_name`` null → model_id."""
    defaults = overlay["defaults"]
    spec_id = spec["spec_id"]
    entry = dict(defaults)
    if spec_id in overlay.get("specs", {}):
        entry.update(overlay["specs"][spec_id])
    served = entry.get("served_name")
    if served is None:
        served = spec["identity"]["model_id"]
    return {
        "served_name": served,
        "port": entry["port"],
        "cache_root": entry.get("cache_root"),
        "placement": entry.get("placement"),
    }


def _review_text(status: str | None, reviewed_at: str | None) -> str:
    """Human review line: status plus its date, or a plain dash."""
    if not status:
        return "-"
    if reviewed_at:
        return f"{status} since {reviewed_at}"
    return status


def cmd_list(repo_root: pathlib.Path, *, as_json: bool) -> int:
    rows = list_releases(repo_root)
    if as_json:
        sys.stdout.buffer.write(pretty_json_bytes({"releases": rows}))
        return 0
    term = TerminalWriter()
    for index, row in enumerate(rows):
        if index:
            term.blank()
        term.emit(row["spec_id"])
        term.field("model", row["model_id"], indent=2)
        term.field("review", _review_text(row["review_status"], row["reviewed_at"]), indent=2)
    return 0


def cmd_verify(repo_root: pathlib.Path, spec_id: str, *, as_json: bool) -> int:
    spec = load_release(repo_root, spec_id)
    status, reviewed_at = _review_fields(spec)
    if as_json:
        sys.stdout.buffer.write(
            pretty_json_bytes(
                {
                    "spec_id": spec["spec_id"],
                    "state": spec["state"],
                    "review": status,
                    "reviewed_at": reviewed_at,
                }
            )
        )
        return 0
    term = TerminalWriter()
    term.field("spec_id", spec["spec_id"])
    term.field("state", spec["state"])
    term.field("review", _review_text(status, reviewed_at))
    return 0


def cmd_show(repo_root: pathlib.Path, spec_id: str, *, as_json: bool) -> int:
    spec = load_release(repo_root, spec_id)
    sys.stdout.buffer.write(pretty_json_bytes(spec))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read released ADR 0017 specs under releases/",
        usage=USAGE,
    )
    parser.add_argument("command", choices=("list", "verify", "show"))
    parser.add_argument("spec_id", nargs="?")
    parser.add_argument(
        "--repo-root",
        default=str(_REPO_ROOT),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 2
    repo_root = pathlib.Path(args.repo_root)
    try:
        if args.command == "list":
            if args.spec_id:
                fail("list does not take a spec_id")
            return cmd_list(repo_root, as_json=args.json)
        if not args.spec_id:
            fail(f"{args.command} requires a spec_id")
        if args.command == "verify":
            return cmd_verify(repo_root, args.spec_id, as_json=args.json)
        return cmd_show(repo_root, args.spec_id, as_json=args.json)
    except (ReleaseConsumerError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
