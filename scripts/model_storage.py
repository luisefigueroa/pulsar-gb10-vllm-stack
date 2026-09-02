#!/usr/bin/env python3
"""Interactive projection for Pulsar model-library health schema 1."""

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


def validate_profiles(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("models"), list):
        raise ModelStorageContractError("serving profiles must contain a models array")
    for index, profile in enumerate(value["models"]):
        if not isinstance(profile, dict):
            raise ModelStorageContractError(
                f"serving profiles[{index}] must be an object"
            )
        for field in ("id", "status", "source", "purpose"):
            if not isinstance(profile.get(field), str) or not profile[field]:
                raise ModelStorageContractError(
                    f"serving profiles[{index}] {field} is invalid"
                )
        nodes = profile.get("nodes")
        if (
            not isinstance(nodes, int)
            or isinstance(nodes, bool)
            or nodes < 1
        ):
            raise ModelStorageContractError(
                f"serving profiles[{index}] nodes is invalid"
            )
        if not isinstance(profile.get("reviewed_identity"), bool):
            raise ModelStorageContractError(
                f"serving profiles[{index}] reviewed_identity is invalid"
            )
        for field in (
            "reviewed_model_id",
            "reviewed_revision",
            "reviewed_manifest",
        ):
            identity_value = profile.get(field)
            if identity_value is not None and (
                not isinstance(identity_value, str) or not identity_value
            ):
                raise ModelStorageContractError(
                    f"serving profiles[{index}] {field} is invalid"
                )
            if profile["reviewed_identity"] and not identity_value:
                raise ModelStorageContractError(
                    f"serving profiles[{index}] {field} is required"
                )
        weights = profile.get("weights_gib")
        if weights is not None and (
            isinstance(weights, bool)
            or not isinstance(weights, (int, float))
            or weights <= 0
        ):
            raise ModelStorageContractError(
                f"serving profiles[{index}] weights_gib is invalid"
            )
    return value


def load_profiles(path: str | pathlib.Path | None) -> dict[str, Any]:
    if not path:
        return {"models": []}
    try:
        with pathlib.Path(path).open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelStorageContractError(
            f"cannot read serving profiles: {exc}"
        ) from exc
    return validate_profiles(value)


def _safe_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _node_label(rank: int) -> str:
    return f"node {rank + 1} (rank {rank})"


def _validation_label(value: object) -> str:
    labels = {
        "expected-unverified": "lab identity expected (retired)",
        "receipt-occupancy": "receipt and occupancy identity",
        "unbound-complete": "complete files without receipt and occupancy",
        "unvalidated": "identity not checked",
        "missing": "not present",
        "match": "historical identity match",
    }
    text = _safe_text(value) or "unknown"
    return labels.get(text, text.replace("-", " "))


