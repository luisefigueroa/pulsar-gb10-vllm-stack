#!/usr/bin/env python3
"""Read-only trusted persistence for ADR 0004 Model Serving Release objects.

This module loads, verifies, and inspects immutable reviewed objects from a
tracked registry.  Its verified inspection result is the read-only source for
catalog and operator status projection.  It does not capture evidence, issue a
decision, change recommendation policy, authorize serving, or launch a release.
A public in-memory graph validator may check a prospective assembly of stored
plus proposed objects; no write command belongs here.
Schema ownership remains in the pure modules: this layer only assembles
caller-visible source sets and asks those validators to check them.

Publishable evidence hashing and review-metadata shape checks do not prove
privacy review, repository review, or physical behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

try:
    from scripts import (
        model_identity,
        model_serving_release,
        model_validation_evidence,
        terminal_format,
    )
except ModuleNotFoundError:
    import model_identity  # type: ignore[no-redef]
    import model_serving_release  # type: ignore[no-redef]
    import model_validation_evidence  # type: ignore[no-redef]
    import terminal_format  # type: ignore[no-redef]


DEFAULT_REGISTRY_RELATIVE = "models/model-serving-releases"
REGISTRY_OUTPUT_SCHEMA_VERSION = 1
OBJECT_FILENAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
ALLOWED_DOC_NAME = "README.md"
INSPECTION_UNIQUE = "unique-reviewed-decision"
INSPECTION_NONE = "no-reviewed-decision"
INSPECTION_AMBIGUOUS = "ambiguous"
PERSISTENCE_NOTES = (
    "The registry verifies stored objects only. It does not capture "
    "evidence, issue a decision, change recommendation policy, or launch "
    "a release.",
    "Publishable evidence hashing does not prove privacy review, "
    "repository review, or physical behavior.",
    "Validation status is advisory and is not serving authorization.",
)

NAMESPACE_SPECS: dict[str, dict[str, str]] = {
    "descriptors": {
        "kind": model_serving_release.MODEL_SERVING_RELEASE_KIND,
        "id_field": "release_id",
    },
    "contracts": {
        "kind": model_serving_release.VALIDATION_CONTRACT_KIND,
        "id_field": "contract_id",
    },
    "run-records": {
        "kind": model_validation_evidence.VALIDATION_RUN_RECORD_KIND,
        "id_field": "run_record_id",
    },
    "evidence-bundles": {
        "kind": model_validation_evidence.VALIDATION_EVIDENCE_BUNDLE_KIND,
        "id_field": "bundle_id",
    },
    "decisions": {
        "kind": model_validation_evidence.VALIDATION_DECISION_KIND,
        "id_field": "decision_id",
    },
}


class ModelServingReleaseRegistryError(ValueError):
    """The tracked registry layout or object graph is invalid."""


def fail(message: str) -> NoReturn:
    raise ModelServingReleaseRegistryError(message)


def default_registry_root(repo_root: Path) -> Path:
    return repo_root / DEFAULT_REGISTRY_RELATIVE


@dataclass
class RegistryGraph:
    """Loaded, identity-checked objects before and after graph validation."""

    repo_root: Path
    registry_root: Path
    descriptors: dict[str, dict[str, Any]] = field(default_factory=dict)
    contracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    run_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    evidence_bundles: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "descriptors": len(self.descriptors),
            "contracts": len(self.contracts),
            "run_records": len(self.run_records),
            "evidence_bundles": len(self.evidence_bundles),
            "decisions": len(self.decisions),
        }


def _lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        fail(f"{label}: {exc}")


def _require_real_directory(path: Path, *, label: str) -> None:
    info = _lstat(path, label=label)
    if stat.S_ISLNK(info.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISDIR(info.st_mode):
        fail(f"{label} must be a directory")


def _require_real_file(path: Path, *, label: str) -> None:
    info = _lstat(path, label=label)
    if stat.S_ISLNK(info.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular file")


def _open_nofollow(path: Path, *, binary: bool = False) -> Any:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        fail(f"{path}: {exc}")
    mode = "rb" if binary else "r"
    encoding = None if binary else "utf-8"
    return os.fdopen(fd, mode, encoding=encoding)


def _reject_json_constant(value: str) -> None:
    fail(f"JSON contains non-standard constant {value}")


def _unique_object_pairs(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _read_json_file(path: Path) -> Any:
    try:
        with _open_nofollow(path, binary=True) as handle:
            raw = handle.read()
    except OSError as exc:
        fail(f"{path}: {exc}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{path}: invalid UTF-8: {exc}")
    try:
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object_pairs,
            strict=True,
        )
    except json.JSONDecodeError as exc:
        fail(f"{path}: malformed JSON: {exc}")
    except ValueError as exc:
        fail(f"{path}: {exc}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with _open_nofollow(path, binary=True) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        fail(f"{path}: {exc}")
    return digest.hexdigest()


def _scan_entries(path: Path, *, label: str) -> list[os.DirEntry[str]]:
    _require_real_directory(path, label=label)
    try:
        return sorted(os.scandir(path), key=lambda item: item.name)
    except OSError as exc:
        fail(f"{label}: {exc}")


def resolve_repository_relative_file(
    repo_root: Path,
    relative: str,
    *,
    label: str,
) -> Path:
    """Resolve a normalized repo-relative path without following symlinks."""
    _require_real_directory(repo_root, label=f"{label} repository root")
    current = repo_root
    for part in PurePosixPath(relative).parts:
        current = current / part
        info = _lstat(current, label=f"{label} {relative}")
        if stat.S_ISLNK(info.st_mode):
            fail(f"{label}: evidence path {relative} contains a symlink")
    if not stat.S_ISREG(info.st_mode):
        fail(f"{label}: {relative} is not a regular file")
    try:
        current.relative_to(repo_root)
    except ValueError:
        fail(f"{label}: {relative} is outside the repository root")
    return current


def verify_publishable_evidence_artifact(
    artifact: dict[str, Any],
    *,
    repo_root: Path,
) -> None:
    artifact = model_validation_evidence.validate_evidence_artifact(artifact)
    if artifact["visibility"] != "publishable":
        return
    location = artifact["location"]
    path = resolve_repository_relative_file(
        repo_root,
        location["value"],
        label="publishable evidence artifact",
    )
    digest = _sha256_file(path)
    if digest != artifact["content"]["sha256"]:
        fail(
            "publishable evidence artifact digest mismatch for "
            f"{location['value']}"
        )


def _load_namespace(namespace_dir: Path, spec: dict[str, str]) -> dict[str, dict[str, Any]]:
    objects: dict[str, dict[str, Any]] = {}
    for entry in _scan_entries(namespace_dir, label=str(namespace_dir)):
        path = Path(entry.path)
        if entry.name == ALLOWED_DOC_NAME:
            _require_real_file(path, label=str(path))
            continue
        if entry.is_symlink():
            fail(f"{path} must not be a symlink")
        if entry.is_dir(follow_symlinks=False):
            fail(f"{path} is an unexpected subdirectory")
        if not entry.is_file(follow_symlinks=False):
            fail(f"{path} is not a regular file")
        if not OBJECT_FILENAME_RE.fullmatch(entry.name):
            fail(f"{path} is not a content-addressed registry object")
        document = _read_json_file(path)
        if not isinstance(document, dict):
            fail(f"{path} must contain a JSON object")
        kind = document.get("kind")
        if kind != spec["kind"]:
            fail(f"{path} has kind {kind!r}, expected {spec['kind']!r}")
        object_id = document.get(spec["id_field"])
        expected_id = entry.name[: -len(".json")]
        if object_id != expected_id:
            fail(
                f"{path} {spec['id_field']} {object_id!r} does not match "
                "filename"
            )
        if expected_id in objects:
            fail(f"{namespace_dir} contains duplicate id {expected_id}")
        objects[expected_id] = document
    return objects


def scan_registry(repo_root: Path) -> RegistryGraph:
    """Load namespace files without following symlinks or extra entries."""
    _require_real_directory(repo_root, label=str(repo_root))
    registry_root = default_registry_root(repo_root)
    seen: set[str] = set()
    graph = RegistryGraph(repo_root=repo_root, registry_root=registry_root)
    for entry in _scan_entries(registry_root, label=str(registry_root)):
        path = Path(entry.path)
        seen.add(entry.name)
        if entry.name == ALLOWED_DOC_NAME:
            _require_real_file(path, label=str(path))
            continue
        if entry.name not in NAMESPACE_SPECS:
            fail(f"{path} is not an allowed registry namespace")
        if entry.is_symlink():
            fail(f"{path} must not be a symlink")
        if not entry.is_dir(follow_symlinks=False):
            fail(f"{path} must be a namespace directory")
    missing = sorted(set(NAMESPACE_SPECS) - seen)
    if missing:
        fail(
            "registry is missing namespace directories: " + ", ".join(missing)
        )
    graph.descriptors = _load_namespace(
        registry_root / "descriptors", NAMESPACE_SPECS["descriptors"]
    )
    graph.contracts = _load_namespace(
        registry_root / "contracts", NAMESPACE_SPECS["contracts"]
    )
    graph.run_records = _load_namespace(
        registry_root / "run-records", NAMESPACE_SPECS["run-records"]
    )
    graph.evidence_bundles = _load_namespace(
        registry_root / "evidence-bundles", NAMESPACE_SPECS["evidence-bundles"]
    )
    graph.decisions = _load_namespace(
        registry_root / "decisions", NAMESPACE_SPECS["decisions"]
    )
    return graph


def _require_object(
    store: dict[str, dict[str, Any]],
    object_id: str,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        return store[object_id]
    except KeyError:
        fail(f"{label} {object_id} is not stored")


def _runs_for_bundle(
    graph: RegistryGraph, bundle: dict[str, Any]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for run_id in bundle["run_record_ids"]:
        records.append(
            _require_object(
                graph.run_records, run_id, label="evidence bundle run record"
            )
        )
    return records


def _source_set_for_decision(
    decision: dict[str, Any],
    graph: RegistryGraph,
    *,
    visiting: set[str] | None = None,
) -> dict[str, Any]:
    visiting = set() if visiting is None else set(visiting)
    decision_id = decision["decision_id"]
    if decision_id in visiting:
        fail("validation decision supersession lineage contains a cycle")
    visiting.add(decision_id)
    release = _require_object(
        graph.descriptors, decision["release_id"], label="decision release"
    )
    contract = _require_object(
        graph.contracts, decision["contract_id"], label="decision contract"
    )
    bundle = _require_object(
        graph.evidence_bundles,
        decision["evidence_bundle_id"],
        label="decision evidence bundle",
    )
    source = {
        "release": release,
        "contract": contract,
        "evidence_bundle": bundle,
        "run_records": _runs_for_bundle(graph, bundle),
        "decision": decision,
    }
    prior_sources = []
    for prior_id in decision["supersedes_decision_ids"]:
        prior = _require_object(
            graph.decisions, prior_id, label="superseded decision"
        )
        prior_sources.append(
            _source_set_for_decision(
                prior,
                graph,
                visiting=visiting,
            )
        )
    if prior_sources:
        source["prior_decision_sources"] = sorted(
            prior_sources,
            key=lambda item: item["decision"]["decision_id"],
        )
    visiting.remove(decision_id)
    return source


def _collect_predecessor_chain(
    contract: dict[str, Any],
    graph: RegistryGraph,
    *,
    visiting: set[str] | None = None,
) -> list[dict[str, Any]]:
    visiting = set() if visiting is None else set(visiting)
    relative = contract["release_criteria"]["relative_performance"]
    if relative["status"] != "required":
        return []
    decision_id = relative["predecessor_decision_id"]
    if decision_id in visiting:
        fail("comparable predecessor evidence contains a decision cycle")
    decision = _require_object(
        graph.decisions, decision_id, label="relative performance predecessor decision"
    )
    if decision["release_id"] != relative["predecessor_release_id"]:
        fail("relative performance predecessor release cross-link mismatch")
    if decision["contract_id"] != relative["predecessor_contract_id"]:
        fail("relative performance predecessor contract cross-link mismatch")
    if decision["evidence_bundle_id"] != relative["predecessor_bundle_id"]:
        fail("relative performance predecessor bundle cross-link mismatch")
    source = _source_set_for_decision(decision, graph)
    pred_contract = _require_object(
        graph.contracts, decision["contract_id"], label="predecessor contract"
    )
    ancestors = _collect_predecessor_chain(
        pred_contract, graph, visiting=visiting | {decision_id}
    )
    by_id = {
        item["decision"]["decision_id"]: item for item in [*ancestors, source]
    }
    return [by_id[item] for item in sorted(by_id)]


def _predecessor_registry_for(
    contract: dict[str, Any], graph: RegistryGraph
) -> list[dict[str, Any]]:
    return _collect_predecessor_chain(contract, graph)


def _validate_graph_objects(graph: RegistryGraph) -> None:
    for release in graph.descriptors.values():
        model_serving_release.validate_model_serving_release(release)

    for contract in graph.contracts.values():
        release = _require_object(
            graph.descriptors, contract["release_id"], label="contract release"
        )
        model_serving_release.validate_validation_contract(
            contract, expected_release=release
        )

    covered_run_ids: set[str] = set()
    for bundle in graph.evidence_bundles.values():
        release = _require_object(
            graph.descriptors, bundle["release_id"], label="bundle release"
        )
        contract = _require_object(
            graph.contracts, bundle["contract_id"], label="bundle contract"
        )
        records = _runs_for_bundle(graph, bundle)
        model_validation_evidence.validate_validation_evidence_bundle(
            bundle,
            release=release,
            contract=contract,
            run_records=records,
        )
        for artifact in bundle["evidence_artifacts"]:
            verify_publishable_evidence_artifact(
                artifact, repo_root=graph.repo_root
            )
        covered_run_ids.update(bundle["run_record_ids"])

    orphan_runs = sorted(set(graph.run_records) - covered_run_ids)
    if orphan_runs:
        fail(
            "run record is not referenced by a stored evidence bundle: "
            + orphan_runs[0]
        )

    for decision in graph.decisions.values():
        source = _source_set_for_decision(decision, graph)
        model_validation_evidence.validate_validation_decision(
            decision,
            release=source["release"],
            contract=source["contract"],
            evidence_bundle=source["evidence_bundle"],
            run_records=source["run_records"],
            predecessor_evidence_registry=_predecessor_registry_for(
                source["contract"], graph
            ),
            prior_decisions=(
                model_validation_evidence.prior_decisions_from_source_set(source)
            ),
        )


def validate_registry_graph(
    graph: RegistryGraph,
) -> RegistryGraph:
    """Validate an in-memory registry graph using the stored-registry rules.

    The graph may be a scanned checkout or a prospective assembly of existing
    verified objects plus proposed objects. This entry point does not write.
    Publishable evidence must already exist at its repository-relative path
    and match the declared digest.
    """
    _validate_graph_objects(graph)
    _validate_relative_performance_contracts(graph)
    _validate_unique_direct_superseders(graph)
    return graph


def load_registry(repo_root: Path) -> RegistryGraph:
    """Scan, identity-check, and fully validate the stored object graph."""
    return validate_registry_graph(scan_registry(repo_root))


def decision_source_set(
    graph: RegistryGraph, decision: dict[str, Any]
) -> dict[str, Any]:
    """Assemble one decision's caller-visible source set from a loaded graph."""
    return _source_set_for_decision(decision, graph)


