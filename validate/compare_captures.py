#!/usr/bin/env python3
"""Compare two greedy captures (from greedy_capture.py).

  validate/compare_captures.py A.json B.json [--label-a X --label-b Y]
      [--result-json PATH]

Reports per-prompt and aggregate:
  - exact match (full text identical)
  - token match rate up to first divergence (prefix match / max length)
  - max |logprob delta| over the common matched prefix

Gate guidance (docs/VALIDATION.md): same config run-to-run must be EXACT.
Different kernels/parallelism gate on prefix-match >= 0.90 mean and
matched-prefix logprob deltas that stay small (< 0.5); systematic early
divergence on many prompts is a red flag regardless of the numbers.

Optional ``--result-json`` writes a closed measurement document computed
from the parsed capture records. That document is not a validation
disposition and does not change the human output or exit codes.
"""
from __future__ import annotations

import argparse
import math
import sys
from decimal import Decimal
from typing import Any

from validator_measurement import (
    ValidatorMeasurementError,
    ValidatorMeasurementMissing,
    build_compare_measurement,
    decimal_from_number,
    parse_strict_json,
    read_stable_bytes,
    sha256_bytes,
    write_measurement,
)


class CaptureError(ValueError):
    def __init__(self, message: str, digest: str | None = None):
        super().__init__(message)
        self.digest = digest


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return True


def load_capture(path, label):
    digest = None
    try:
        raw = read_stable_bytes(path, label=label)
    except ValidatorMeasurementMissing as exc:
        raise CaptureError(f"{label}: cannot read capture: {exc}") from exc
    except ValidatorMeasurementError as exc:
        raise CaptureError(f"{label}: cannot read capture: {exc}") from exc
    except OSError as exc:
        raise CaptureError(f"{label}: cannot read capture: {exc}") from exc
    digest = sha256_bytes(raw)
    try:
        data = parse_strict_json(raw, label=label)
    except ValidatorMeasurementError as exc:
        raise CaptureError(f"{label}: cannot read capture: {exc}", digest=digest) from exc
    if not isinstance(data, list) or not data:
        raise CaptureError(f"{label}: capture must be a non-empty JSON list", digest=digest)
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise CaptureError(f"{label}[{i}]: entry must be an object", digest=digest)
        for field, kind in (
            ("prompt", str),
            ("text", str),
            ("tokens", list),
            ("logprobs", list),
        ):
            if not isinstance(row.get(field), kind):
                raise CaptureError(
                    f"{label}[{i}].{field}: expected {kind.__name__}",
                    digest=digest,
                )
        if len(row["logprobs"]) < len(row["tokens"]):
            raise CaptureError(
                f"{label}[{i}]: logprobs shorter than tokens "
                f"({len(row['logprobs'])} < {len(row['tokens'])})",
                digest=digest,
            )
        if any(
            value is not None and not _is_finite_number(value)
            for value in row["logprobs"]
        ):
            raise CaptureError(
                f"{label}[{i}]: logprobs must be finite numeric or null",
                digest=digest,
            )
    return data, digest


def prefix_match(a_toks, b_toks):
    n = min(len(a_toks), len(b_toks))
    if n == 0:
        return 0, 1.0 if not a_toks and not b_toks else 0.0
    i = 0
    while i < n and a_toks[i] == b_toks[i]:
        i += 1
    return i, i / max(len(a_toks), len(b_toks))


def prefix_rate(a_toks, b_toks) -> Decimal:
    if not a_toks and not b_toks:
        return Decimal(1)
    denom = max(len(a_toks), len(b_toks))
    if denom == 0:
        return Decimal(0)
    matched, _ignored = prefix_match(a_toks, b_toks)
    return Decimal(matched) / Decimal(denom)


def incomplete_compare_document(
    *,
    reason: str,
    digest_a: str | None,
    digest_b: str | None,
) -> dict[str, Any]:
    return build_compare_measurement(
        completion="incomplete",
        reason=reason,
        source_digests={"a": digest_a, "b": digest_b},
    )


