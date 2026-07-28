#!/usr/bin/env python3
"""Capture greedy completions + logprobs from an OpenAI-compatible server.

  validate/greedy_capture.py --url http://127.0.0.1:8000 --model NAME \
      --prompts validate/prompts.txt --out results/<file>.json [--max-tokens 64]

Output JSON: [{prompt, token_ids, tokens, logprobs}] — the comparison unit for
determinism (hash-identical), node parity, 1-vs-2-node, and HF reference checks.
"""
import argparse, json, sys, urllib.request

def complete(url, model, prompt, max_tokens):
    body = {
        "model": model, "prompt": prompt, "max_tokens": max_tokens,
        "temperature": 0, "seed": 42, "logprobs": 1,
    }
    req = urllib.request.Request(
        url + "/v1/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    ch = d["choices"][0]
    lp = ch.get("logprobs") or {}
    return {
        "prompt": prompt,
        "text": ch["text"],
        "tokens": lp.get("tokens", []),
        "logprobs": lp.get("token_logprobs", []),
        "finish_reason": ch.get("finish_reason"),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--prompts", default="validate/prompts.txt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=64)
    a = ap.parse_args()

    prompts = [l.rstrip("\n") for l in open(a.prompts) if l.strip()]
    results = []
    for i, p in enumerate(prompts):
        results.append(complete(a.url, a.model, p, a.max_tokens))
        print(f"  [{i+1}/{len(prompts)}] {p[:40]!r} -> {results[-1]['text'][:40]!r}", file=sys.stderr)
    json.dump(results, open(a.out, "w"), indent=1)
    print(f"wrote {a.out} ({len(results)} prompts)")

if __name__ == "__main__":
    main()
