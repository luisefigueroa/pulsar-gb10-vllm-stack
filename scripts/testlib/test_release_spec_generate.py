#!/usr/bin/env python3
"""Contracts for ADR 0017 from-profile spec generation."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from release_spec import pretty_json_bytes, spec_id_for, verify_spec  # noqa: E402
from scripts import release_spec_generate as generate  # noqa: E402
from scripts.testlib import release_spec_generate_fixture as receipt_fixture  # noqa: E402

CLI = ROOT / "scripts" / "release-spec.sh"
FIXTURES = ROOT / "scripts" / "testdata" / "release-spec-from-profile"
RECEIPTS = FIXTURES / "receipts"
EXPECTED_GAPS = FIXTURES / "expected-gaps"
PINNED_DIGEST = "c" * 64
PINNED_IMAGE = f"vllm/vllm-openai@sha256:{PINNED_DIGEST}"
STACK_VERSION = "0.0.0-test"
PLATFORM_ID = "dgx-spark-gb10"
NANO = "nemotron-3-nano-30b-nvfp4"
SUPER = "nemotron-3-super-120b-nvfp4"
TWO_NODE = "qwen3.8-27b-fp8-2node"
SUPER_JSON = (
    '{"method":"mtp","num_speculative_tokens":1,"moe_backend":"triton"}'
)


def profile_confs() -> list[str]:
    return sorted(path.stem for path in (ROOT / "models").glob("*.conf"))


def model_id_for(profile: str) -> str:
    for raw in (ROOT / "models" / f"{profile}.conf").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("MODEL="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise AssertionError(f"{profile}: MODEL missing")


def receipt_for(model_id: str) -> pathlib.Path:
    return RECEIPTS / f"{model_id.replace('/', '__')}.json"


def expected_gap_path(profile: str, *, spec_decode: bool = False) -> pathlib.Path:
    suffix = "--spec-decode" if spec_decode else ""
    return EXPECTED_GAPS / f"{profile}{suffix}.json"


def bash_env(*, pinned: bool = True) -> dict[str, str]:
    env = os.environ.copy()
    if pinned:
        env["VLLM_IMAGE_MAINLINE"] = PINNED_IMAGE
    else:
        env["VLLM_IMAGE_MAINLINE"] = "vllm/vllm-openai:v0.26.0"
    return env


def run_cli(
    args: list[str],
    *,
    pinned: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CLI), *args],
        cwd=ROOT,
        env=bash_env(pinned=pinned),
        check=False,
        capture_output=True,
        text=True,
    )


def from_profile_args(
    profile: str,
    *,
    tmp: pathlib.Path,
    spec_decode: bool = False,
    receipt: pathlib.Path | None = None,
    out_name: str = "spec.json",
    gap_name: str = "gaps.json",
) -> tuple[list[str], pathlib.Path, pathlib.Path]:
    model_id = model_id_for(profile)
    receipt_path = receipt if receipt is not None else receipt_for(model_id)
    out = tmp / out_name
    gap = tmp / gap_name
    args = [
        "from-profile",
        profile,
        "--receipt",
        str(receipt_path),
        "--stack-version",
        STACK_VERSION,
        "--out",
        str(out),
        "--gap-report",
        str(gap),
    ]
    if spec_decode:
        args.append("--spec-decode")
    return args, out, gap


def nano_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "profile": NANO,
        "model_id": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4",
        "image": PINNED_IMAGE,
        "nodes": 1,
        "gpu_mem_util": "0.80",
        "engine_args": [
            "--max-model-len",
            "131072",
            "--max-num-seqs",
            "16",
            "--moe-backend",
            "marlin",
        ],
        "container_env": ["VLLM_MARLIN_USE_ATOMIC_ADD=1"],
        "spec_decode_args": [],
        "platform_id": PLATFORM_ID,
        "stack_version": STACK_VERSION,
        "spec_decode": False,
        "receipt_path": receipt_for(
            "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
        ),
        "repo_root": ROOT,
    }
    values.update(overrides)
    return values


class ReleaseSpecGenerateTests(unittest.TestCase):
    def test_bash_from_profile_every_conf(self) -> None:
        profiles = profile_confs()
        self.assertGreaterEqual(len(profiles), 7)
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            for profile in profiles:
                args, out, gap = from_profile_args(profile, tmp=tmp / profile)
                (tmp / profile).mkdir()
                result = run_cli(args)
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"{profile}: {result.stderr}",
                )
                spec = verify_spec(json.loads(out.read_text(encoding="utf-8")))
                self.assertEqual(spec["state"], "measured")
                self.assertEqual(spec["review"], {})
                self.assertEqual(spec["measurements"], [])
                self.assertEqual(spec["baselines"], [])
                self.assertEqual(spec["evidence"], [])
                self.assertEqual(spec["spec_id"], spec_id_for(spec["identity"]))
                self.assertEqual(
                    gap.read_bytes(),
                    expected_gap_path(profile).read_bytes(),
                    msg=profile,
                )
                self.assertEqual(
                    spec["identity"]["snapshot_revision"],
                    "a" * 40,
                )

    def test_one_node_geometry_and_gpu_mem_token(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            args, out, _gap = from_profile_args(NANO, tmp=tmp)
            result = run_cli(args)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            spec = verify_spec(json.loads(out.read_text(encoding="utf-8")))
        engine = spec["identity"]["engine_args"]
        self.assertNotIn("--tensor-parallel-size", engine)
        self.assertNotIn("-tp", engine)
        self.assertEqual(engine[-2:], ["--gpu-memory-utilization", "0.80"])
        self.assertEqual(
            spec["identity"]["geometry"],
            {
                "fabric": "local",
                "nodes": 1,
                "platform_id": PLATFORM_ID,
                "pp": 1,
                "tp": 1,
            },
        )
        self.assertEqual(
            spec["launch_contract"]["argv"][-4:],
            [
                "--tensor-parallel-size",
                "1",
                "--pipeline-parallel-size",
                "1",
            ],
        )
        self.assertEqual(spec["launch_contract"]["stack_version"], STACK_VERSION)
        self.assertEqual(
            spec["identity"]["image"]["digest"],
            f"sha256:{PINNED_DIGEST}",
        )

    def test_two_node_strips_tp_keeps_mp_and_roce_fabric(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            args, out, _gap = from_profile_args(TWO_NODE, tmp=tmp)
            result = run_cli(args)
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            spec = verify_spec(json.loads(out.read_text(encoding="utf-8")))
        engine = spec["identity"]["engine_args"]
        self.assertNotIn("--tensor-parallel-size", engine)
        self.assertIn("--distributed-executor-backend", engine)
        self.assertEqual(engine[engine.index("--distributed-executor-backend") + 1], "mp")
        self.assertEqual(
            spec["identity"]["geometry"],
            {
                "fabric": "roce-v2",
                "nodes": 2,
                "platform_id": PLATFORM_ID,
                "pp": 1,
                "tp": 2,
            },
        )
        self.assertEqual(
            spec["launch_contract"]["argv"][-4:],
            [
                "--tensor-parallel-size",
                "2",
                "--pipeline-parallel-size",
                "1",
            ],
        )
        overlay = {
            "--model",
            "--served-model-name",
            "--host",
            "--port",
            "--api-key",
            "--download-dir",
            "--nnodes",
            "--master-addr",
            "--node-rank",
        }
        self.assertTrue(overlay.isdisjoint(spec["launch_contract"]["argv"]))

    def test_super_spec_decode_is_a_second_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            off_args, off_out, off_gap = from_profile_args(
                SUPER, tmp=tmp, out_name="off.json", gap_name="off-gaps.json"
            )
            on_args, on_out, on_gap = from_profile_args(
                SUPER,
                tmp=tmp,
                spec_decode=True,
                out_name="on.json",
                gap_name="on-gaps.json",
            )
            off = run_cli(off_args)
            on = run_cli(on_args)
            self.assertEqual(off.returncode, 0, msg=off.stderr)
            self.assertEqual(on.returncode, 0, msg=on.stderr)
            off_spec = verify_spec(json.loads(off_out.read_text(encoding="utf-8")))
            on_spec = verify_spec(json.loads(on_out.read_text(encoding="utf-8")))
            off_gap_bytes = off_gap.read_bytes()
            on_gap_bytes = on_gap.read_bytes()
        self.assertNotIn("--speculative-config", off_spec["identity"]["engine_args"])
        self.assertNotIn(SUPER_JSON, off_spec["identity"]["engine_args"])
        engine = on_spec["identity"]["engine_args"]
        self.assertIn("--speculative-config", engine)
        self.assertIn(SUPER_JSON, engine)
        self.assertEqual(engine.count(SUPER_JSON), 1)
        self.assertNotEqual(off_spec["spec_id"], on_spec["spec_id"])
        self.assertEqual(off_gap_bytes, expected_gap_path(SUPER).read_bytes())
        self.assertEqual(
            on_gap_bytes,
            expected_gap_path(SUPER, spec_decode=True).read_bytes(),
        )

    def test_nano_spec_decode_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            args, out, gap = from_profile_args(NANO, tmp=tmp, spec_decode=True)
            result = run_cli(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())
            report = json.loads(gap.read_text(encoding="utf-8"))
        self.assertFalse(report["generated"])
        self.assertIsNone(report["spec_id"])
        self.assertTrue(
            any(
                item["class"] == "blocking"
                and "SPEC_DECODE_ARGS" in item["reason"]
                for item in report["gaps"]
            )
        )

    def test_missing_receipt_writes_blocking_gap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            missing = tmp / "missing-receipt.json"
            args, out, gap = from_profile_args(NANO, tmp=tmp, receipt=missing)
            result = run_cli(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(out.exists())
            report = json.loads(gap.read_text(encoding="utf-8"))
        self.assertFalse(report["generated"])
        self.assertTrue(
            any(
                item["class"] == "blocking" and item["field"] == "snapshot_manifest"
                for item in report["gaps"]
            )
        )

    def test_unpinned_image_fails(self) -> None:
        spec, report = generate.build_spec_from_profile(
            **nano_kwargs(image="vllm/vllm-openai:v0.26.0")  # type: ignore[arg-type]
        )
        self.assertIsNone(spec)
        self.assertFalse(report["generated"])
        self.assertTrue(
            any(
                item["class"] == "blocking" and item["field"] == "image"
                for item in report["gaps"]
            )
        )
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            args, out, gap = from_profile_args(NANO, tmp=tmp)
            result = run_cli(args, pinned=False)
            if result.returncode == 0:
                # Site .env may pin VLLM_IMAGE_MAINLINE; Python path above is the contract.
                return
            self.assertFalse(out.exists())
            bash_report = json.loads(gap.read_text(encoding="utf-8"))
            self.assertFalse(bash_report["generated"])

    def test_committed_receipts_are_valid_and_regenerable(self) -> None:
        mapping = receipt_fixture.profile_model_ids()
        self.assertEqual(
            sorted(path.name for path in RECEIPTS.glob("*.json")),
            sorted(receipt_fixture.receipt_path(m).name for m in mapping),
        )
        for model_id, profile in mapping.items():
            rebuilt = receipt_fixture.build_fixture_receipt(model_id, profile=profile)
            self.assertEqual(
                receipt_fixture.receipt_path(model_id).read_bytes(),
                receipt_fixture.receipt_bytes(rebuilt),
                model_id,
            )

    def test_bare_file_list_is_not_a_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bare = pathlib.Path(raw) / "bare.json"
            valid = json.loads(receipt_for(model_id_for(NANO)).read_text(encoding="utf-8"))
            bare.write_text(
                json.dumps(
                    {
                        "model_id": valid["model_id"],
                        "snapshot_revision": valid["snapshot_revision"],
                        "observed_manifest": valid["observed_manifest"],
                    }
                ),
                encoding="utf-8",
            )
            spec, report = generate.build_spec_from_profile(
                **nano_kwargs(receipt_path=bare)  # type: ignore[arg-type]
            )
        self.assertIsNone(spec)
        self.assertTrue(
            any(
                item["class"] == "blocking"
                and item["field"] == "snapshot_manifest"
                and "failed validation" in item["reason"]
                for item in report["gaps"]
            )
        )
        tampered = json.loads(receipt_for(model_id_for(NANO)).read_text(encoding="utf-8"))
        tampered["observed_manifest"]["files"][0]["sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as raw:
            path = pathlib.Path(raw) / "tampered.json"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            spec, report = generate.build_spec_from_profile(
                **nano_kwargs(receipt_path=path)  # type: ignore[arg-type]
            )
        self.assertIsNone(spec)
        self.assertFalse(report["generated"])

    def test_trusted_output_directories_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            for target in (
                ROOT / "releases" / "draft.json",
                ROOT / "models" / "draft.json",
                ROOT / "models" / "model-serving-releases" / "x" / "draft.json",
            ):
                with self.assertRaisesRegex(generate.ReleaseSpecGenerateError, "trusted"):
                    generate.validate_output_locations(
                        repo_root=ROOT, out=target, gap_report=tmp / "gap.json"
                    )
                with self.assertRaisesRegex(generate.ReleaseSpecGenerateError, "trusted"):
                    generate.validate_output_locations(
                        repo_root=ROOT, out=tmp / "spec.json", gap_report=target
                    )
                self.assertFalse(target.exists())
            with self.assertRaisesRegex(generate.ReleaseSpecGenerateError, "different files"):
                generate.validate_output_locations(
                    repo_root=ROOT, out=tmp / "same.json", gap_report=tmp / "./same.json"
                )
            generate.validate_output_locations(
                repo_root=ROOT, out=tmp / "spec.json", gap_report=tmp / "gap.json"
            )
            args, out, gap = from_profile_args(NANO, tmp=tmp)
            aliased = [item if item != str(gap) else str(out) for item in args]
            result = run_cli(aliased)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("different files", result.stderr)
            self.assertFalse(out.exists())

    def test_receipt_model_mismatch(self) -> None:
        spec, report = generate.build_spec_from_profile(
            **nano_kwargs(receipt_path=receipt_for("Qwen/Qwen3.8-27B-FP8"))  # type: ignore[arg-type]
        )
        self.assertIsNone(spec)
        self.assertTrue(
            any(
                item["class"] == "blocking"
                and item["field"] == "model_id"
                and "differs from the profile MODEL" in item["reason"]
                for item in report["gaps"]
            )
        )

    def test_tp_times_pp_must_equal_nodes(self) -> None:
        spec, report = generate.build_spec_from_profile(
            **nano_kwargs(
                engine_args=[
                    "--max-model-len",
                    "131072",
                    "--tensor-parallel-size",
                    "2",
                ]
            )  # type: ignore[arg-type]
        )
        self.assertIsNone(spec)
        self.assertTrue(
            any(
                item["class"] == "blocking"
                and item["field"] == "geometry"
                and item["reason"] == "tp * pp must equal nodes"
                for item in report["gaps"]
            )
        )

    def test_port_in_engine_args_is_blocking(self) -> None:
        spec, report = generate.build_spec_from_profile(
            **nano_kwargs(
                engine_args=["--max-model-len", "131072", "--port", "8000"]
            )  # type: ignore[arg-type]
        )
        self.assertIsNone(spec)
        matching = [
            item
            for item in report["gaps"]
            if item["class"] == "blocking" and "--port" in item["reason"]
        ]
        self.assertEqual(len(matching), 1)

    def test_existing_out_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            args, out, gap = from_profile_args(NANO, tmp=tmp)
            out.write_text("keep\n", encoding="utf-8")
            result = run_cli(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(out.read_text(encoding="utf-8"), "keep\n")
            report = json.loads(gap.read_text(encoding="utf-8"))
        self.assertFalse(report["generated"])
        self.assertTrue(
            any(
                item["class"] == "blocking" and item["field"] == "out"
                for item in report["gaps"]
            )
        )

    def test_two_runs_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            first_args, first_out, first_gap = from_profile_args(
                NANO, tmp=tmp, out_name="a.json", gap_name="a-gaps.json"
            )
            second_args, second_out, second_gap = from_profile_args(
                NANO, tmp=tmp, out_name="b.json", gap_name="b-gaps.json"
            )
            first = run_cli(first_args)
            second = run_cli(second_args)
            self.assertEqual(first.returncode, 0, msg=first.stderr)
            self.assertEqual(second.returncode, 0, msg=second.stderr)
            self.assertEqual(first_out.read_bytes(), second_out.read_bytes())
            self.assertEqual(first_gap.read_bytes(), second_gap.read_bytes())

    def test_usage_errors_exit_2(self) -> None:
        unknown = run_cli(["nope"])
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("unknown release-spec command", unknown.stderr)
        missing_profile = run_cli(
            ["from-profile", "--receipt", "/tmp/x", "--stack-version", STACK_VERSION]
        )
        self.assertEqual(missing_profile.returncode, 2)
        missing_receipt = run_cli(
            ["from-profile", NANO, "--stack-version", STACK_VERSION]
        )
        self.assertEqual(missing_receipt.returncode, 2)
        missing_stack = run_cli(
            ["from-profile", NANO, "--receipt", str(receipt_for(model_id_for(NANO)))]
        )
        self.assertEqual(missing_stack.returncode, 2)

    def test_do_not_use_and_diagnostic_profiles_still_generate(self) -> None:
        for profile in ("qwen3.6-27b-fp8-2node", "qwen3-1.7b-2node"):
            with tempfile.TemporaryDirectory() as raw:
                tmp = pathlib.Path(raw)
                args, out, gap = from_profile_args(profile, tmp=tmp)
                result = run_cli(args)
                self.assertEqual(result.returncode, 0, msg=f"{profile}: {result.stderr}")
                spec = verify_spec(json.loads(out.read_text(encoding="utf-8")))
                self.assertEqual(spec["state"], "measured")
                self.assertTrue(json.loads(gap.read_text(encoding="utf-8"))["generated"])

    def test_gap_report_sort_and_pretty_encoding(self) -> None:
        spec, report = generate.build_spec_from_profile(**nano_kwargs())  # type: ignore[arg-type]
        self.assertIsNotNone(spec)
        classes = [item["class"] for item in report["gaps"]]
        self.assertEqual(classes, sorted(classes))
        encoded = pretty_json_bytes(report)
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(encoded, pretty_json_bytes(json.loads(encoded)))


if __name__ == "__main__":
    unittest.main()
