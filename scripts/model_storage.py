#!/usr/bin/env python3
"""Read-only interactive projection for Pulsar model-library health schema 1."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any

try:
    from scripts.terminal_format import TerminalWriter, terminal_width
except ModuleNotFoundError:
    from terminal_format import TerminalWriter, terminal_width


REPORT_KIND = "pulsar-model-library-health"
REPORT_SCHEMA = 1
REPORT_STATES = {"healthy", "attention", "not-configured", "unavailable"}


class ModelStorageContractError(ValueError):
    """Raised when the health service does not satisfy its stable contract."""


def _is_string_or_none(value: object) -> bool:
    return value is None or isinstance(value, str)


def validate_report(report: object) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ModelStorageContractError("report must be an object")
    if report.get("schema_version") != REPORT_SCHEMA:
        raise ModelStorageContractError("unsupported schema_version")
    if report.get("kind") != REPORT_KIND:
        raise ModelStorageContractError("unexpected report kind")
    if report.get("state") not in REPORT_STATES:
        raise ModelStorageContractError("unsupported report state")

    catalog = report.get("catalog")
    if not isinstance(catalog, dict):
        raise ModelStorageContractError("catalog must be an object")
    if not isinstance(catalog.get("status"), str):
        raise ModelStorageContractError("catalog status is missing")
    compatible = catalog.get("topology_compatible")
    if compatible is not None and not isinstance(compatible, bool):
        raise ModelStorageContractError("catalog topology_compatible is invalid")
    if not _is_string_or_none(catalog.get("refreshed_at")):
        raise ModelStorageContractError("catalog refreshed_at is invalid")

    for field in ("models", "hot_instances", "issues"):
        if not isinstance(report.get(field), list):
            raise ModelStorageContractError(f"{field} must be an array")

    for index, model in enumerate(report["models"]):
        if not isinstance(model, dict):
            raise ModelStorageContractError(f"models[{index}] must be an object")
        if not isinstance(model.get("model_id"), str) or not model["model_id"]:
            raise ModelStorageContractError(f"models[{index}] model_id is invalid")
        if not _is_string_or_none(model.get("revision")):
            raise ModelStorageContractError(f"models[{index}] revision is invalid")
        profiles = model.get("profiles") or []
        if not isinstance(profiles, list) or not all(
            isinstance(item, str) and item for item in profiles
        ):
            raise ModelStorageContractError(f"models[{index}] profiles are invalid")
        homes = model.get("home_ranks") or []
        if not isinstance(homes, list) or not all(
            isinstance(rank, int) and not isinstance(rank, bool) and rank >= 0
            for rank in homes
        ):
            raise ModelStorageContractError(f"models[{index}] home_ranks are invalid")
        for field in ("expected_manifest", "validation", "duplicate_home"):
            if not _is_string_or_none(model.get(field)):
                raise ModelStorageContractError(
                    f"models[{index}] {field} is invalid"
                )
        primary = model.get("primary") or {}
        if not isinstance(primary, dict):
            raise ModelStorageContractError(f"models[{index}] primary is invalid")
        for field in ("mode", "status"):
            if not _is_string_or_none(primary.get(field)):
                raise ModelStorageContractError(
                    f"models[{index}] primary {field} is invalid"
                )
        primary_rank = primary.get("rank")
        if primary_rank is not None and (
            not isinstance(primary_rank, int)
            or isinstance(primary_rank, bool)
            or primary_rank < 0
        ):
            raise ModelStorageContractError(
                f"models[{index}] primary rank is invalid"
            )

    for index, instance in enumerate(report["hot_instances"]):
        if not isinstance(instance, dict):
            raise ModelStorageContractError(
                f"hot_instances[{index}] must be an object"
            )
        rank = instance.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 0:
            raise ModelStorageContractError(
                f"hot_instances[{index}] rank is invalid"
            )
        for field in (
            "profile", "model_id", "revision", "runtime_source", "retention",
            "identity_status", "witness_status",
        ):
            if not _is_string_or_none(instance.get(field)):
                raise ModelStorageContractError(
                    f"hot_instances[{index}] {field} is invalid"
                )
        if not isinstance(instance.get("active_reference"), bool):
            raise ModelStorageContractError(
                f"hot_instances[{index}] active_reference is invalid"
            )

    for index, issue in enumerate(report["issues"]):
        if not isinstance(issue, dict):
            raise ModelStorageContractError(f"issues[{index}] must be an object")
        if not isinstance(issue.get("code"), str) or not issue["code"]:
            raise ModelStorageContractError(f"issues[{index}] code is invalid")
        if not isinstance(issue.get("detail"), str) or not issue["detail"]:
            raise ModelStorageContractError(f"issues[{index}] detail is invalid")
        remediation = issue.get("remediation")
        if remediation is not None:
            if not isinstance(remediation, dict) or not isinstance(
                remediation.get("command"), str
            ):
                raise ModelStorageContractError(
                    f"issues[{index}] remediation is invalid"
                )

    return report


def load_report(path: str | pathlib.Path) -> dict[str, Any]:
    try:
        with pathlib.Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelStorageContractError(f"cannot read health report: {exc}") from exc
    return validate_report(value)


def _safe_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _node_label(rank: int) -> str:
    return f"node {rank + 1} (rank {rank})"


def _validation_label(value: object) -> str:
    labels = {
        "expected-unverified": "reviewed identity expected",
        "legacy-unsealed": "legacy identity",
        "unvalidated": "unvalidated",
        "missing": "not present",
        "match": "identity match",
    }
    text = _safe_text(value) or "unknown"
    return labels.get(text, text.replace("-", " "))


def _runtime_label(value: object) -> str:
    labels = {
        "durable-home": "durable home",
        "sealed-hot": "sealed hot",
        "live-mount": "live mount",
    }
    text = _safe_text(value) or "unknown"
    return labels.get(text, text.replace("-", " "))


def _status_label(value: object) -> str:
    return (_safe_text(value) or "unknown").replace("-", " ")


def sorted_models(report: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        report.get("models") or [],
        key=lambda model: (
            (model.get("profiles") or [model.get("model_id") or ""])[0],
            model.get("model_id") or "",
            model.get("revision") or "",
        ),
    )


def model_instances(
    report: dict[str, Any], model: dict[str, Any]
) -> list[dict[str, Any]]:
    return sorted(
        [
            instance
            for instance in report.get("hot_instances") or []
            if instance.get("model_id") == model.get("model_id")
            and instance.get("revision") == model.get("revision")
        ],
        key=lambda instance: int(instance["rank"]),
    )


def _catalog_age(value: object, now: dt.datetime | None = None) -> str:
    text = _safe_text(value)
    if not text:
        return "unknown"
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        current = now or dt.datetime.now(dt.timezone.utc)
        seconds = max(0, int((current - parsed).total_seconds()))
    except ValueError:
        return text
    if seconds < 60:
        age = "just now"
    elif seconds < 3600:
        age = f"{seconds // 60}m ago"
    elif seconds < 86400:
        age = f"{seconds // 3600}h ago"
    else:
        age = f"{seconds // 86400}d ago"
    return f"{text} · {age}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def model_choice_labels(report: dict[str, Any], width: int | None = None) -> list[str]:
    limit = max(24, (width or terminal_width()) - 5)
    placement_current = report["catalog"].get("topology_compatible") is True
    labels: list[str] = []
    for model in sorted_models(report):
        profiles = model.get("profiles") or []
        name = profiles[0] if profiles else model["model_id"]
        if len(profiles) > 1:
            name = f"{name} +{len(profiles) - 1}"
        revision = _safe_text(model.get("revision"))
        revision_text = revision[:12] if revision else "revision unknown"
        homes = model.get("home_ranks") or []
        if not placement_current:
            home_text = "placement stale"
        elif len(homes) == 1:
            home_text = f"home n{homes[0] + 1}"
        elif homes:
            home_text = f"{len(homes)} homes"
        else:
            home_text = "no home"
        prefix = (
            f"{_safe_text(name)} · {revision_text} · "
            f"{_validation_label(model.get('validation'))}"
        )
        suffix = f" · {home_text}"
        labels.append(f"{_truncate(prefix, limit - len(suffix))}{suffix}")
    return labels


def render_summary(report: dict[str, Any], width: int | None = None) -> None:
    term = TerminalWriter(width=width)
    state = str(report["state"])
    catalog = report["catalog"]
    term.emit("MODELS & STORAGE")
    term.field("serving", "replicated local model copies · guided default")
    term.field("catalog", f"{state.replace('-', ' ')} · experimental read-only view")
    term.field("cached", _status_label(catalog.get("status")))
    if catalog.get("status") == "cached":
        term.field("refreshed", _catalog_age(catalog.get("refreshed_at")))
        compatible = catalog.get("topology_compatible")
        term.field(
            "topology",
            "current" if compatible is True else "stale or unavailable",
        )
    term.field("models", len(report.get("models") or []))
    term.field("views", len(report.get("hot_instances") or []))
    term.field("findings", len(report.get("issues") or []))
    term.blank()
    term.emit(
        "Browsing does not refresh the catalog, move model files, or start a model."
    )
    if state == "not-configured":
        term.emit(
            "No cached distributed catalog is configured. Replicated serving remains available."
        )
        term.emit("To create one later: scripts/model-library.sh catalog refresh")
    elif state == "unavailable":
        term.emit(
            "Catalog state cannot be confirmed. Keep using the replicated path and review the findings before library maintenance."
        )


def render_detail(
    report: dict[str, Any], index: int, width: int | None = None
) -> None:
    models = sorted_models(report)
    if index < 0 or index >= len(models):
        raise ModelStorageContractError("model selection is out of range")
    model = models[index]
    instances = model_instances(report, model)
    term = TerminalWriter(width=width)
    term.emit("MODEL STORAGE DETAIL")
    term.field("model", model["model_id"])
    term.field("profiles", ", ".join(model.get("profiles") or []) or "none")
    term.field("revision", model.get("revision") or "unknown")
    term.field(
        "manifest",
        model.get("expected_manifest") or "no reviewed expected manifest",
    )
    term.field("identity", _validation_label(model.get("validation")))

    homes = model.get("home_ranks") or []
    primary = model.get("primary") or {}
    placement_current = report["catalog"].get("topology_compatible") is True
    if placement_current:
        term.field(
            "home",
            ", ".join(_node_label(int(rank)) for rank in homes)
            if homes
            else "no complete durable home",
        )
        primary_rank = primary.get("rank")
        primary_text = (
            f"{_node_label(int(primary_rank))} · "
            f"{_status_label(primary.get('status'))}"
            if isinstance(primary_rank, int) and not isinstance(primary_rank, bool)
            else (
                f"{_status_label(primary.get('mode'))} · "
                f"{_status_label(primary.get('status'))}"
            )
        )
        duplicate_text = _status_label(model.get("duplicate_home"))
    else:
        term.field("home", "unavailable · cached topology is stale")
        primary_text = "unavailable · refresh catalog"
        duplicate_text = (
            f"cached {_status_label(model.get('duplicate_home'))} · topology stale"
        )
    term.field("primary", primary_text)
    term.field("duplicates", duplicate_text, label_width=11)

    term.blank()
    term.emit("Runtime views")
    if not instances:
        term.emit(
            "No prepared library-hot runtime views are recorded.",
            initial_indent="  ",
            subsequent_indent="  ",
        )
    for instance in instances:
        parts = [
            _node_label(int(instance["rank"])),
            _runtime_label(instance.get("runtime_source")),
            _status_label(instance.get("retention")),
            f"identity {_status_label(instance.get('identity_status'))}",
            f"witness {_status_label(instance.get('witness_status'))}",
        ]
        if instance.get("active_reference"):
            parts.append("in use")
        term.emit(
            " · ".join(parts),
            initial_indent="  ",
            subsequent_indent="    ",
        )

    term.blank()
    if not placement_current:
        term.emit(
            "Dependency: durable-home placement cannot be confirmed until the cached catalog is refreshed."
        )
    elif homes:
        term.emit(
            "Dependency: the durable home remains authoritative. Prepared non-home copies and pins do not provide durable-home-loss resilience."
        )
    else:
        term.emit(
            "Dependency: no complete durable home is currently available for this exact revision."
        )
    term.emit(
        "Claim boundary: catalog identity and runtime views do not establish model qualification or storage-path promotion."
    )


def render_findings(report: dict[str, Any], width: int | None = None) -> None:
    term = TerminalWriter(width=width)
    issues = report.get("issues") or []
    term.emit("CATALOG FINDINGS")
    if not issues:
        term.emit("No model-library findings.")
        return
    for issue in issues:
        rank = issue.get("rank")
        suffix = f" · {_node_label(int(rank))}" if isinstance(rank, int) else ""
        term.emit(
            f"{_status_label(issue.get('code'))}{suffix}",
            initial_indent="  ",
            subsequent_indent="    ",
        )
        term.emit(
            issue.get("detail") or "attention required",
            initial_indent="    ",
            subsequent_indent="    ",
        )
        command = (issue.get("remediation") or {}).get("command")
        if command:
            term.emit(
                f"Next: {command}",
                initial_indent="    ",
                subsequent_indent="      ",
            )


def render_about(width: int | None = None) -> None:
    term = TerminalWriter(width=width)
    term.emit("HOW MODEL STORAGE WORKS")
    term.field("default", "replicated local model copies on every serving node")
    term.field("catalog", "one durable home per exact revision")
    term.field("prepared", "sealed hot copies only on non-home serving nodes")
    term.field("home node", "uses its durable model through a validated local view")
    term.field("pin", "retains non-home hot copies but still requires the durable home")
    term.blank()
    term.emit(
        "The distributed catalog and library-hot path remain experimental. Browsing them does not change serving policy or qualify a model."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-file", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("summary")
    sub.add_parser("choices")
    detail = sub.add_parser("detail")
    detail.add_argument("--index", type=int, required=True)
    sub.add_parser("findings")
    sub.add_parser("about")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = load_report(args.report_file)
        if args.command == "validate":
            return 0
        if args.command == "summary":
            render_summary(report)
        elif args.command == "choices":
            for label in model_choice_labels(report):
                print(label)
        elif args.command == "detail":
            render_detail(report, args.index)
        elif args.command == "findings":
            render_findings(report)
        elif args.command == "about":
            render_about()
        else:
            raise ModelStorageContractError("unsupported renderer command")
    except ModelStorageContractError as exc:
        print(f"model-storage: invalid health report: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
