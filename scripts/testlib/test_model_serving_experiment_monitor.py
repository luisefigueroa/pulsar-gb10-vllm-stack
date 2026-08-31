#!/usr/bin/env python3
"""Deterministic contracts for experiment-only resource monitoring."""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_serving_experiment_monitor as monitor  # noqa: E402
from scripts import check_publishable_privacy  # noqa: E402


class ModelServingExperimentMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-resource-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

    def test_proc_and_cgroup_readers(self) -> None:
        meminfo = self.tmpdir / "meminfo"
        meminfo.write_text(
            "MemAvailable: 1024 kB\nSwapTotal: 512 kB\nSwapFree: 128 kB\n",
            encoding="utf-8",
        )
        pressure = self.tmpdir / "pressure"
        pressure.write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=42\n",
            encoding="utf-8",
        )
        cgroup = self.tmpdir / "cgroup"
        cgroup.mkdir()
        (cgroup / "memory.current").write_text("100\n", encoding="utf-8")
        (cgroup / "memory.peak").write_text("150\n", encoding="utf-8")
        (cgroup / "memory.swap.current").write_text("3\n", encoding="utf-8")
        (cgroup / "memory.events").write_text(
            "low 0\nhigh 0\nmax 0\noom 2\noom_kill 1\n",
            encoding="utf-8",
        )
        self.assertEqual(
            monitor.read_meminfo(meminfo),
            {"mem_available_bytes": 1024 * 1024, "swap_used_bytes": 384 * 1024},
        )
        self.assertEqual(monitor.read_pressure_some_total(pressure), 42)
        self.assertEqual(
            monitor.read_cgroup(cgroup),
            {
                "memory_current_bytes": 100,
                "memory_peak_bytes": 150,
                "memory_swap_current_bytes": 3,
                "oom": 2,
                "oom_kill": 1,
            },
        )

    def _sample(
        self,
        *,
        sampled_at: str,
        current: int,
        peak: int,
        oom: int,
        oom_kill: int,
        pressure: int,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": monitor.SAMPLE_KIND,
            "rank": "single",
            "sampled_at": sampled_at,
            "monotonic_ns": 1,
            "node": {
                "mem_available_bytes": 1000 - current,
                "swap_used_bytes": current // 10,
            },
            "node_memory_pressure_some_total_us": pressure,
            "workload": {
                "memory_current_bytes": current,
                "memory_peak_bytes": peak,
                "memory_swap_current_bytes": current // 20,
                "oom": oom,
                "oom_kill": oom_kill,
            },
        }

    def test_summary_captures_peaks_pressure_and_oom_without_site_identity(self) -> None:
        state = self.tmpdir / "state"
        monitor.init_session(
            state_dir=state,
            repo_root=REPO_ROOT,
            profile="example-model",
            interval=Decimal("1"),
            ranks=[("single", "rank-single.jsonl")],
        )
        rows = [
            self._sample(
                sampled_at="2026-08-14T12:00:00Z",
                current=100,
                peak=120,
                oom=0,
                oom_kill=0,
                pressure=10,
            ),
            self._sample(
                sampled_at="2026-08-14T12:00:01Z",
                current=300,
                peak=350,
                oom=1,
                oom_kill=1,
                pressure=25,
            ),
        ]
        (state / "rank-single.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8"
        )
        result = self.tmpdir / "resource.json"
        document = monitor.summarize_session(
            state_dir=state,
            repo_root=REPO_ROOT,
            started_at="2026-08-14T12:00:00Z",
            ended_at="2026-08-14T12:00:01Z",
            qualification_scope="model-qualification",
            result_json=result,
        )
        rank = document["observe-resources"]["ranks"][0]
        self.assertEqual(document["completion"], "complete")
        self.assertEqual(rank["workload_memory_current_max_bytes"], 300)
        self.assertEqual(rank["workload_memory_peak_end_bytes"], 350)
        self.assertEqual(rank["oom_kill_delta"], 1)
        self.assertEqual(rank["node_memory_pressure_some_total_delta_us"], 15)
        serialized = result.read_text(encoding="utf-8")
        self.assertNotIn("container", serialized)
        self.assertNotIn("hostname", serialized)
        self.assertNotIn(str(state), serialized)
        self.assertEqual(
            check_publishable_privacy.scan_bytes(
                "results/resource-fixture/benchmark-resources.json",
                result.read_bytes(),
            ),
            [],
        )

    def test_missing_workload_is_an_explicit_incomplete_diagnostic(self) -> None:
        state = self.tmpdir / "state"
        monitor.init_session(
            state_dir=state,
            repo_root=REPO_ROOT,
            profile="example-model",
            interval=Decimal("1"),
            ranks=[("single", "rank-single.jsonl")],
        )
        sample = self._sample(
            sampled_at="2026-08-14T12:00:00Z",
            current=100,
            peak=120,
            oom=0,
            oom_kill=0,
            pressure=10,
        )
        sample["workload"] = None
        (state / "rank-single.jsonl").write_text(
            json.dumps(sample) + "\n", encoding="utf-8"
        )
        document = monitor.summarize_session(
            state_dir=state,
            repo_root=REPO_ROOT,
            started_at="2026-08-14T12:00:00Z",
            ended_at="2026-08-14T12:00:00Z",
            qualification_scope="serving-integration",
            result_json=self.tmpdir / "resource.json",
        )
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "workload-unobserved")
        self.assertEqual(
            document["observe-resources"]["ranks"][0]["collection_status"],
            "pool-only",
        )

    def test_in_repo_state_is_confined_to_onboarding_workflows(self) -> None:
        with self.assertRaises(monitor.ResourceMonitorError):
            monitor.init_session(
                state_dir=REPO_ROOT / "results" / "monitor-state",
                repo_root=REPO_ROOT,
                profile="example-model",
                interval=Decimal("1"),
                ranks=[("single", "rank-single.jsonl")],
            )


if __name__ == "__main__":
    unittest.main()
