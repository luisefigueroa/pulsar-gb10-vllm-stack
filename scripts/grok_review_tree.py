#!/usr/bin/env python3
"""Prepare an optional tracked-files-only tree for an external Grok review.

Direct worktree review is the normal repository policy. This optional helper
provides a narrower disclosure mode by copying only tracked worktree files into
a temporary directory outside the repository and omitting gitignored secrets
and site identity.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from typing import Any

try:
    from scripts.terminal_format import TerminalWriter
except ModuleNotFoundError:
    from terminal_format import TerminalWriter  # type: ignore[no-redef]


FORBIDDEN_EXACT = frozenset(
    {
        ".env",
        ".cluster-topology.json",
        ".cluster-ssh-config",
        ".git",
    }
)
FORBIDDEN_PREFIXES = (
    ".git/",
    ".weight-fabric/",
    ".model-library/",
    "experiments/",
)
FORBIDDEN_DIR_NAMES = frozenset(
    {
        ".git",
        ".weight-fabric",
        ".model-library",
        "experiments",
    }
)


class GrokReviewTreeError(ValueError):
    """Preparing or verifying a sanitized review tree is unsafe."""


def fail(message: str) -> None:
    raise GrokReviewTreeError(message)


def path_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def normalize_relpath(value: str) -> str:
    rel = value.replace("\\", "/").lstrip("/")
    while rel.startswith("./"):
        rel = rel[2:]
    if not rel or rel == "." or rel.startswith("../"):
        fail(f"unsafe review path: {value}")
    return rel


def is_forbidden_relpath(rel: str) -> bool:
    rel = normalize_relpath(rel)
    if rel in FORBIDDEN_EXACT or rel in FORBIDDEN_DIR_NAMES:
        return True
    if any(rel.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return True
    parts = rel.split("/")
    if parts and parts[0] == "results" and "raw" in parts[1:]:
        return True
    return False


def run_git(repo_root: pathlib.Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        input=stdin,
        capture_output=True,
        check=False,
    )


def require_git_repo(repo_root: pathlib.Path) -> None:
    result = run_git(repo_root, "rev-parse", "--is-inside-work-tree")
    if result.returncode != 0 or result.stdout.strip() != b"true":
        fail(f"{repo_root} is not a git worktree")


def list_tracked_files(repo_root: pathlib.Path) -> list[str]:
    result = run_git(repo_root, "ls-files", "-z")
    if result.returncode != 0:
        detail = result.stderr.decode().strip() or "git ls-files failed"
        fail(detail)
    files = [normalize_relpath(part.decode()) for part in result.stdout.split(b"\0") if part]
    if not files:
        fail("repository has no tracked files to review")
    return files


def resolve_destination(repo_root: pathlib.Path, dest: str | None) -> pathlib.Path:
    if dest:
        path = pathlib.Path(dest).expanduser()
        if not path.is_absolute():
            path = pathlib.Path.cwd() / path
        path = path.resolve(strict=False)
    else:
        path = pathlib.Path(
            tempfile.mkdtemp(prefix="pulsar-grok-review-")
        ).resolve()
    if path.exists() and not path.is_dir():
        fail(f"review destination is not a directory: {path}")
    if path_within(path, repo_root) or path == repo_root.resolve():
        fail("review destination must be outside the live repository")
    if path.exists() and any(path.iterdir()):
        fail(f"review destination is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def copy_tracked_file(
    repo_root: pathlib.Path,
    dest_root: pathlib.Path,
    rel: str,
) -> bool:
    source = repo_root / rel
    if source.is_symlink():
        fail(f"refusing to copy symlink {rel}")
    if not source.exists():
        return False
    if not source.is_file():
        fail(f"tracked path is not a regular file: {rel}")
    target = dest_root / rel
    if is_forbidden_relpath(rel) or is_forbidden_relpath(
        target.relative_to(dest_root).as_posix()
    ):
        fail(f"refusing forbidden path {rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target, follow_symlinks=False)
    return True


def verify_tree(dest_root: pathlib.Path, expected: set[str]) -> None:
    found: set[str] = set()
    for current, dirnames, filenames in os.walk(dest_root, followlinks=False):
        current_path = pathlib.Path(current)
        rel_dir = (
            current_path.relative_to(dest_root).as_posix()
            if current_path != dest_root
            else ""
        )
        if rel_dir and is_forbidden_relpath(rel_dir):
            fail(f"sanitized tree contains forbidden path {rel_dir}")
        for name in dirnames + filenames:
            rel = name if not rel_dir else f"{rel_dir}/{name}"
            if is_forbidden_relpath(rel):
                fail(f"sanitized tree contains forbidden path {rel}")
            entry = current_path / name
            if entry.is_symlink():
                fail(f"sanitized tree contains symlink {rel}")
        for name in filenames:
            rel = name if not rel_dir else f"{rel_dir}/{name}"
            found.add(rel)
    unexpected = sorted(found - expected)
    missing = sorted(expected - found)
    if unexpected:
        fail("sanitized tree contains untracked path " + unexpected[0])
    if missing:
        fail("sanitized tree missing tracked path " + missing[0])


def prepare_review_tree(
    repo_root: str | pathlib.Path,
    dest: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    root = pathlib.Path(repo_root).resolve()
    if not root.is_dir():
        fail(f"repository root does not exist: {root}")
    require_git_repo(root)
    tracked = list_tracked_files(root)
    forbidden = [rel for rel in tracked if is_forbidden_relpath(rel)]
    if forbidden:
        fail("tracked tree includes forbidden path " + forbidden[0])
    destination = resolve_destination(
        root,
        None if dest is None else str(dest),
    )
    copied: list[str] = []
    try:
        for rel in tracked:
            if copy_tracked_file(root, destination, rel):
                copied.append(rel)
        if not copied:
            fail("no readable tracked files to copy")
        verify_tree(destination, set(copied))
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return {
        "destination": str(destination),
        "file_count": len(copied),
        "source": "tracked-worktree",
        "excluded": ["gitignored", "untracked", "git-dir", "site-local-state"],
    }


def render_human(
    report: dict[str, Any],
    stream=None,
    width: int | None = None,
) -> None:
    term = TerminalWriter(width=width, stream=stream or sys.stdout)
    term.emit("SANITIZED GROK REVIEW TREE")
    term.blank()
    term.field("Destination", report["destination"])
    term.field("Files", str(report["file_count"]))
    term.field("Source", "tracked worktree files only")
    term.field("Excluded", "gitignored, untracked, and site-local state")
    term.blank()
    term.emit("Point grok --cwd at the destination.")
    term.emit("Delete the tree after the review.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser(
        "prepare",
        help="copy tracked files into a sanitized review tree",
    )
    prepare.add_argument("--repo-root", required=True)
    prepare.add_argument("--dest")
    prepare.add_argument("--json", action="store_true")
    prepare.add_argument("--print-dest", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = prepare_review_tree(args.repo_root, args.dest)
    except GrokReviewTreeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    elif args.print_dest:
        print(report["destination"])
    else:
        render_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
