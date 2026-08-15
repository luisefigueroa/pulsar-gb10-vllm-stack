#!/usr/bin/env python3
"""Contracts for sanitized Grok review trees."""

from __future__ import annotations

import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import grok_review_tree as review  # noqa: E402


def git(repo: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def init_repo(root: pathlib.Path) -> pathlib.Path:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "review-tree@example.test")
    git(root, "config", "user.name", "Review Tree")
    (root / ".gitignore").write_text(
        "\n".join(
            [
                ".env",
                ".cluster-topology.json",
                ".cluster-ssh-config",
                ".weight-fabric/",
                ".model-library/",
                "experiments/",
                "results/**/raw/",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text("tracked readme\n", encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "fixture.conf").write_text("STATUS=tested\n", encoding="utf-8")
    (root / ".env.example").write_text("HF_TOKEN=\n", encoding="utf-8")
    git(root, "add", ".gitignore", "README.md", "models/fixture.conf", ".env.example")
    return root


def write_secrets(root: pathlib.Path) -> None:
    (root / ".env").write_text("HF_TOKEN=super-secret\n", encoding="utf-8")
    (root / ".cluster-topology.json").write_text(
        '{"hostname":"lab-node-a.example"}\n',
        encoding="utf-8",
    )
    (root / ".cluster-ssh-config").write_text("Host lab\n", encoding="utf-8")
    (root / ".weight-fabric").mkdir()
    (root / ".weight-fabric" / "state.json").write_text("{}\n", encoding="utf-8")
    (root / ".model-library").mkdir()
    (root / ".model-library" / "catalog.json").write_text("{}\n", encoding="utf-8")
    (root / "experiments").mkdir()
    (root / "experiments" / "notes.md").write_text("private\n", encoding="utf-8")
    raw = root / "results" / "run" / "raw"
    raw.mkdir(parents=True)
    (raw / "private.log").write_text("site-id\n", encoding="utf-8")
    (root / "untracked-notes.md").write_text("do not copy\n", encoding="utf-8")


class GrokReviewTreeContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name) / "repo"
        self.root.mkdir()
        init_repo(self.root)
        write_secrets(self.root)
        self.dest_parent = pathlib.Path(self.temporary.name) / "outside"
        self.dest_parent.mkdir()
        self.dest = self.dest_parent / "review-tree"

    def test_prepare_omits_gitignored_secrets_and_untracked_files(self) -> None:
        report = review.prepare_review_tree(self.root, self.dest)
        dest = pathlib.Path(report["destination"])
        self.assertEqual(dest, self.dest.resolve())
        self.assertEqual(report["source"], "tracked-worktree")
        self.assertGreaterEqual(report["file_count"], 4)
        self.assertEqual(
            (dest / "README.md").read_text(encoding="utf-8"),
            "tracked readme\n",
        )
        self.assertTrue((dest / "models" / "fixture.conf").is_file())
        self.assertTrue((dest / ".env.example").is_file())
        for rel in (
            ".env",
            ".cluster-topology.json",
            ".cluster-ssh-config",
            ".weight-fabric/state.json",
            ".model-library/catalog.json",
            "experiments/notes.md",
            "results/run/raw/private.log",
            "untracked-notes.md",
            ".git",
        ):
            self.assertFalse(
                (dest / rel).exists(),
                f"{rel} leaked into the sanitized tree",
            )

    def test_prepare_keeps_tracked_agent_docs_even_if_gitignored(self) -> None:
        (self.root / "AGENTS.md").write_text("review pointer\n", encoding="utf-8")
        gitignore = (self.root / ".gitignore").read_text(encoding="utf-8")
        (self.root / ".gitignore").write_text(
            "AGENTS.md\n" + gitignore,
            encoding="utf-8",
        )
        git(self.root, "add", "-f", "AGENTS.md", ".gitignore")
        report = review.prepare_review_tree(self.root, self.dest)
        dest = pathlib.Path(report["destination"])
        self.assertEqual(
            (dest / "AGENTS.md").read_text(encoding="utf-8"),
            "review pointer\n",
        )
        self.assertFalse((dest / ".env").exists())

    def test_prepare_copies_dirty_tracked_content(self) -> None:
        (self.root / "README.md").write_text("dirty tracked\n", encoding="utf-8")
        report = review.prepare_review_tree(self.root, self.dest)
        dest = pathlib.Path(report["destination"])
        self.assertEqual(
            (dest / "README.md").read_text(encoding="utf-8"),
            "dirty tracked\n",
        )

    def test_prepare_refuses_destination_inside_repo(self) -> None:
        inside = self.root / "tmp-review"
        with self.assertRaisesRegex(review.GrokReviewTreeError, "outside"):
            review.prepare_review_tree(self.root, inside)

    def test_prepare_refuses_tracked_forbidden_path(self) -> None:
        git(self.root, "add", "-f", ".env")
        with self.assertRaisesRegex(review.GrokReviewTreeError, "forbidden path"):
            review.prepare_review_tree(self.root, self.dest)
        self.assertFalse(self.dest.exists())

    def test_prepare_refuses_symlink(self) -> None:
        link = self.root / "models" / "link.conf"
        link.symlink_to(self.root / ".env")
        git(self.root, "add", "-f", "models/link.conf")
        with self.assertRaisesRegex(review.GrokReviewTreeError, "symlink"):
            review.prepare_review_tree(self.root, self.dest)
        self.assertFalse(self.dest.exists())

    def test_forbidden_relpath_contract(self) -> None:
        self.assertTrue(review.is_forbidden_relpath(".env"))
        self.assertTrue(review.is_forbidden_relpath(".cluster-topology.json"))
        self.assertTrue(review.is_forbidden_relpath(".weight-fabric/state.json"))
        self.assertTrue(review.is_forbidden_relpath("results/tag/raw/out.log"))
        self.assertFalse(review.is_forbidden_relpath(".env.example"))
        self.assertFalse(review.is_forbidden_relpath("models/fixture.conf"))

    def test_human_output_honors_narrow_width(self) -> None:
        report = {
            "destination": "/tmp/pulsar-grok-review-narrow",
            "file_count": 12,
        }
        buf = io.StringIO()
        review.render_human(report, stream=buf, width=40)
        lines = buf.getvalue().splitlines()
        self.assertTrue(lines)
        self.assertLessEqual(max(len(line) for line in lines), 40)
        self.assertEqual(lines[0], "SANITIZED GROK REVIEW TREE")

    def test_cli_print_dest_and_json(self) -> None:
        dest = self.dest_parent / "cli-tree"
        printed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "grok_review_tree.py"),
                "prepare",
                "--repo-root",
                str(self.root),
                "--dest",
                str(dest),
                "--print-dest",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(printed.stdout.strip(), str(dest.resolve()))
        payload = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "grok_review_tree.py"),
                "prepare",
                "--repo-root",
                str(self.root),
                "--dest",
                str(self.dest_parent / "json-tree"),
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(payload.stdout)
        self.assertEqual(data["source"], "tracked-worktree")
        self.assertGreaterEqual(data["file_count"], 4)
        self.assertFalse((pathlib.Path(data["destination"]) / ".env").exists())


if __name__ == "__main__":
    unittest.main()