def predecessor_source_sets(
    graph: RegistryGraph, contract: dict[str, Any]
) -> list[dict[str, Any]]:
    """Resolve the contract's frozen predecessor chain from a loaded graph."""
    return _predecessor_registry_for(contract, graph)


def _decisions_for_release(
    graph: RegistryGraph, release_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in graph.decisions.values()
        if item["release_id"] == release_id
    ]


def _contracts_for_release(
    graph: RegistryGraph, release_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in graph.contracts.values()
        if item["release_id"] == release_id
    ]


def _direct_superseders(
    graph: RegistryGraph, decision_id: str
) -> list[dict[str, Any]]:
    return [
        item
        for item in graph.decisions.values()
        if decision_id in item["supersedes_decision_ids"]
    ]


def _unsuperseded_heads(
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    superseded = {
        parent_id
        for item in decisions
        for parent_id in item["supersedes_decision_ids"]
    }
    return [
        item for item in decisions if item["decision_id"] not in superseded
    ]


def _lineage_source_sets(
    graph: RegistryGraph, *, release_id: str, contract_id: str
) -> list[dict[str, Any]]:
    sources = [
        _source_set_for_decision(item, graph)
        for item in graph.decisions.values()
        if item["release_id"] == release_id
        and item["contract_id"] == contract_id
    ]
    return sorted(sources, key=lambda item: item["decision"]["decision_id"])


def _validate_relative_performance_contracts(graph: RegistryGraph) -> None:
    """Resolve every frozen predecessor reference even without a decision."""
    for contract in graph.contracts.values():
        relative = contract["release_criteria"]["relative_performance"]
        if relative["status"] != "required":
            continue
        release = _require_object(
            graph.descriptors, contract["release_id"], label="contract release"
        )
        model_validation_evidence.validate_predecessor_evidence_registry(
            release=release,
            contract=contract,
            predecessor_evidence_registry=_predecessor_registry_for(
                contract, graph
            ),
        )


def _validate_unique_direct_superseders(graph: RegistryGraph) -> None:
    """Reject two later decisions that directly supersede the same record."""
    lineages: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for decision in graph.decisions.values():
        key = (decision["release_id"], decision["contract_id"])
        lineages.setdefault(key, []).append(decision)
    for (release_id, contract_id), _decisions in lineages.items():
        sources = _lineage_source_sets(
            graph, release_id=release_id, contract_id=contract_id
        )
        predecessor = _predecessor_registry_for(
            _require_object(
                graph.contracts, contract_id, label="decision contract"
            ),
            graph,
        )
        for decision in _decisions:
            model_validation_evidence.effective_validation_status(
                decision,
                decision_evidence_registry=sources,
                predecessor_evidence_registry=predecessor,
            )


def inspect_release(graph: RegistryGraph, release_id: str) -> dict[str, Any]:
    """Return informational inspection state for one stored release."""
    if not model_identity.SHA256_HEX_RE.fullmatch(release_id):
        fail("release_id must be a sha256 content ID")
    release = _require_object(graph.descriptors, release_id, label="release")
    contracts = sorted(
        _contracts_for_release(graph, release_id),
        key=lambda item: item["contract_id"],
    )
    decisions = sorted(
        _decisions_for_release(graph, release_id),
        key=lambda item: item["decision_id"],
    )
    contract_ids = [item["contract_id"] for item in contracts]
    decision_ids = [item["decision_id"] for item in decisions]
    heads = _unsuperseded_heads(decisions)
    head_ids = [item["decision_id"] for item in heads]
    inspection: dict[str, Any] = {
        "state": INSPECTION_NONE,
        "effective_status": None,
        "effective_status_label": None,
        "contract_ids": contract_ids,
        "decision_ids": decision_ids,
        "unsuperseded_decision_ids": head_ids,
        "unique_contract_id": None,
        "unique_decision_id": None,
    }
    if len(contract_ids) > 1 or len(heads) > 1:
        inspection["state"] = INSPECTION_AMBIGUOUS
        return {
            "release": release,
            "inspection": inspection,
            "notes": list(PERSISTENCE_NOTES),
        }
    if not heads:
        inspection["state"] = INSPECTION_NONE
        return {
            "release": release,
            "inspection": inspection,
            "notes": [
                "Absence of a reviewed decision is not Untested.",
                *PERSISTENCE_NOTES,
            ],
        }
    head = heads[0]
    sources = _lineage_source_sets(
        graph, release_id=release_id, contract_id=head["contract_id"]
    )
    status = model_validation_evidence.effective_validation_status(
        head,
        decision_evidence_registry=sources,
        predecessor_evidence_registry=_predecessor_registry_for(
            _require_object(
                graph.contracts, head["contract_id"], label="decision contract"
            ),
            graph,
        ),
    )
    inspection.update(
        {
            "state": INSPECTION_UNIQUE,
            "effective_status": status,
            "effective_status_label": (
                model_validation_evidence.validation_status_label(status)
            ),
            "unique_contract_id": head["contract_id"],
            "unique_decision_id": head["decision_id"],
        }
    )
    return {
        "release": release,
        "inspection": inspection,
        "notes": list(PERSISTENCE_NOTES),
    }


def inspect_decision(graph: RegistryGraph, decision_id: str) -> dict[str, Any]:
    """Return stored base outcome and effective projection for one decision."""
    if not model_identity.SHA256_HEX_RE.fullmatch(decision_id):
        fail("decision_id must be a sha256 content ID")
    decision = _require_object(graph.decisions, decision_id, label="decision")
    sources = _lineage_source_sets(
        graph,
        release_id=decision["release_id"],
        contract_id=decision["contract_id"],
    )
    effective = model_validation_evidence.effective_validation_status(
        decision,
        decision_evidence_registry=sources,
        predecessor_evidence_registry=_predecessor_registry_for(
            _require_object(
                graph.contracts,
                decision["contract_id"],
                label="decision contract",
            ),
            graph,
        ),
    )
    superseded_by = sorted(
        item["decision_id"] for item in _direct_superseders(graph, decision_id)
    )
    return {
        "decision": decision,
        "base_status": decision["status"],
        "base_status_label": model_validation_evidence.validation_status_label(
            decision["status"]
        ),
        "effective_status": effective,
        "effective_status_label": (
            model_validation_evidence.validation_status_label(effective)
        ),
        "superseded_by_decision_ids": superseded_by,
        "notes": list(PERSISTENCE_NOTES),
    }


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _writer() -> terminal_format.TerminalWriter:
    return terminal_format.TerminalWriter()


def render_verify(graph: RegistryGraph) -> None:
    term = _writer()
    label_width = 10
    term.emit("Model Serving Release registry")
    term.blank()
    term.field("Root", DEFAULT_REGISTRY_RELATIVE, label_width=label_width)
    term.field("State", "verified", label_width=label_width)
    counts = graph.counts()
    term.field(
        "Objects",
        f"{counts['descriptors']} descriptors",
        label_width=label_width,
    )
    term.emit(
        f"{counts['contracts']} contracts",
        initial_indent=" " * label_width,
        subsequent_indent=" " * label_width,
    )
    term.emit(
        f"{counts['run_records']} run-records",
        initial_indent=" " * label_width,
        subsequent_indent=" " * label_width,
    )
    term.emit(
        f"{counts['evidence_bundles']} evidence-bundles",
        initial_indent=" " * label_width,
        subsequent_indent=" " * label_width,
    )
    term.emit(
        f"{counts['decisions']} decisions",
        initial_indent=" " * label_width,
        subsequent_indent=" " * label_width,
    )
    term.blank()
    term.emit("Notes")
    for note in PERSISTENCE_NOTES:
        term.emit(note, initial_indent="  ", subsequent_indent="  ")


def render_release(payload: dict[str, Any]) -> None:
    term = _writer()
    inspection = payload["inspection"]
    term.emit("Model Serving Release")
    term.blank()
    term.field("ID", payload["release"]["release_id"], label_width=12)
    term.field("Inspection", inspection["state"], label_width=12)
    contract_ids = inspection["contract_ids"] or ["none"]
    term.field("Contracts", contract_ids[0], label_width=12)
    for item in contract_ids[1:]:
        term.emit(item, initial_indent=" " * 12, subsequent_indent=" " * 12)
    decision_ids = inspection["decision_ids"] or ["none"]
    term.field("Decisions", decision_ids[0], label_width=12)
    for item in decision_ids[1:]:
        term.emit(item, initial_indent=" " * 12, subsequent_indent=" " * 12)
    if inspection["state"] == INSPECTION_UNIQUE:
        status_text = inspection["effective_status_label"]
    elif inspection["state"] == INSPECTION_AMBIGUOUS:
        status_text = (
            "unavailable — multiple contract lineages or unsuperseded heads"
        )
    else:
        status_text = "no reviewed decision"
    term.field("Status", status_text, label_width=12)
    if inspection["unsuperseded_decision_ids"] and inspection["state"] == (
        INSPECTION_AMBIGUOUS
    ):
        heads = inspection["unsuperseded_decision_ids"]
        term.field("Heads", heads[0], label_width=12)
        for item in heads[1:]:
            term.emit(item, initial_indent=" " * 12, subsequent_indent=" " * 12)
    term.blank()
    term.emit("Notes")
    for note in payload["notes"]:
        term.emit(note, initial_indent="  ", subsequent_indent="  ")


def render_decision(payload: dict[str, Any]) -> None:
    term = _writer()
    decision = payload["decision"]
    term.emit("Validation decision")
    term.blank()
    term.field("ID", decision["decision_id"], label_width=12)
    term.field("Release", decision["release_id"], label_width=12)
    term.field("Contract", decision["contract_id"], label_width=12)
    term.field("Bundle", decision["evidence_bundle_id"], label_width=12)
    term.field("Base", payload["base_status_label"], label_width=12)
    term.field("Effective", payload["effective_status_label"], label_width=12)
    supersedes = decision["supersedes_decision_ids"] or ["none"]
    term.field("Supersedes", supersedes[0], label_width=12)
    for item in supersedes[1:]:
        term.emit(item, initial_indent=" " * 12, subsequent_indent=" " * 12)
    superseded_by = payload["superseded_by_decision_ids"] or ["none"]
    term.field("Superseded", superseded_by[0], label_width=12)
    for item in superseded_by[1:]:
        term.emit(item, initial_indent=" " * 12, subsequent_indent=" " * 12)
    term.blank()
    term.emit("Notes")
    for note in payload["notes"]:
        term.emit(note, initial_indent="  ", subsequent_indent="  ")


def verify_payload(graph: RegistryGraph) -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_OUTPUT_SCHEMA_VERSION,
        "ok": True,
        "command": "verify",
        "registry_root": DEFAULT_REGISTRY_RELATIVE,
        "counts": graph.counts(),
        "descriptor_ids": sorted(graph.descriptors),
        "contract_ids": sorted(graph.contracts),
        "run_record_ids": sorted(graph.run_records),
        "evidence_bundle_ids": sorted(graph.evidence_bundles),
        "decision_ids": sorted(graph.decisions),
        "notes": list(PERSISTENCE_NOTES),
    }


