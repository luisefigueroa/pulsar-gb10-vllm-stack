#!/usr/bin/env python3
"""Contracts for the closed GSM8K evaluator."""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "validate"))

import gsm8k_eval  # noqa: E402


class Gsm8kEvalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-gsm8k-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_number_normalization(self) -> None:
        self.assertEqual(gsm8k_eval.normalize_number("work #### 1,234", gold=True), "1234")
        self.assertEqual(gsm8k_eval.normalize_number("answer is -2.50", gold=False), "-2.5")
        self.assertIsNone(gsm8k_eval.normalize_number("no number", gold=False))

    def test_selection_is_hash_stable(self) -> None:
        rows = [
            {"question": "b", "answer": "#### 2"},
            {"question": "a", "answer": "#### 1"},
            {"question": "c", "answer": "#### 3"},
        ]
        first = gsm8k_eval.select_rows(rows, 2)
        second = gsm8k_eval.select_rows(list(reversed(rows)), 2)
        self.assertEqual(first, second)

    def test_complete_run_writes_closed_measurement(self) -> None:
        dataset = self.root / "test.json"
        dataset.write_text(
            json.dumps(
                [
                    {"question": "one plus one", "answer": "steps #### 2"},
                    {"question": "three plus four", "answer": "steps #### 7"},
                ]
            ),
            encoding="utf-8",
        )
        output = self.root / "measurement.json"

        def fake_complete(_url, _model, question, _max_tokens, _mode, _key):
            return "The final answer is 2" if question == "one plus one" else "7"

        with mock.patch.object(gsm8k_eval, "complete", side_effect=fake_complete):
            code = gsm8k_eval.main(
                [
                    "--model",
                    "fixture",
                    "--dataset",
                    str(dataset),
                    "--dataset-id",
                    "openai/gsm8k",
                    "--dataset-revision",
                    "a" * 40,
                    "--sample-size",
                    "2",
                    "--result-json",
                    str(output),
                ]
            )
        self.assertEqual(code, 0)
        document = json.loads(output.read_text(encoding="utf-8"))
        payload = document["evaluate-gsm8k"]
        self.assertEqual(payload["correct_count"], 2)
        self.assertEqual(payload["accuracy"], "1")
        self.assertEqual(payload["request_error_count"], 0)
        self.assertNotIn(str(dataset), json.dumps(document))

    def test_parquet_loader_failure_writes_closed_incomplete_measurement(self) -> None:
        dataset = self.root / "test.parquet"
        dataset.write_bytes(b"not parquet")
        output = self.root / "measurement.json"
        with mock.patch.object(
            gsm8k_eval,
            "_load_rows",
            side_effect=RuntimeError("parquet columns are invalid"),
        ):
            code = gsm8k_eval.main(
                [
                    "--model",
                    "fixture",
                    "--dataset",
                    str(dataset),
                    "--dataset-id",
                    "openai/gsm8k",
                    "--dataset-revision",
                    "a" * 40,
                    "--sample-size",
                    "2",
                    "--result-json",
                    str(output),
                ]
            )
        self.assertEqual(code, 2)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "dataset-invalid")

    def test_digest_reader_failure_writes_closed_incomplete_measurement(self) -> None:
        dataset = self.root / "test.json"
        dataset.write_text("[]", encoding="utf-8")
        output = self.root / "measurement.json"
        with mock.patch.object(
            gsm8k_eval,
            "file_digest",
            side_effect=RuntimeError("digest source unreadable"),
        ):
            code = gsm8k_eval.main(
                [
                    "--model",
                    "fixture",
                    "--dataset",
                    str(dataset),
                    "--dataset-id",
                    "openai/gsm8k",
                    "--dataset-revision",
                    "a" * 40,
                    "--sample-size",
                    "2",
                    "--result-json",
                    str(output),
                ]
            )
        self.assertEqual(code, 2)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "dataset-invalid")
        self.assertIsNone(document["evaluate-gsm8k"]["dataset_file_sha256"])


if __name__ == "__main__":
    unittest.main()