def _runtime_label(value: object) -> str:
    labels = {
        "durable-home": "durable home",
        "working-copy": "working copy on other node",
        "live-mount": "live NFS mount (retired)",
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


def multi_rank_home_geometry_blocker(
    profile: str, home_rank: int, target_ranks: list[int]
) -> str:
    serving = ", ".join(str(rank) for rank in target_ranks)
    return (
        f"durable home rank {home_rank} is outside {profile} serving ranks "
        f"({serving}); relocate the home before preparation"
    )


def multi_rank_home_geometry_relocation_options(
    profile: str, target_ranks: list[int]
) -> list[str]:
    return [
        (
            "scripts/model-library.sh home relocate "
            f"{profile} --node {rank} --yes"
        )
        for rank in target_ranks
    ]


def multi_rank_home_geometry_remediation(profile: str) -> list[str]:
    return [
        "scripts/model-library.sh catalog refresh",
        "scripts/model-library.sh prepare "
        f"{profile} --backend copy --transport ssh-roce --copy-streams 8 --yes",
    ]


def preparation_check(
    report: dict[str, Any],
    profiles: dict[str, Any],
    model_index: int,
    target_ranks_by_profile: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    models = sorted_models(report)
    if model_index < 0 or model_index >= len(models):
        raise ModelStorageContractError("model selection is out of range")
    model = models[model_index]
    blockers: list[str] = []
    if report.get("state") == "unavailable":
        blockers.append("catalog or rank observation is unavailable")
    if report["catalog"].get("topology_compatible") is not True:
        blockers.append("cached topology is stale; refresh the catalog")
    primary = model.get("primary") or {}
    primary_current = primary.get("status") == "match" and isinstance(
        primary.get("rank"), int
    )
    placement_current = report["catalog"].get("topology_compatible") is True
    if not primary_current:
        blockers.append("no current primary durable home is available")

    model_profiles = set(model.get("profiles") or [])
    candidates: list[dict[str, Any]] = []
    geometry_blockers: list[str] = []
    geometry_relocation_options: list[str] = []
    geometry_remediation: list[str] = []
    identity_mismatch = False
    matched_serving_profile = False
    for profile in profiles.get("models") or []:
        if profile.get("id") not in model_profiles:
            continue
        if profile.get("purpose") != "serving" or profile.get("source") != "hf":
            continue
        matched_serving_profile = True
        candidate = {
            "profile": profile["id"],
            "nodes": profile["nodes"],
            "weights_gib": profile.get("weights_gib"),
            "already_prepared": False,
        }
        target_ranks = (
            list(target_ranks_by_profile[profile["id"]])
            if target_ranks_by_profile
            and profile["id"] in target_ranks_by_profile
            else (
                [int(primary["rank"])]
                if int(profile["nodes"]) == 1
                and isinstance(primary.get("rank"), int)
                else list(range(int(profile["nodes"])))
            )
        )
        if len(target_ranks) != int(profile["nodes"]):
            blockers.append("selected serving placement does not match profile geometry")
            continue
        if int(profile["nodes"]) == 1 and target_ranks[0] != primary.get("rank"):
            blockers.append(
                "one-node serving must use the durable-home node; "
                "choose that node"
            )
            continue
        if (
            int(profile["nodes"]) > 1
            and placement_current
            and isinstance(primary.get("rank"), int)
            and int(primary["rank"]) not in target_ranks
        ):
            geometry_blockers.append(
                multi_rank_home_geometry_blocker(
                    profile["id"], int(primary["rank"]), target_ranks
                )
            )
            geometry_relocation_options.extend(
                multi_rank_home_geometry_relocation_options(
                    profile["id"], target_ranks
                )
            )
            geometry_remediation.extend(
                multi_rank_home_geometry_remediation(profile["id"])
            )
            continue
        candidate["target_ranks"] = target_ranks
        non_home = [rank for rank in target_ranks if rank != primary.get("rank")]
        candidate["prepare_transport"] = (
            "ssh-roce" if non_home else "ssh-control"
        )
        candidate["transfer"] = (
            "SSH over confirmed RoCE · 8 streams"
            if non_home
            else "none · durable-home local view"
        )
        candidate["copy_streams"] = 8 if non_home else 1
        instances = [
            instance
            for instance in model_instances(report, model)
            if instance.get("profile") == profile["id"]
            and instance.get("metadata_status") == "current"
            and instance.get("identity_status") == "receipt-occupancy"
            and instance.get("witness_status") == "match"
            and instance.get("runtime_source")
            == (
                "durable-home"
                if int(instance["rank"]) == primary.get("rank")
                else "working-copy"
            )
        ]
        candidate["already_prepared"] = {
            int(instance["rank"]) for instance in instances
        } == set(target_ranks)
        candidates.append(candidate)
    if not candidates and not matched_serving_profile:
        if identity_mismatch:
            blockers.append(
                "trusted profile identity differs from the cached model; "
                "refresh the catalog"
            )
        else:
            blockers.append(
                "no serving profile is available for this catalog entry"
            )
    if not candidates and geometry_blockers:
        blockers.extend(geometry_blockers)
    if blockers:
        candidates = []
    return {
        "schema_version": 1,
        "kind": "pulsar-model-preparation-check",
        "state": "available" if candidates else "blocked",
        "model_id": model["model_id"],
        "revision": model.get("revision"),
        "expected_manifest": model.get("expected_manifest"),
        "home_rank": primary.get("rank") if primary_current else None,
        "candidates": sorted(candidates, key=lambda item: item["profile"]),
        "blockers": blockers,
        "relocation_options": (
            geometry_relocation_options
            if geometry_blockers and not candidates
            else []
        ),
        "remediation": geometry_remediation if geometry_blockers and not candidates else [],
    }


def serving_preparation_check(
    report: dict[str, Any],
    profiles: dict[str, Any],
    profile_id: str,
    target_rank: int | None = None,
) -> dict[str, Any]:
    profile = next(
        (
            item
            for item in profiles.get("models") or []
            if item.get("id") == profile_id
        ),
        None,
    )
    if profile is None:
        raise ModelStorageContractError("selected serving profile is unavailable")
    nodes = int(profile["nodes"])
    if nodes == 1:
        if target_rank is None:
            raise ModelStorageContractError(
                "one-node distributed serving requires an exact target rank"
            )
        targets = [target_rank]
    else:
        if target_rank is not None:
            raise ModelStorageContractError(
                "an explicit target rank is valid only for one-node profiles"
            )
        targets = list(range(nodes))
    models = sorted_models(report)
    indexes = [
        index
        for index, model in enumerate(models)
        if profile_id in (model.get("profiles") or [])
    ]
    if len(indexes) != 1:
        return {
            "schema_version": 1,
            "kind": "pulsar-model-serving-preparation-check",
            "state": "blocked",
            "profile": profile_id,
            "target_ranks": targets,
            "blockers": [
                "the cached catalog does not contain one exact entry for this profile"
            ],
            "relocation_options": [],
            "remediation": [],
        }
    model = models[indexes[0]]
    primary = model.get("primary") or {}
    home_rank = primary.get("rank")
    if (
        nodes > 1
        and report["catalog"].get("topology_compatible") is True
        and primary.get("status") == "match"
        and isinstance(home_rank, int)
        and home_rank not in targets
    ):
        return {
            "schema_version": 1,
            "kind": "pulsar-model-serving-preparation-check",
            "state": "blocked",
            "profile": profile_id,
            "model_id": model.get("model_id"),
            "revision": model.get("revision"),
            "expected_manifest": model.get("expected_manifest"),
            "home_rank": home_rank,
            "target_ranks": targets,
            "blockers": [
                multi_rank_home_geometry_blocker(profile_id, home_rank, targets)
            ],
            "relocation_options": multi_rank_home_geometry_relocation_options(
                profile_id, targets
            ),
            "remediation": multi_rank_home_geometry_remediation(profile_id),
        }
    check = preparation_check(
        report,
        {"models": [profile]},
        indexes[0],
        target_ranks_by_profile={profile_id: targets},
    )
    candidate = next(
        (
            item
            for item in check.get("candidates") or []
            if item.get("profile") == profile_id
        ),
        None,
    )
    if candidate is None:
        return {
            "schema_version": 1,
            "kind": "pulsar-model-serving-preparation-check",
            "state": "blocked",
            "profile": profile_id,
            "model_id": check.get("model_id"),
            "revision": check.get("revision"),
            "expected_manifest": check.get("expected_manifest"),
            "home_rank": check.get("home_rank"),
            "target_ranks": targets,
            "blockers": check.get("blockers") or [
                "distributed catalog preparation is unavailable"
            ],
            "relocation_options": check.get("relocation_options") or [],
            "remediation": check.get("remediation") or [],
        }
    return {
        "schema_version": 1,
        "kind": "pulsar-model-serving-preparation-check",
        "state": "ready" if candidate["already_prepared"] else "needs-preparation",
        "profile": profile_id,
        "model_id": check["model_id"],
        "revision": check.get("revision"),
        "expected_manifest": check.get("expected_manifest"),
        "home_rank": check["home_rank"],
        "target_ranks": targets,
        "weights_gib": candidate.get("weights_gib"),
        "prepare_transport": candidate["prepare_transport"],
        "transfer": candidate["transfer"],
        "copy_streams": candidate["copy_streams"],
        "blockers": [],
        "relocation_options": [],
        "remediation": [],
    }


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
    term.field("serving", "model library · the only weight mechanism (ADR 0006)")
    term.field("catalog", f"{state.replace('-', ' ')} · read-only inventory")
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
        "Browsing does not automatically refresh the catalog, move model files, or start a model."
    )
    if state == "not-configured":
        term.emit(
            "No cached distributed catalog is configured. Serving requires one; running services are unaffected."
        )
        term.emit("To create one later: scripts/model-library.sh catalog refresh")
    elif state == "unavailable":
        term.emit(
            "Catalog state cannot be confirmed. Running services are unaffected; review the findings before library maintenance."
        )


def render_detail(
    report: dict[str, Any],
    profiles: dict[str, Any],
    index: int,
    width: int | None = None,
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
            "No prepared model-library runtime views are recorded.",
            initial_indent="  ",
            subsequent_indent="  ",
        )
    for instance in instances:
        parts = [
            _node_label(int(instance["rank"])),
            _runtime_label(instance.get("runtime_source")),
            _status_label(instance.get("retention")),
            f"identity {_validation_label(instance.get('identity_status'))}",
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
        "Claim boundary: catalog identity and runtime views do not establish model qualification or assign a release status."
    )

    check = preparation_check(report, profiles, index)
    term.blank()
    term.emit("Preparation options")
    if check["state"] == "available":
        for candidate in check["candidates"]:
            state = (
                "prepared; verify or rebuild"
                if candidate["already_prepared"]
                else "available"
            )
            term.emit(
                f"{candidate['profile']} · {candidate['nodes']} node(s) · {state}",
                initial_indent="  ",
                subsequent_indent="    ",
            )
        term.emit(
            "Preparation is explicit and does not start serving.",
            initial_indent="  ",
            subsequent_indent="  ",
        )
    else:
        term.emit(
            "Preparation blocked:",
            initial_indent="  ",
            subsequent_indent="  ",
        )
        for blocker in check["blockers"]:
            term.emit(
                blocker,
                initial_indent="  ",
                subsequent_indent="  ",
            )
        if check.get("relocation_options"):
            term.emit(
                "Choose one relocation destination:",
                initial_indent="  ",
                subsequent_indent="  ",
            )
            for command in check["relocation_options"]:
                term.emit(
                    command,
                    initial_indent="    ",
                    subsequent_indent="      ",
                )
        if check.get("remediation"):
            term.emit("Then:", initial_indent="  ", subsequent_indent="  ")
            for command in check["remediation"]:
                term.emit(
                    command,
                    initial_indent="    ",
                    subsequent_indent="      ",
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
    term.field("mechanism", "the model library serves every profile (ADR 0006)")
    term.field("catalog", "one durable home per exact revision")
    term.field("prepared", "working copies only on non-home serving nodes")
    term.field("home node", "uses its durable model through a hashed local view")
    term.field("pin", "retains non-home hot copies but still requires the durable home")
    term.blank()
    term.emit(
        "Every live profile uses local files on every rank (ADR 0006). Browsing does not change serving policy or qualify a model."
    )


def render_refresh(report: dict[str, Any], width: int | None = None) -> None:
    term = TerminalWriter(width=width)
    catalog = report["catalog"]
    term.emit("REFRESH DISTRIBUTED CATALOG")
    term.field("cached", _status_label(catalog.get("status")))
    if catalog.get("status") == "cached":
        term.field("refreshed", _catalog_age(catalog.get("refreshed_at")))
        term.field(
            "topology",
            "current"
            if catalog.get("topology_compatible") is True
            else "stale or unavailable",
        )
    term.blank()
    term.emit(
        "This rescans durable Hugging Face model homes on every confirmed rank and atomically updates the cached inventory."
    )
    term.emit(
        "It preserves explicit exact-revision primary selections. Incomplete rank or topology observation fails without fallback."
    )
    term.emit(
        "It does not download, copy, prepare, start, pin, purge, repair, or delete model files."
    )


def prepare_choice_labels(check: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for candidate in check.get("candidates") or []:
        verb = "Verify" if candidate.get("already_prepared") else "Prepare"
        scope = (
            "two-rank serving"
            if int(candidate.get("nodes") or 0) == 2
            else "one-rank serving"
        )
        labels.append(f"{verb} {candidate['profile']} for {scope}")
    return labels


def selected_preparation_candidate(
    check: dict[str, Any], candidate_index: int
) -> dict[str, Any]:
    candidates = check.get("candidates") or []
    if candidate_index < 0 or candidate_index >= len(candidates):
        raise ModelStorageContractError("preparation selection is out of range")
    return candidates[candidate_index]


def render_preparation(
    report: dict[str, Any],
    profiles: dict[str, Any],
    model_index: int,
    candidate_index: int,
    width: int | None = None,
) -> None:
    check = preparation_check(report, profiles, model_index)
    candidate = selected_preparation_candidate(check, candidate_index)
    term = TerminalWriter(width=width)
    heading = (
        "PREPARE FOR TWO-RANK SERVING"
        if int(candidate.get("nodes") or 0) == 2
        else "PREPARE FOR ONE-RANK SERVING"
    )
    term.emit(heading)
    term.field("profile", candidate["profile"])
    term.field("model", check["model_id"])
    term.field("revision", check.get("revision") or "unknown")
    term.field("manifest", check.get("expected_manifest") or "unavailable")
    label_width = 14
    term.field("serving nodes", candidate["nodes"], label_width=label_width)
    term.field(
        "durable home",
        _node_label(int(check["home_rank"])),
        label_width=label_width,
    )
    term.field(
        "transfer",
        candidate["transfer"],
        label_width=label_width,
    )
    term.field("fallback", "none", label_width=label_width)
    weights = candidate.get("weights_gib")
    term.field(
        "hot storage",
        (
            f"about {weights:g} GiB on each non-home serving node"
            if weights is not None
            else "one complete working copy on each non-home serving node"
        ),
        label_width=label_width,
    )
    term.blank()
    term.emit(
        "The preparation service will full-verify the durable home, check exact live capacity on every serving node, transfer only non-home bytes, and publish ready views only after every rank verifies."
    )
    term.emit(
        "This does not start or qualify a model. The durable home remains required."
    )


def render_serving_preparation(
    check: dict[str, Any], width: int | None = None
) -> None:
    term = TerminalWriter(width=width)
    targets = check.get("target_ranks") or []
    heading = (
        "DISTRIBUTED CATALOG · TWO-RANK SERVING"
        if len(targets) == 2
        else "DISTRIBUTED CATALOG · ONE-RANK SERVING"
    )
    term.emit(heading)
    term.field("profile", check.get("profile") or "unknown")
    if check.get("model_id"):
        term.field("model", check["model_id"])
    if check.get("revision"):
        term.field("revision", check["revision"])
    if check.get("expected_manifest"):
        term.field("manifest", check["expected_manifest"])
    if isinstance(check.get("home_rank"), int):
        term.field("durable home", _node_label(int(check["home_rank"])))
    if targets:
        term.field(
            "serving nodes",
            ", ".join(_node_label(int(rank)) for rank in targets),
        )
    term.field("readiness", _status_label(check.get("state")))
    if check.get("transfer"):
        term.field("transfer", check["transfer"])
        term.field("fallback", "none")
    term.blank()
    if check.get("state") == "blocked":
        term.emit("Distributed catalog serving is blocked:")
        for blocker in check.get("blockers") or []:
            term.emit(
                blocker,
                initial_indent="  ",
                subsequent_indent="  ",
            )
        if check.get("relocation_options"):
            term.blank()
            term.emit("Choose one relocation destination:")
            for command in check["relocation_options"]:
                term.emit(
                    command,
                    initial_indent="  ",
                    subsequent_indent="    ",
                )
        if check.get("remediation"):
            term.emit("Then refresh and prepare:")
            for command in check["remediation"]:
                term.emit(
                    command,
                    initial_indent="  ",
                    subsequent_indent="    ",
                )
        return
    if check.get("state") == "needs-preparation":
        term.emit(
            "The exact runtime views must be prepared and fully verified before the final start confirmation."
        )
    else:
        term.emit(
            "The selected ranks already have exact, witnessed runtime views ready for launch."
        )
    term.emit(
        "The durable home remains required. Readiness does not establish model qualification."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-file", required=True)
    parser.add_argument("--profiles-file", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    sub.add_parser("summary")
    sub.add_parser("choices")
    detail = sub.add_parser("detail")
    detail.add_argument("--index", type=int, required=True)
    sub.add_parser("findings")
    sub.add_parser("about")
    sub.add_parser("refresh")
    prepare_choices = sub.add_parser("prepare-choices")
    prepare_choices.add_argument("--index", type=int, required=True)
    prepare_profile = sub.add_parser("prepare-profile")
    prepare_profile.add_argument("--index", type=int, required=True)
    prepare_profile.add_argument("--candidate-index", type=int, required=True)
    prepare_command = sub.add_parser("prepare-command")
    prepare_command.add_argument("--index", type=int, required=True)
    prepare_command.add_argument("--candidate-index", type=int, required=True)
    prepare_preview = sub.add_parser("prepare-preview")
    prepare_preview.add_argument("--index", type=int, required=True)
    prepare_preview.add_argument("--candidate-index", type=int, required=True)
    serving_check = sub.add_parser("serving-check")
    serving_check.add_argument("--profile", required=True)
    serving_check.add_argument("--target-rank", type=int)
    serving_preview = sub.add_parser("serving-preview")
    serving_preview.add_argument("--profile", required=True)
    serving_preview.add_argument("--target-rank", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = load_report(args.report_file)
        profiles = load_profiles(args.profiles_file)
        if args.command == "validate":
            return 0
        if args.command == "summary":
            render_summary(report)
        elif args.command == "choices":
            for label in model_choice_labels(report):
                print(label)
        elif args.command == "detail":
            render_detail(report, profiles, args.index)
        elif args.command == "findings":
            render_findings(report)
        elif args.command == "about":
            render_about()
        elif args.command == "refresh":
            render_refresh(report)
        elif args.command == "prepare-choices":
            check = preparation_check(report, profiles, args.index)
            for label in prepare_choice_labels(check):
                print(label)
        elif args.command == "prepare-profile":
            check = preparation_check(report, profiles, args.index)
            candidate = selected_preparation_candidate(
                check, args.candidate_index
            )
            print(candidate["profile"])
        elif args.command == "prepare-command":
            check = preparation_check(report, profiles, args.index)
            candidate = selected_preparation_candidate(
                check, args.candidate_index
            )
            target = (
                str(candidate["target_ranks"][0])
                if int(candidate["nodes"]) == 1
                else ""
            )
            print(
                "\t".join(
                    [
                        candidate["profile"],
                        candidate["prepare_transport"],
                        str(candidate["copy_streams"]),
                        target,
                    ]
                )
            )
        elif args.command == "prepare-preview":
            render_preparation(
                report,
                profiles,
                args.index,
                args.candidate_index,
            )
        elif args.command in {"serving-check", "serving-preview"}:
            check = serving_preparation_check(
                report,
                profiles,
                args.profile,
                target_rank=args.target_rank,
            )
            if args.command == "serving-check":
                print(json.dumps(check, indent=2, sort_keys=True))
            else:
                render_serving_preparation(check)
        else:
            raise ModelStorageContractError("unsupported renderer command")
    except ModelStorageContractError as exc:
        print(f"model-storage: invalid health report: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