def release_payload(graph: RegistryGraph, release_id: str) -> dict[str, Any]:
    inspected = inspect_release(graph, release_id)
    inspection = inspected["inspection"]
    return {
        "schema_version": REGISTRY_OUTPUT_SCHEMA_VERSION,
        "ok": True,
        "command": "show-release",
        "release_id": release_id,
        "model_access_contract": inspected["release"]["serving_recipe"][
            "model_access_contract"
        ],
        "inspection": inspection,
        "notes": inspected["notes"],
    }


def decision_payload(graph: RegistryGraph, decision_id: str) -> dict[str, Any]:
    inspected = inspect_decision(graph, decision_id)
    decision = inspected["decision"]
    return {
        "schema_version": REGISTRY_OUTPUT_SCHEMA_VERSION,
        "ok": True,
        "command": "show-decision",
        "decision_id": decision_id,
        "release_id": decision["release_id"],
        "contract_id": decision["contract_id"],
        "evidence_bundle_id": decision["evidence_bundle_id"],
        "base_status": inspected["base_status"],
        "base_status_label": inspected["base_status_label"],
        "effective_status": inspected["effective_status"],
        "effective_status_label": inspected["effective_status_label"],
        "supersedes_decision_ids": decision["supersedes_decision_ids"],
        "superseded_by_decision_ids": inspected["superseded_by_decision_ids"],
        "notes": inspected["notes"],
    }