def complete_compare_document(
    *,
    sample_count: int,
    identical_record_count: int,
    exact_text_count: int,
    rates: list[Decimal],
    lp_deltas: list[Decimal],
    hard_disagreement_count: int,
    digest_a: str,
    digest_b: str,
) -> dict[str, Any]:
    if identical_record_count == sample_count:
        verdict = "identical"
    elif hard_disagreement_count == 0 and max(lp_deltas) < Decimal("0.5"):
        verdict = "fp-equivalent"
    else:
        verdict = "divergent"
    payload = {
        "sample_count": sample_count,
        "identical_record_count": identical_record_count,
        "exact_text_count": exact_text_count,
        "mean_prefix_match": decimal_from_number(sum(rates) / Decimal(sample_count)),
        "min_prefix_match": decimal_from_number(min(rates)),
        "max_matched_prefix_logprob_delta": decimal_from_number(max(lp_deltas)),
        "hard_disagreement_count": hard_disagreement_count,
        "diagnostic_verdict": verdict,
        "source_digests": {"a": digest_a, "b": digest_b},
    }
    return build_compare_measurement(
        completion="complete",
        reason="completed",
        payload=payload,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument(
        "--require-identical",
        action="store_true",
        help="fail unless every captured record is exactly identical",
    )
    ap.add_argument(
        "--result-json",
        default=None,
        help="write a closed measurement document next to the human report",
    )
    args = ap.parse_args(argv)
    document = None
    digest_a = None
    digest_b = None
    try:
        try:
            records_a, digest_a = load_capture(args.a, args.label_a)
            records_b, digest_b = load_capture(args.b, args.label_b)
        except CaptureError as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            if exc.digest is not None and digest_a is None:
                digest_a = exc.digest
            elif exc.digest is not None and digest_b is None:
                digest_b = exc.digest
            document = incomplete_compare_document(
                reason="unusable-input",
                digest_a=digest_a,
                digest_b=digest_b,
            )
            return 2
        if len(records_a) != len(records_b):
            print(
                f"FATAL: different prompt counts {len(records_a)} vs {len(records_b)}"
            )
            document = incomplete_compare_document(
                reason="sample-count-mismatch",
                digest_a=digest_a,
                digest_b=digest_b,
            )
            return 2

        exact = 0
        identical = 0
        rates: list[float] = []
        decimal_rates: list[Decimal] = []
        lp_deltas: list[float] = []
        decimal_lp: list[Decimal] = []
        for i, (left, right) in enumerate(zip(records_a, records_b)):
            if left["prompt"] != right["prompt"]:
                print(f"FATAL: prompt mismatch at {i}", file=sys.stderr)
                document = incomplete_compare_document(
                    reason="prompt-mismatch",
                    digest_a=digest_a,
                    digest_b=digest_b,
                )
                return 2
            if left == right:
                identical += 1
            if left["text"] == right["text"]:
                exact += 1
            matched, rate = prefix_match(left["tokens"], right["tokens"])
            rates.append(rate)
            decimal_rates.append(prefix_rate(left["tokens"], right["tokens"]))
            delta = 0.0
            for index in range(matched):
                left_lp = left["logprobs"][index]
                right_lp = right["logprobs"][index]
                if left_lp is not None and right_lp is not None:
                    delta = max(delta, abs(left_lp - right_lp))
            lp_deltas.append(delta)
            decimal_lp.append(Decimal(str(delta)))
            flag = "" if rate == 1.0 else f"  <-- diverges at token {matched}"
            print(
                f"[{i:02d}] prefix={rate:5.3f} maxdlp={delta:6.3f} "
                f"{left['prompt'][:38]!r}{flag}"
            )

        # classify each divergence: near-tie (both sides' chosen-token logprobs
        # close => argmax flip on FP noise) vs real disagreement
        hard = []
        for i, (left, right) in enumerate(zip(records_a, records_b)):
            tokens_a, tokens_b = left["tokens"], right["tokens"]
            shared = min(len(tokens_a), len(tokens_b))
            index = 0
            while index < shared and tokens_a[index] == tokens_b[index]:
                index += 1
            if index < shared:
                left_lp = (
                    left["logprobs"][index]
                    if index < len(left["logprobs"])
                    else None
                )
                right_lp = (
                    right["logprobs"][index]
                    if index < len(right["logprobs"])
                    else None
                )
                margin = (
                    abs(left_lp - right_lp)
                    if left_lp is not None and right_lp is not None
                    else 99.0
                )
                left_s = "n/a" if left_lp is None else f"{left_lp:.3f}"
                right_s = "n/a" if right_lp is None else f"{right_lp:.3f}"
                print(
                    f"  div [{i:02d}] @tok{index}: {args.label_a}={tokens_a[index]!r}"
                    f"({left_s}) {args.label_b}={tokens_b[index]!r}({right_s}) "
                    f"delta={margin:.3f}"
                )
                if margin > 0.5:
                    hard.append(i)
            elif len(tokens_a) != len(tokens_b):
                print(
                    f"  div [{i:02d}] @tok{index}: truncated output "
                    f"lengths {len(tokens_a)} vs {len(tokens_b)}"
                )
                hard.append(i)

        count = len(records_a)
        print(f"\n{args.label_a} vs {args.label_b}: {count} prompts")
        print(f"  exact-text matches : {exact}/{count}")
        print(f"  identical captures : {identical}/{count}")
        print(f"  mean prefix match  : {sum(rates)/count:.4f}")
        print(f"  min  prefix match  : {min(rates):.4f}")
        print(f"  max logprob delta  : {max(lp_deltas):.4f} (over matched prefixes)")
        document = complete_compare_document(
            sample_count=count,
            identical_record_count=identical,
            exact_text_count=exact,
            rates=decimal_rates,
            lp_deltas=decimal_lp,
            hard_disagreement_count=len(hard),
            digest_a=digest_a,
            digest_b=digest_b,
        )
        if args.require_identical and identical != count:
            print("  verdict: NOT-IDENTICAL (strict run-to-run gate)")
            return 1
        if identical == count:
            print("  verdict: IDENTICAL")
            return 0
        if not hard and max(lp_deltas) < 0.5:
            print(
                "  verdict: FP-EQUIVALENT (all divergences are near-ties; "
                "expected across kernels/parallelism)"
            )
            return 0
        print(f"  verdict: DIVERGENT ({len(hard)} hard disagreements) — investigate")
        return 1
    except KeyboardInterrupt:
        document = incomplete_compare_document(
            reason="interrupted",
            digest_a=digest_a,
            digest_b=digest_b,
        )
        print("interrupted", file=sys.stderr)
        return 130
    finally:
        if args.result_json and document is not None:
            write_measurement(args.result_json, document)


if __name__ == "__main__":
    raise SystemExit(main())
