#!/usr/bin/env python3
"""Assemble, validate, render, and atomically write cluster topology manifests."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import itertools
import json
import os
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

try:
    from scripts.terminal_format import TerminalWriter
except ModuleNotFoundError:
    from terminal_format import TerminalWriter


SCHEMA_VERSION = 1
SAFE_ENDPOINT = re.compile(r"^[A-Za-z0-9._:@%+-]+$")
SAFE_IFACE = re.compile(r"^[A-Za-z0-9_.:@+-]+$")


class TopologyError(ValueError):
    pass


def fail(message: str) -> None:
    raise TopologyError(message)


def load_json(path: str | pathlib.Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path}: expected a JSON object")
    return value


def clean_text(value: Any, field: str) -> str:
    text = str(value or "")
    if not text or "\t" in text or "\r" in text or "\n" in text:
        fail(f"{field}: missing or contains control characters")
    return text


def safe_endpoint(value: Any, field: str) -> str:
    text = clean_text(value, field)
    if text.startswith("-") or not SAFE_ENDPOINT.fullmatch(text):
        fail(f"{field}: unsafe endpoint {text!r}")
    return text


def safe_interface(value: Any, field: str) -> str:
    text = clean_text(value, field)
    if not SAFE_IFACE.fullmatch(text):
        fail(f"{field}: unsafe interface {text!r}")
    return text


def valid_ip(value: Any, field: str) -> str:
    text = clean_text(value, field)
    try:
        return str(ipaddress.ip_address(text))
    except ValueError as exc:
        fail(f"{field}: {exc}")


def valid_cidr(value: Any, field: str) -> str:
    text = clean_text(value, field)
    try:
        return str(ipaddress.ip_interface(text))
    except ValueError as exc:
        fail(f"{field}: {exc}")


def normalize_probe(raw: dict[str, Any], path: str) -> dict[str, Any]:
    if raw.get("probe_schema_version") != 1:
        fail(f"{path}: unsupported probe schema")
    node = {
        "node_id": clean_text(raw.get("node_id"), f"{path}.node_id"),
        "hostname": clean_text(raw.get("hostname"), f"{path}.hostname"),
        "ssh_host": safe_endpoint(raw.get("ssh_host"), f"{path}.ssh_host"),
        "local": bool(raw.get("local")),
        "arch": str(raw.get("arch") or ""),
        "gpu": str(raw.get("gpu") or ""),
        "docker_ok": bool(raw.get("docker_ok")),
        "docker_nvidia": bool(raw.get("docker_nvidia")),
        "qualified": bool(raw.get("qualified")),
        "reject_reasons": [str(x) for x in raw.get("reject_reasons") or []],
    }
    control = raw.get("control") or {}
    try:
        node["control"] = {
            "interface": safe_interface(
                control.get("interface"), f"{path}.control.interface"
            ),
            "ip": valid_ip(control.get("ip"), f"{path}.control.ip"),
        }
    except TopologyError:
        if node["qualified"]:
            raise
        node["control"] = {"interface": "", "ip": ""}

    rdma: list[dict[str, Any]] = []
    for index, raw_link in enumerate(raw.get("rdma") or []):
        hca = safe_interface(raw_link.get("hca"), f"{path}.rdma[{index}].hca")
        netdev = safe_interface(
            raw_link.get("netdev"), f"{path}.rdma[{index}].netdev"
        )
        cidrs = [
            valid_cidr(cidr, f"{path}.rdma[{index}].cidrs")
            for cidr in raw_link.get("cidrs") or []
        ]
        rdma.append({"hca": hca, "netdev": netdev, "cidrs": sorted(set(cidrs))})
    node["rdma"] = rdma
    return node


def rdma_endpoints(node: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = {}
    for link in node.get("rdma") or []:
        for cidr in link.get("cidrs") or []:
            interface = ipaddress.ip_interface(cidr)
            network = str(interface.network)
            result.setdefault(network, []).append(
                {
                    "hca": link["hca"],
                    "netdev": link["netdev"],
                    "ip": str(interface.ip),
                }
            )
    return result


def rails_between(
    first: dict[str, Any], second: dict[str, Any]
) -> list[dict[str, Any]]:
    a_endpoints = rdma_endpoints(first)
    b_endpoints = rdma_endpoints(second)
    rails: list[dict[str, Any]] = []
    for network in sorted(set(a_endpoints) & set(b_endpoints)):
        candidates = [
            (a, b)
            for a in a_endpoints[network]
            for b in b_endpoints[network]
            if a["ip"] != b["ip"]
        ]
        if not candidates:
            continue
        a, b = sorted(
            candidates,
            key=lambda pair: (
                pair[0]["hca"],
                pair[0]["ip"],
                pair[1]["hca"],
                pair[1]["ip"],
            ),
        )[0]
        rails.append({"network": network, "a": a, "b": b})
    return rails


def largest_mesh(
    local: dict[str, Any], remotes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(remotes) <= 14:
        for size in range(len(remotes), -1, -1):
            for subset in itertools.combinations(remotes, size):
                nodes = [local, *subset]
                if all(
                    rails_between(nodes[a], nodes[b])
                    for a in range(len(nodes))
                    for b in range(a + 1, len(nodes))
                ):
                    return nodes
    selected = [local]
    for candidate in remotes:
        if all(rails_between(candidate, present) for present in selected):
            selected.append(candidate)
    return selected


def topology_digest(topology: dict[str, Any]) -> str:
    identity = {
        "nodes": topology["nodes"],
        "links": topology["links"],
    }
    payload = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assemble(paths: list[str], existing_path: str | None) -> dict[str, Any]:
    if not paths:
        fail("no node probes supplied")
    probes = [normalize_probe(load_json(path), path) for path in paths]
    local_candidates = [probe for probe in probes if probe["local"]]
    if len(local_candidates) != 1:
        fail("discovery must contain exactly one local probe")
    local = local_candidates[0]

    rejected: list[dict[str, Any]] = []
    if not local["qualified"]:
        return {
            "schema_version": SCHEMA_VERSION,
            "result": "incomplete",
            "topology": None,
            "rejected": [
                {
                    "ssh_host": local["ssh_host"],
                    "hostname": local["hostname"],
                    "reasons": local["reject_reasons"],
                }
            ],
        }

    by_id: dict[str, dict[str, Any]] = {local["node_id"]: local}

    def ssh_preference(probe: dict[str, Any]) -> tuple[int, str]:
        host = probe["ssh_host"].lower()
        hostname = probe["hostname"].lower()
        if host in (hostname, f"{hostname}.local"):
            return (0, host)
        if host == str(probe["control"]["ip"]).lower():
            return (1, host)
        rdma_ips = {
            str(ipaddress.ip_interface(cidr).ip)
            for link in probe.get("rdma") or []
            for cidr in link.get("cidrs") or []
        }
        if probe["ssh_host"] in rdma_ips:
            return (3, host)
        return (2, host)

    for probe in probes:
        if probe is local:
            continue
        if not probe["qualified"]:
            rejected.append(
                {
                    "ssh_host": probe["ssh_host"],
                    "hostname": probe["hostname"],
                    "reasons": probe["reject_reasons"],
                }
            )
            continue
        if probe["node_id"] in by_id:
            current = by_id[probe["node_id"]]
            if current is not local and ssh_preference(probe) < ssh_preference(current):
                by_id[probe["node_id"]] = probe
                probe = current
            rejected.append(
                {
                    "ssh_host": probe["ssh_host"],
                    "hostname": probe["hostname"],
                    "reasons": ["duplicate node identity"],
                }
            )
            continue
        by_id[probe["node_id"]] = probe

    existing_order: dict[str, int] = {}
    if existing_path and pathlib.Path(existing_path).is_file():
        old = load_json(existing_path)
        if "topology" in old:
            old = old.get("topology") or {}
        for item in old.get("nodes") or []:
            node_id = str(item.get("node_id") or "")
            rank = item.get("rank")
            if node_id and isinstance(rank, int):
                existing_order[node_id] = rank

    remotes = [node for node_id, node in by_id.items() if node_id != local["node_id"]]
    remotes.sort(
        key=lambda node: (
            existing_order.get(node["node_id"], 1_000_000),
            node["hostname"].lower(),
            node["node_id"],
        )
    )
    selected = largest_mesh(local, remotes)
    selected_ids = {node["node_id"] for node in selected}
    for node in remotes:
        if node["node_id"] not in selected_ids:
            rejected.append(
                {
                    "ssh_host": node["ssh_host"],
                    "hostname": node["hostname"],
                    "reasons": [
                        "not part of the largest directly connected RoCE group containing this node"
                    ],
                }
            )

    nodes: list[dict[str, Any]] = []
    for rank, probe in enumerate(selected):
        nodes.append(
            {
                "rank": rank,
                "node_id": probe["node_id"],
                "hostname": probe["hostname"],
                "ssh_host": "local" if rank == 0 else probe["ssh_host"],
                "control": probe["control"],
                "gpu": probe["gpu"],
                "rdma": probe["rdma"],
            }
        )

    links: list[dict[str, Any]] = []
    for a in range(len(nodes)):
        for b in range(a + 1, len(nodes)):
            rails = rails_between(nodes[a], nodes[b])
            links.append({"ranks": [a, b], "rails": rails})

    rail_counts = [len(link["rails"]) for link in links]
    topology = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "links": links,
        "validation": {
            "class": "roce-full-mesh",
            "full_mesh": all(rail_counts) if links else True,
            "connectivity_verified": False,
            "min_rails_per_pair": min(rail_counts) if rail_counts else 0,
        },
    }
    topology["topology_id"] = topology_digest(topology)
    validate_manifest(topology)
    return {
        "schema_version": SCHEMA_VERSION,
        "result": "ok",
        "topology": topology,
        "rejected": rejected,
    }


def validate_manifest(topology: dict[str, Any], require_verified: bool = False) -> None:
    if topology.get("schema_version") != SCHEMA_VERSION:
        fail("unsupported topology schema")
    nodes = topology.get("nodes")
    links = topology.get("links")
    validation = topology.get("validation")
    if not isinstance(nodes, list) or not nodes:
        fail("topology.nodes must be a non-empty array")
    if not isinstance(links, list) or not isinstance(validation, dict):
        fail("topology.links/validation malformed")
    if [node.get("rank") for node in nodes] != list(range(len(nodes))):
        fail("node ranks must be contiguous and start at zero")

    node_ids: set[str] = set()
    control_ips: set[str] = set()
    for rank, node in enumerate(nodes):
        node_id = clean_text(node.get("node_id"), f"nodes[{rank}].node_id")
        if node_id in node_ids:
            fail("duplicate node_id")
        node_ids.add(node_id)
        clean_text(node.get("hostname"), f"nodes[{rank}].hostname")
        safe_endpoint(node.get("ssh_host"), f"nodes[{rank}].ssh_host")
        control = node.get("control") or {}
        control_ip = valid_ip(control.get("ip"), f"nodes[{rank}].control.ip")
        safe_interface(
            control.get("interface"), f"nodes[{rank}].control.interface"
        )
        if control_ip in control_ips:
            fail("duplicate control-plane IP")
        control_ips.add(control_ip)
        if node.get("gpu") != "NVIDIA GB10":
            fail(f"rank {rank}: not an NVIDIA GB10")
        for index, rdma in enumerate(node.get("rdma") or []):
            safe_interface(rdma.get("hca"), f"nodes[{rank}].rdma[{index}].hca")
            safe_interface(
                rdma.get("netdev"), f"nodes[{rank}].rdma[{index}].netdev"
            )
            for cidr in rdma.get("cidrs") or []:
                valid_cidr(cidr, f"nodes[{rank}].rdma[{index}].cidr")

    expected_pairs = {
        (a, b) for a in range(len(nodes)) for b in range(a + 1, len(nodes))
    }
    observed_pairs: set[tuple[int, int]] = set()
    rail_counts: list[int] = []
    for index, link in enumerate(links):
        ranks = link.get("ranks")
        if (
            not isinstance(ranks, list)
            or len(ranks) != 2
            or not all(isinstance(rank, int) for rank in ranks)
        ):
            fail(f"links[{index}].ranks malformed")
        pair = (ranks[0], ranks[1])
        if pair not in expected_pairs or pair in observed_pairs:
            fail(f"links[{index}]: invalid or duplicate rank pair")
        observed_pairs.add(pair)
        rails = link.get("rails")
        if not isinstance(rails, list) or not rails:
            fail(f"links[{index}]: no shared RoCE rails")
        rail_counts.append(len(rails))
        for rail_index, rail in enumerate(rails):
            valid_cidr(
                rail.get("network"), f"links[{index}].rails[{rail_index}].network"
            )
            for side in ("a", "b"):
                endpoint = rail.get(side) or {}
                safe_interface(
                    endpoint.get("hca"),
                    f"links[{index}].rails[{rail_index}].{side}.hca",
                )
                safe_interface(
                    endpoint.get("netdev"),
                    f"links[{index}].rails[{rail_index}].{side}.netdev",
                )
                valid_ip(
                    endpoint.get("ip"),
                    f"links[{index}].rails[{rail_index}].{side}.ip",
                )
    if observed_pairs != expected_pairs:
        fail("links do not cover every rank pair")
    if validation.get("class") != "roce-full-mesh":
        fail("unsupported validation class")
    if validation.get("full_mesh") is not True:
        fail("topology is not a full mesh")
    actual_min = min(rail_counts) if rail_counts else 0
    if validation.get("min_rails_per_pair") != actual_min:
        fail("min_rails_per_pair does not match links")
    if require_verified and validation.get("connectivity_verified") is not True:
        fail("pairwise RoCE connectivity has not been verified")

    topology_id = clean_text(topology.get("topology_id"), "topology_id")
    if topology_id != topology_digest(topology):
        fail("topology_id digest mismatch")


def extract_topology(document: dict[str, Any]) -> dict[str, Any]:
    topology = document.get("topology") if "topology" in document else document
    if not isinstance(topology, dict):
        fail("document has no usable topology")
    return topology


def render(document: dict[str, Any], skipped_ssh: int = 0) -> None:
    topology = extract_topology(document)
    validate_manifest(topology)
    if skipped_ssh < 0:
        fail("skipped SSH address count cannot be negative")

    term = TerminalWriter()

    def field(label: str, value: str, indent: int = 0) -> None:
        term.field(label, value, indent=indent, label_width=12)

    nodes = topology["nodes"]
    validation = topology["validation"]
    link_count = validation["min_rails_per_pair"]
    link_word = "link" if link_count == 1 else "links"
    node_word = "system" if len(nodes) == 1 else "systems"
    check_count = sum(2 * len(link["rails"]) for link in topology["links"])

    term.emit("CLUSTER DISCOVERY")
    field("Nodes", f"{len(nodes)} GB10 {node_word}")
    if len(nodes) == 1:
        field("Fabric", "single node · no cluster links required")
        field("Checks", "not needed · no other cluster nodes found")
    else:
        field(
            "Fabric",
            f"full mesh · at least {link_count} RoCE {link_word} "
            "between each node pair",
        )
        if validation.get("connectivity_verified") is True:
            field(
                "Checks",
                f"PASS · {check_count}/{check_count} connections reachable",
            )
        else:
            field("Checks", "not yet verified")
    field("Cluster ID", topology["topology_id"][:12])

    term.blank()
    term.emit("NODES")
    for node in nodes:
        hcas = [link["hca"] for link in node.get("rdma") or []]
        if node["rank"] == 0:
            position = "this node"
        else:
            position = f"cluster node {node['rank'] + 1}"
        if node["rank"] > 0:
            term.blank()
        term.emit(
            f"{position} · {node['hostname']}",
            initial_indent="  ",
            subsequent_indent="    ",
        )
        field("SSH", node["ssh_host"], indent=4)
        field(
            "Cluster IP",
            f"{node['control']['ip']} · interface "
            f"{node['control']['interface']}",
            indent=4,
        )
        adapter_word = "adapter" if len(hcas) == 1 else "adapters"
        field(
            "RoCE",
            f"{len(hcas)} {adapter_word} · {', '.join(hcas) if hcas else 'none'}",
            indent=4,
        )

    rejected = document.get("rejected") or []
    if rejected or skipped_ssh:
        term.blank()
        term.emit("DISCOVERY NOTES")
    for item in rejected:
        ssh_host = item.get("ssh_host") or "?"
        hostname = item.get("hostname") or "?"
        reasons = item.get("reasons") or ["not qualified"]
        if reasons == ["duplicate node identity"]:
            detail = f"{ssh_host} · same system already listed as {hostname}"
        else:
            detail = f"{ssh_host} · {'; '.join(reasons)}"
        field("Ignored", detail)
    if skipped_ssh:
        address_word = "address" if skipped_ssh == 1 else "addresses"
        field(
            "SSH",
            f"{skipped_ssh} advertised {address_word} could not be checked "
            "with saved keys",
        )
        field(
            "Help",
            "These are usually unrelated devices. If a GB10 is missing, set "
            "up key-based SSH and rerun with --candidate HOST.",
        )


def render_save(document: dict[str, Any], destination: str) -> None:
    topology = extract_topology(document)
    validate_manifest(topology, require_verified=True)
    clean_text(destination, "destination")
    term = TerminalWriter()

    def field(label: str, value: str) -> None:
        term.field(label, value, label_width=12)

    nodes = topology["nodes"]
    node_word = "node" if len(nodes) == 1 else "nodes"

    term.emit("SAVE CLUSTER MEMBERSHIP")
    field("File", destination)
    field("Membership", f"{len(nodes)} {node_word} shown above")
    field(
        "Models",
        "unchanged · wizard will offer only exact validated configurations",
    )
    field(
        "Effect",
        "saves cluster membership only; does not validate or create a model "
        "profile",
    )


def rows(topology: dict[str, Any]) -> None:
    validate_manifest(topology, require_verified=True)
    validation = topology["validation"]
    print(
        "\t".join(
            [
                "META",
                str(len(topology["nodes"])),
                topology["topology_id"],
                "1" if validation["full_mesh"] else "0",
                str(validation["min_rails_per_pair"]),
            ]
        )
    )
    for node in topology["nodes"]:
        hcas = ",".join(link["hca"] for link in node.get("rdma") or [])
        netdevs = ",".join(
            sorted({link["netdev"] for link in node.get("rdma") or []})
        )
        print(
            "\t".join(
                [
                    "NODE",
                    str(node["rank"]),
                    node["node_id"],
                    node["hostname"],
                    node["ssh_host"],
                    node["control"]["ip"],
                    node["control"]["interface"],
                    hcas,
                    netdevs,
                ]
            )
        )
    for link in topology["links"]:
        print(
            "\t".join(
                [
                    "LINK",
                    str(link["ranks"][0]),
                    str(link["ranks"][1]),
                    str(len(link["rails"])),
                ]
            )
        )


def profile_fabric(topology: dict[str, Any], node_count: int) -> None:
    """Emit per-rank HCAs restricted to links inside an exact profile."""
    validate_manifest(topology, require_verified=True)
    if node_count < 1 or node_count > len(topology["nodes"]):
        fail(
            f"profile node count {node_count} is outside confirmed capacity "
            f"{len(topology['nodes'])}"
        )

    selected: dict[int, list[tuple[str, str]]] = {
        rank: [] for rank in range(node_count)
    }

    def add_endpoint(rank: int, endpoint: dict[str, Any]) -> None:
        item = (endpoint["hca"], endpoint["netdev"])
        if item not in selected[rank]:
            selected[rank].append(item)

    if node_count == 1:
        for rdma in topology["nodes"][0].get("rdma") or []:
            add_endpoint(0, rdma)
    else:
        for link in topology["links"]:
            a, b = link["ranks"]
            if a >= node_count or b >= node_count:
                continue
            for rail in link["rails"]:
                add_endpoint(a, rail["a"])
                add_endpoint(b, rail["b"])

    for rank in range(node_count):
        endpoints = selected[rank]
        if not endpoints:
            fail(f"rank {rank}: no RDMA HCA participates in selected profile")
        hcas = ",".join(hca for hca, _netdev in endpoints)
        netdevs = ",".join(dict.fromkeys(netdev for _hca, netdev in endpoints))
        print("\t".join([str(rank), hcas, netdevs]))



def ping_plan(topology: dict[str, Any]) -> None:
    validate_manifest(topology)
    nodes = topology["nodes"]
    for link in topology["links"]:
        a, b = link["ranks"]
        for rail in link["rails"]:
            print(
                "\t".join(
                    [
                        str(a),
                        nodes[a]["ssh_host"],
                        str(b),
                        rail["b"]["ip"],
                        rail["network"],
                    ]
                )
            )
            print(
                "\t".join(
                    [
                        str(b),
                        nodes[b]["ssh_host"],
                        str(a),
                        rail["a"]["ip"],
                        rail["network"],
                    ]
                )
            )


def mark_verified(document: dict[str, Any]) -> dict[str, Any]:
    topology = extract_topology(document)
    validate_manifest(topology)
    topology["validation"]["connectivity_verified"] = True
    topology["topology_id"] = topology_digest(topology)
    validate_manifest(topology, require_verified=True)
    if "topology" in document:
        document["topology"] = topology
        return document
    return topology


def atomic_write(topology: dict[str, Any], destination: str) -> None:
    validate_manifest(topology, require_verified=True)
    path = pathlib.Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(topology, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    assemble_parser = sub.add_parser("assemble")
    assemble_parser.add_argument("--existing")
    assemble_parser.add_argument("probes", nargs="+")

    render_parser = sub.add_parser("render")
    render_parser.add_argument("document")
    render_parser.add_argument("--skipped-ssh", type=int, default=0)

    render_save_parser = sub.add_parser("render-save")
    render_save_parser.add_argument("document")
    render_save_parser.add_argument("destination")

    for name in ("rows", "ping-plan", "mark-verified", "validate"):
        command = sub.add_parser(name)
        command.add_argument("document")
    profile_parser = sub.add_parser("profile-fabric")
    profile_parser.add_argument("document")
    profile_parser.add_argument("nodes", type=int)

    write_parser = sub.add_parser("write")
    write_parser.add_argument("document")
    write_parser.add_argument("destination")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "assemble":
            document = assemble(args.probes, args.existing)
            print(json.dumps(document, indent=2, sort_keys=True))
        elif args.command == "render":
            render(load_json(args.document), args.skipped_ssh)
        elif args.command == "render-save":
            render_save(load_json(args.document), args.destination)
        elif args.command == "rows":
            rows(extract_topology(load_json(args.document)))
        elif args.command == "profile-fabric":
            profile_fabric(
                extract_topology(load_json(args.document)), args.nodes
            )
        elif args.command == "ping-plan":
            ping_plan(extract_topology(load_json(args.document)))
        elif args.command == "mark-verified":
            print(
                json.dumps(
                    mark_verified(load_json(args.document)), indent=2, sort_keys=True
                )
            )
        elif args.command == "validate":
            validate_manifest(extract_topology(load_json(args.document)))
        elif args.command == "write":
            atomic_write(extract_topology(load_json(args.document)), args.destination)
        return 0
    except TopologyError as exc:
        print(f"topology: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
