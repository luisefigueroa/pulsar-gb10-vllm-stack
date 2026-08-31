#!/usr/bin/env python3
"""Experiment-only GB10 resource sampling and summary generation.

The rank-local ``collect`` command reads only procfs, cgroup v2 files, and the
managed container PID lookup.  It writes privacy-safe JSON lines containing a
generic rank label and numeric measurements.  Controller-side commands own a
private session directory and emit one closed, status-neutral resource
diagnostic for an explicit attempt window.

This tool does not launch or stop a model, evaluate a Validation Contract,
derive status, or participate in ordinary catalog serving.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any


SESSION_SCHEMA_VERSION = 1
SESSION_KIND = "pulsar-model-serving-experiment-monitor-session"
SAMPLE_SCHEMA_VERSION = 1
SAMPLE_KIND = "pulsar-model-serving-resource-sample"
SESSION_NAME = "session.json"
SAFE_PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
SAFE_RANK_RE = re.compile(r"^(?:single|0|[1-9][0-9]*)$")
SAFE_RAW_NAME_RE = re.compile(r"^rank-(?:single|0|[1-9][0-9]*)\.jsonl$")
SESSION_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
STOP = False


class ResourceMonitorError(ValueError):
    """Resource monitoring input or state is unsafe or invalid."""


def fail(message: str) -> None:
    raise ResourceMonitorError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_utc(value: str, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail(f"{label} must be RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        fail(f"{label} must be RFC3339 UTC")
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        fail(f"{label} must be RFC3339 UTC")
    return parsed


def canonical_decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return "0" if Decimal(text) == 0 else text


def safe_rank(value: str) -> str:
    if not isinstance(value, str) or SAFE_RANK_RE.fullmatch(value) is None:
        fail("rank label must be 'single' or a non-negative integer")
    return value


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def read_meminfo(path: Path = Path("/proc/meminfo")) -> dict[str, int] | None:
    text = _read_text(path)
    if text is None:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].endswith(":"):
            continue
        try:
            values[parts[0][:-1]] = int(parts[1]) * 1024
        except ValueError:
            continue
    required = {"MemAvailable", "SwapTotal", "SwapFree"}
    if not required.issubset(values):
        return None
    return {
        "mem_available_bytes": values["MemAvailable"],
        "swap_used_bytes": max(0, values["SwapTotal"] - values["SwapFree"]),
    }


def read_pressure_some_total(
    path: Path = Path("/proc/pressure/memory"),
) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    for line in text.splitlines():
        if not line.startswith("some "):
            continue
        for item in line.split()[1:]:
            if item.startswith("total="):
                try:
                    return int(item.split("=", 1)[1])
                except ValueError:
                    return None
    return None


def _read_nonnegative_int(path: Path) -> int | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        value = int(text.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def read_memory_events(path: Path) -> dict[str, int] | None:
    text = _read_text(path)
    if text is None:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            values[parts[0]] = int(parts[1])
        except ValueError:
            continue
    if "oom" not in values or "oom_kill" not in values:
        return None
    return {"oom": values["oom"], "oom_kill": values["oom_kill"]}


def cgroup_for_container(container_name: str) -> Path | None:
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Pid}}",
                container_name,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        pid = int(result.stdout.strip())
    except ValueError:
        return None
    if pid <= 0:
        return None
    cgroup_text = _read_text(Path("/proc") / str(pid) / "cgroup")
    if cgroup_text is None:
        return None
    relative = None
    for line in cgroup_text.splitlines():
        if line.startswith("0::"):
            relative = line[3:]
            break
    if not relative or not relative.startswith("/"):
        return None
    parts = PurePosixPath(relative).parts
    if ".." in parts:
        return None
    root = Path("/sys/fs/cgroup")
    target = root.joinpath(*parts[1:])
    try:
        resolved = target.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def read_cgroup(cgroup: Path | None) -> dict[str, int | None] | None:
    if cgroup is None:
        return None
    current = _read_nonnegative_int(cgroup / "memory.current")
    peak = _read_nonnegative_int(cgroup / "memory.peak")
    if current is None or peak is None:
        return None
    events = read_memory_events(cgroup / "memory.events")
    return {
        "memory_current_bytes": current,
        "memory_peak_bytes": peak,
        "memory_swap_current_bytes": _read_nonnegative_int(
            cgroup / "memory.swap.current"
        ),
        "oom": events["oom"] if events is not None else None,
        "oom_kill": events["oom_kill"] if events is not None else None,
    }


def make_sample(
    *,
    rank: str,
    cgroup: Path | None,
    meminfo_path: Path = Path("/proc/meminfo"),
    pressure_path: Path = Path("/proc/pressure/memory"),
) -> dict[str, Any]:
    return {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "kind": SAMPLE_KIND,
        "rank": safe_rank(rank),
        "sampled_at": utc_now(),
        "monotonic_ns": time.monotonic_ns(),
        "node": read_meminfo(meminfo_path),
        "node_memory_pressure_some_total_us": read_pressure_some_total(
            pressure_path
        ),
        "workload": read_cgroup(cgroup),
    }


def _handle_stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def collect(
    *, rank: str, container_name: str, interval: float, session_token: str
) -> int:
    safe_rank(rank)
    if SESSION_TOKEN_RE.fullmatch(session_token) is None:
        fail("session token is invalid")
    if not container_name or any(ch in container_name for ch in "\r\n\0"):
        fail("container name is invalid")
    if interval < 0.1 or interval > 60:
        fail("sample interval must be between 0.1 and 60 seconds")
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    cgroup: Path | None = None
    while not STOP:
        if cgroup is None or not cgroup.exists():
            cgroup = cgroup_for_container(container_name)
        sample = make_sample(rank=rank, cgroup=cgroup)
        print(json.dumps(sample, sort_keys=True, separators=(",", ":")), flush=True)
        deadline = time.monotonic() + interval
        while not STOP and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
    return 0


def _safe_state_dir(path: Path, *, repo_root: Path, create: bool) -> Path:
    base = repo_root.resolve()
    candidate = path if path.is_absolute() else base / path
    candidate = candidate.resolve(strict=False)
    forbidden = {Path("/"), base, base / ".git", base / "models"}
    if candidate in forbidden:
        fail("monitor state directory is unsafe")
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        relative = None
    if relative is not None:
        allowed = Path("experiments/model-onboarding/workflows")
        if relative == allowed or allowed not in relative.parents:
            fail(
                "in-repository monitor state must be below "
                "experiments/model-onboarding/workflows/"
            )
    if create:
        if candidate.exists():
            fail("monitor state directory already exists")
        candidate.mkdir(mode=0o700, parents=True)
    if not candidate.is_dir():
        fail("monitor state directory is unavailable")
    return candidate


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def init_session(
    *,
    state_dir: Path,
    repo_root: Path,
    profile: str,
    interval: Decimal,
    ranks: list[tuple[str, str]],
) -> tuple[Path, str]:
    if SAFE_PROFILE_RE.fullmatch(profile) is None:
        fail("profile is invalid")
    if interval < Decimal("0.1") or interval > Decimal(60):
        fail("sample interval must be between 0.1 and 60 seconds")
    if not ranks:
        fail("at least one rank is required")
    seen: set[str] = set()
    rows = []
    for rank, raw_name in ranks:
        rank = safe_rank(rank)
        if rank in seen:
            fail("rank labels must be unique")
        seen.add(rank)
        if SAFE_RAW_NAME_RE.fullmatch(raw_name) is None:
            fail("raw sample filename is invalid")
        rows.append({"rank": rank, "raw_file": raw_name})
    rows.sort(
        key=lambda item: (
            item["rank"] != "single",
            int(item["rank"]) if item["rank"].isdigit() else -1,
        )
    )
    target = _safe_state_dir(state_dir, repo_root=repo_root, create=True)
    session_token = secrets.token_hex(16)
    document = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "kind": SESSION_KIND,
        "profile": profile,
        "interval_seconds": canonical_decimal_text(interval),
        "started_at": utc_now(),
        "stopped_at": None,
        "state": "active",
        "session_token": session_token,
        "ranks": rows,
    }
    _atomic_json(target / SESSION_NAME, document)
    return target, session_token


def load_session(state_dir: Path, *, repo_root: Path) -> tuple[Path, dict[str, Any]]:
    target = _safe_state_dir(state_dir, repo_root=repo_root, create=False)
    try:
        document = json.loads((target / SESSION_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        fail("monitor session cannot be read")
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "kind",
        "profile",
        "interval_seconds",
        "started_at",
        "stopped_at",
        "state",
        "session_token",
        "ranks",
    }:
        fail("monitor session fields are invalid")
    if document.get("schema_version") != SESSION_SCHEMA_VERSION:
        fail("monitor session schema_version is unsupported")
    if document.get("kind") != SESSION_KIND:
        fail("monitor session kind is invalid")
    if document.get("state") not in {"active", "stopped"}:
        fail("monitor session state is invalid")
    if SESSION_TOKEN_RE.fullmatch(document.get("session_token", "")) is None:
        fail("monitor session token is invalid")
    parse_utc(document.get("started_at"), label="monitor session started_at")
    if document.get("stopped_at") is not None:
        parse_utc(document["stopped_at"], label="monitor session stopped_at")
    try:
        interval = Decimal(document.get("interval_seconds"))
    except Exception:
        fail("monitor session interval is invalid")
    if interval < Decimal("0.1") or interval > Decimal(60):
        fail("monitor session interval is invalid")
    ranks = document.get("ranks")
    if not isinstance(ranks, list) or not ranks:
        fail("monitor session ranks are invalid")
    seen = set()
    for row in ranks:
        if not isinstance(row, dict) or set(row) != {"rank", "raw_file"}:
            fail("monitor session rank entry is invalid")
        rank = safe_rank(row.get("rank"))
        if rank in seen or SAFE_RAW_NAME_RE.fullmatch(row.get("raw_file", "")) is None:
            fail("monitor session rank entry is invalid")
        seen.add(rank)
    return target, document


def stop_session(state_dir: Path, *, repo_root: Path) -> None:
    target, document = load_session(state_dir, repo_root=repo_root)
    if document["state"] == "stopped":
        return
    document["state"] = "stopped"
    document["stopped_at"] = utc_now()
    _atomic_json(target / SESSION_NAME, document)


def _load_samples(path: Path, *, rank: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    samples: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("kind") != SAMPLE_KIND:
            continue
        if item.get("schema_version") != SAMPLE_SCHEMA_VERSION or item.get("rank") != rank:
            continue
        try:
            parse_utc(item.get("sampled_at"), label="resource sample sampled_at")
        except ResourceMonitorError:
            continue
        samples.append(item)
    return samples


def _int_values(samples: list[dict[str, Any]], *keys: str) -> list[int]:
    values: list[int] = []
    for sample in samples:
        value: Any = sample
        for key in keys:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values.append(value)
    return values


def _delta(values: list[int]) -> int | None:
    return max(0, values[-1] - values[0]) if values else None


def summarize_session(
    *,
    state_dir: Path,
    repo_root: Path,
    started_at: str,
    ended_at: str,
    qualification_scope: str,
    result_json: Path,
) -> dict[str, Any]:
    target, session = load_session(state_dir, repo_root=repo_root)
    started = parse_utc(started_at, label="resource diagnostic started_at")
    ended = parse_utc(ended_at, label="resource diagnostic ended_at")
    if ended < started:
        fail("resource diagnostic ended_at precedes started_at")
    if qualification_scope not in {
        "model-qualification",
        "serving-integration",
        "release-promotion",
    }:
        fail("resource diagnostic qualification scope is unsupported")
    rank_results = []
    total_samples = 0
    observed_ranks = 0
    all_workload_observed = True
    for row in session["ranks"]:
        rank = row["rank"]
        all_samples = _load_samples(target / row["raw_file"], rank=rank)
        samples = [
            item
            for item in all_samples
            if started
            <= parse_utc(item["sampled_at"], label="resource sample sampled_at")
            <= ended
        ]
        node_samples = [item for item in samples if isinstance(item.get("node"), dict)]
        workload_samples = [
            item for item in samples if isinstance(item.get("workload"), dict)
        ]
        total_samples += len(samples)
        if node_samples:
            observed_ranks += 1
        if not workload_samples:
            all_workload_observed = False
        mem_available = _int_values(node_samples, "node", "mem_available_bytes")
        swap_used = _int_values(node_samples, "node", "swap_used_bytes")
        pressure = _int_values(
            node_samples, "node_memory_pressure_some_total_us"
        )
        current = _int_values(
            workload_samples, "workload", "memory_current_bytes"
        )
        peak = _int_values(workload_samples, "workload", "memory_peak_bytes")
        workload_swap = _int_values(
            workload_samples, "workload", "memory_swap_current_bytes"
        )
        oom = _int_values(workload_samples, "workload", "oom")
        oom_kill = _int_values(workload_samples, "workload", "oom_kill")
        status = (
            "complete"
            if node_samples and workload_samples
            else "pool-only"
            if node_samples
            else "unavailable"
        )
        rank_results.append(
            {
                "rank": rank,
                "collection_status": status,
                "sample_count": len(samples),
                "workload_sample_count": len(workload_samples),
                "mem_available_min_bytes": min(mem_available) if mem_available else None,
                "swap_used_max_bytes": max(swap_used) if swap_used else None,
                "node_memory_pressure_some_total_delta_us": _delta(pressure),
                "workload_memory_current_max_bytes": max(current) if current else None,
                "workload_memory_peak_start_bytes": peak[0] if peak else None,
                "workload_memory_peak_end_bytes": peak[-1] if peak else None,
                "workload_swap_current_max_bytes": (
                    max(workload_swap) if workload_swap else None
                ),
                "oom_delta": _delta(oom),
                "oom_kill_delta": _delta(oom_kill),
            }
        )
    expected = len(session["ranks"])
    if total_samples == 0:
        completion, reason = "incomplete", "no-samples"
    elif observed_ranks != expected:
        completion, reason = "incomplete", "missing-ranks"
    elif not all_workload_observed:
        completion, reason = "incomplete", "workload-unobserved"
    else:
        completion, reason = "complete", "completed"
    duration = Decimal(str((ended - started).total_seconds()))
    sys.path.insert(0, str(repo_root / "validate"))
    from validator_measurement import (  # type: ignore[import-not-found]
        build_resource_measurement,
        decimal_from_number,
        write_measurement,
    )

    measurement = build_resource_measurement(
        completion=completion,
        reason=reason,
        payload={
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": decimal_from_number(duration),
            "qualification_scope": qualification_scope,
            "sample_interval_seconds": session["interval_seconds"],
            "expected_rank_count": expected,
            "observed_rank_count": observed_ranks,
            "sample_count": total_samples,
            "ranks": rank_results,
        },
    )
    return write_measurement(result_json, measurement)


def parse_rank_spec(value: str) -> tuple[str, str]:
    if "=" not in value:
        fail("--rank requires RANK=rank-RANK.jsonl")
    rank, raw_name = value.split("=", 1)
    return safe_rank(rank), raw_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect_parser = sub.add_parser("collect", help="emit rank-local JSONL samples")
    collect_parser.add_argument("--rank-label", required=True)
    collect_parser.add_argument("--container-name", required=True)
    collect_parser.add_argument("--interval", type=float, default=1.0)
    collect_parser.add_argument("--session-token", required=True)

    init_parser = sub.add_parser("init-session", help="create private session state")
    init_parser.add_argument("--repo-root", type=Path, required=True)
    init_parser.add_argument("--state-dir", type=Path, required=True)
    init_parser.add_argument("--profile", required=True)
    init_parser.add_argument("--interval", default="1")
    init_parser.add_argument("--rank", action="append", default=[])

    check_parser = sub.add_parser("check-session", help="validate session state")
    check_parser.add_argument("--repo-root", type=Path, required=True)
    check_parser.add_argument("--state-dir", type=Path, required=True)

    stop_parser = sub.add_parser("stop-session", help="mark session stopped")
    stop_parser.add_argument("--repo-root", type=Path, required=True)
    stop_parser.add_argument("--state-dir", type=Path, required=True)

    token_parser = sub.add_parser("session-token", help=argparse.SUPPRESS)
    token_parser.add_argument("--repo-root", type=Path, required=True)
    token_parser.add_argument("--state-dir", type=Path, required=True)

    summary_parser = sub.add_parser(
        "summarize", help="write a closed per-attempt resource diagnostic"
    )
    summary_parser.add_argument("--repo-root", type=Path, required=True)
    summary_parser.add_argument("--state-dir", type=Path, required=True)
    summary_parser.add_argument("--started-at", required=True)
    summary_parser.add_argument("--ended-at", required=True)
    summary_parser.add_argument("--qualification-scope", required=True)
    summary_parser.add_argument("--result-json", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            return collect(
                rank=args.rank_label,
                container_name=args.container_name,
                interval=args.interval,
                session_token=args.session_token,
            )
        if args.command == "init-session":
            _target, session_token = init_session(
                state_dir=args.state_dir,
                repo_root=args.repo_root,
                profile=args.profile,
                interval=Decimal(args.interval),
                ranks=[parse_rank_spec(item) for item in args.rank],
            )
            print(session_token)
            return 0
        if args.command == "check-session":
            load_session(args.state_dir, repo_root=args.repo_root)
            return 0
        if args.command == "stop-session":
            stop_session(args.state_dir, repo_root=args.repo_root)
            return 0
        if args.command == "session-token":
            _target, session = load_session(
                args.state_dir, repo_root=args.repo_root
            )
            print(session["session_token"])
            return 0
        summarize_session(
            state_dir=args.state_dir,
            repo_root=args.repo_root,
            started_at=args.started_at,
            ended_at=args.ended_at,
            qualification_scope=args.qualification_scope,
            result_json=args.result_json,
        )
        return 0
    except (ResourceMonitorError, OSError, ValueError) as exc:
        print(f"resource monitor: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