def _error_payload(command: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_OUTPUT_SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "error": message,
    }


def _redact_repo_root(message: str, repo_root: Path) -> str:
    """Keep local checkout paths out of human and machine error output."""
    raw = str(repo_root)
    if raw in {"", ".", "/"}:
        return message
    prefix = raw.rstrip("/")
    message = message.replace(prefix + "/", "")
    return message.replace(prefix, "<repository-root>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and inspect the read-only ADR 0004 Model Serving "
            "Release registry"
        )
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        help=argparse.SUPPRESS,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser(
        "verify", help="Verify the stored registry graph"
    )
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)

    show_release = subparsers.add_parser(
        "show-release", help="Inspect one stored release"
    )
    show_release.add_argument("release_id")
    show_release.add_argument("--json", action="store_true")
    show_release.set_defaults(func=cmd_show_release)

    show_decision = subparsers.add_parser(
        "show-decision", help="Inspect one stored decision"
    )
    show_decision.add_argument("decision_id")
    show_decision.add_argument("--json", action="store_true")
    show_decision.set_defaults(func=cmd_show_decision)
    return parser


def _graph_from_args(args: argparse.Namespace) -> RegistryGraph:
    return load_registry(Path(args.repo_root))


def cmd_verify(args: argparse.Namespace) -> int:
    graph = _graph_from_args(args)
    if args.json:
        _emit_json(verify_payload(graph))
    else:
        render_verify(graph)
    return 0


