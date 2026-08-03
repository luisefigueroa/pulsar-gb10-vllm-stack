#!/usr/bin/env python3
"""Compare two greedy captures (from greedy_capture.py).

  validate/compare_captures.py A.json B.json [--label-a X --label-b Y]

Reports per-prompt and aggregate:
  - exact match (full text identical)
  - token match rate up to first divergence (prefix match / min length)
  - max |logprob delta| over the common matched prefix

Gate guidance (docs/VALIDATION.md): same config run-to-run must be EXACT.
Different kernels/parallelism gate on prefix-match >= 0.90 mean and
matched-prefix logprob deltas that stay small (< 0.5); systematic early
divergence on many prompts is a red flag regardless of the numbers.
"""
import argparse, json, sys

class CaptureError(ValueError):
    pass


def load_capture(path, label):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"{label}: cannot read capture: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise CaptureError(f"{label}: capture must be a non-empty JSON list")
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise CaptureError(f"{label}[{i}]: entry must be an object")
        for field, kind in (
            ("prompt", str),
            ("text", str),
            ("tokens", list),
            ("logprobs", list),
        ):
            if not isinstance(row.get(field), kind):
                raise CaptureError(
                    f"{label}[{i}].{field}: expected {kind.__name__}"
                )
        if len(row["logprobs"]) < len(row["tokens"]):
            raise CaptureError(
                f"{label}[{i}]: logprobs shorter than tokens "
                f"({len(row['logprobs'])} < {len(row['tokens'])})"
            )
        if any(
            value is not None and not isinstance(value, (int, float))
            for value in row["logprobs"]
        ):
            raise CaptureError(f"{label}[{i}]: logprobs must be numeric or null")
    return data

def prefix_match(a_toks, b_toks):
    n = min(len(a_toks), len(b_toks))
    if n == 0:
        return 0, 1.0 if not a_toks and not b_toks else 0.0
    i = 0
    while i < n and a_toks[i] == b_toks[i]:
        i += 1
    return i, i / max(len(a_toks), len(b_toks))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--label-a", default="A"); ap.add_argument("--label-b", default="B")
    ap.add_argument(
        "--require-identical",
        action="store_true",
        help="fail unless every captured record is exactly identical",
    )
    args = ap.parse_args()
    try:
        A = load_capture(args.a, args.label_a)
        B = load_capture(args.b, args.label_b)
    except CaptureError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    if len(A) != len(B):
        print(f"FATAL: different prompt counts {len(A)} vs {len(B)}")
        return 2

    exact = 0; identical = 0; rates = []; lp_deltas = []
    for i, (x, y) in enumerate(zip(A, B)):
        if x["prompt"] != y["prompt"]:
            print(f"FATAL: prompt mismatch at {i}", file=sys.stderr)
            return 2
        if x == y:
            identical += 1
        if x["text"] == y["text"]:
            exact += 1
        matched, rate = prefix_match(x["tokens"], y["tokens"])
        rates.append(rate)
        d = 0.0
        for j in range(matched):
            la, lb = x["logprobs"][j], y["logprobs"][j]
            if la is not None and lb is not None:
                d = max(d, abs(la - lb))
        lp_deltas.append(d)
        flag = "" if rate == 1.0 else f"  <-- diverges at token {matched}"
        print(f"[{i:02d}] prefix={rate:5.3f} maxdlp={d:6.3f} {x['prompt'][:38]!r}{flag}")

    # classify each divergence: near-tie (both sides' chosen-token logprobs
    # close => argmax flip on FP noise) vs real disagreement
    hard = []
    for i, (x, y) in enumerate(zip(A, B)):
        ta, tb = x["tokens"], y["tokens"]
        n_ = min(len(ta), len(tb)); j = 0
        while j < n_ and ta[j] == tb[j]:
            j += 1
        if j < n_:
            la = x["logprobs"][j] if j < len(x["logprobs"]) else None
            lb = y["logprobs"][j] if j < len(y["logprobs"]) else None
            margin = abs(la - lb) if la is not None and lb is not None else 99.0
            la_s = "n/a" if la is None else f"{la:.3f}"
            lb_s = "n/a" if lb is None else f"{lb:.3f}"
            print(f"  div [{i:02d}] @tok{j}: {args.label_a}={ta[j]!r}({la_s}) "
                  f"{args.label_b}={tb[j]!r}({lb_s}) delta={margin:.3f}")
            if margin > 0.5:
                hard.append(i)
        elif len(ta) != len(tb):
            print(
                f"  div [{i:02d}] @tok{j}: truncated output "
                f"lengths {len(ta)} vs {len(tb)}"
            )
            hard.append(i)

    n = len(A)
    print(f"\n{args.label_a} vs {args.label_b}: {n} prompts")
    print(f"  exact-text matches : {exact}/{n}")
    print(f"  identical captures : {identical}/{n}")
    print(f"  mean prefix match  : {sum(rates)/n:.4f}")
    print(f"  min  prefix match  : {min(rates):.4f}")
    print(f"  max logprob delta  : {max(lp_deltas):.4f} (over matched prefixes)")
    if args.require_identical and identical != n:
        print("  verdict: NOT-IDENTICAL (strict run-to-run gate)")
        return 1
    if identical == n:
        print("  verdict: IDENTICAL")
        return 0
    elif not hard and max(lp_deltas) < 0.5:
        print("  verdict: FP-EQUIVALENT (all divergences are near-ties; "
              "expected across kernels/parallelism)")
        return 0
    else:
        print(f"  verdict: DIVERGENT ({len(hard)} hard disagreements) — investigate")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
