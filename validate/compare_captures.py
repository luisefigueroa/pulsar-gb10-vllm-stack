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

def prefix_match(a_toks, b_toks):
    n = min(len(a_toks), len(b_toks))
    if n == 0:
        return 0, 0.0
    i = 0
    while i < n and a_toks[i] == b_toks[i]:
        i += 1
    return i, i / n

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a"); ap.add_argument("b")
    ap.add_argument("--label-a", default="A"); ap.add_argument("--label-b", default="B")
    args = ap.parse_args()
    A = json.load(open(args.a)); B = json.load(open(args.b))
    if len(A) != len(B):
        print(f"FATAL: different prompt counts {len(A)} vs {len(B)}"); sys.exit(2)

    exact = 0; rates = []; lp_deltas = []
    for i, (x, y) in enumerate(zip(A, B)):
        assert x["prompt"] == y["prompt"], f"prompt mismatch at {i}"
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

    n = len(A)
    print(f"\n{args.label_a} vs {args.label_b}: {n} prompts")
    print(f"  exact-text matches : {exact}/{n}")
    print(f"  mean prefix match  : {sum(rates)/n:.4f}")
    print(f"  min  prefix match  : {min(rates):.4f}")
    print(f"  max logprob delta  : {max(lp_deltas):.4f} (over matched prefixes)")
    # exit nonzero if wildly off, for scripting
    sys.exit(0 if sum(rates)/n >= 0.90 else 1)

if __name__ == "__main__":
    main()