def cmd_show_release(args: argparse.Namespace) -> int:
    graph = _graph_from_args(args)
    inspected = inspect_release(graph, args.release_id)
    if inspected["inspection"]["state"] == INSPECTION_AMBIGUOUS:
        message = (
            "release inspection is ambiguous; multiple contract lineages "
            "or unsuperseded heads prevent one reviewed status"
        )
        if args.json:
            payload = release_payload(graph, args.release_id)
            payload["ok"] = False
            payload["error"] = message
            _emit_json(payload)
        else:
            render_release(inspected)
            print(f"model-serving-release-registry: ERROR: {message}", file=sys.stderr)
        return 1
    if args.json:
        _emit_json(release_payload(graph, args.release_id))
    else:
        render_release(inspected)
    return 0


def cmd_show_decision(args: argparse.Namespace) -> int:
    graph = _graph_from_args(args)
    inspected = inspect_decision(graph, args.decision_id)
    if args.json:
        _emit_json(decision_payload(graph, args.decision_id))
    else:
        render_decision(inspected)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    try:
        return int(args.func(args))
    except (
        ModelServingReleaseRegistryError,
        model_serving_release.ModelServingReleaseError,
        model_validation_evidence.ModelValidationEvidenceError,
        model_identity.ModelIdentityError,
        OSError,
    ) as exc:
        message = _redact_repo_root(str(exc), Path(args.repo_root))
        if args.json:
            _emit_json(_error_payload(command, message))
        else:
            print(
                f"model-serving-release-registry: ERROR: {message}",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
