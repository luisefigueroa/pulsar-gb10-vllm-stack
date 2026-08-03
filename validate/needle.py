#!/usr/bin/env python3
"""Needle-in-haystack at a target context length against a running server.

  validate/needle.py --url http://127.0.0.1:8000 --model NAME \
      --context-tokens 250000 [--depths 0.1 0.5 0.9] [--trials-per-depth 2]

Builds filler text to ~context-tokens (approx 4 chars/token; verified against
the server's returned prompt_tokens), hides a unique magic value at each depth,
and asks for it back. PASS requires every trial to contain the exact value.
"""
import argparse, json, random, sys, urllib.request

from http_auth import api_headers, resolve_api_key

FILLER = (
    "The quick brown fox jumps over the lazy dog. Pack my box with five dozen "
    "liquor jugs. How vexingly quick daft zebras jump. Sphinx of black quartz, "
    "judge my vow. "
)

def chat(url, model, prompt, api_key, max_tokens=48):
    body = {"model": model, "prompt": prompt, "max_tokens": max_tokens, "temperature": 0}
    req = urllib.request.Request(url + "/v1/completions",
                                 data=json.dumps(body).encode(),
                                 headers=api_headers(api_key, content_type=True))
    with urllib.request.urlopen(req, timeout=3600) as r:
        d = json.load(r)
    return d["choices"][0]["text"], d["usage"]["prompt_tokens"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None,
                    help="API key; defaults to VLLM_API_KEY or API_KEY environment")
    ap.add_argument("--context-tokens", type=int, required=True)
    ap.add_argument("--depths", nargs="+", type=float, default=[0.05, 0.25, 0.5, 0.75, 0.95])
    ap.add_argument("--trials-per-depth", type=int, default=1)
    a = ap.parse_args()
    api_key = resolve_api_key(a.api_key)

    rng = random.Random(42)
    target_chars = a.context_tokens * 4
    ok = fail = 0
    for depth in a.depths:
        for t in range(a.trials_per_depth):
            magic = f"{rng.randrange(10**8):08d}"
            needle = f" The secret access code is {magic}. Remember it. "
            n_fill = target_chars // len(FILLER)
            pos = int(n_fill * depth)
            hay = FILLER * pos + needle + FILLER * (n_fill - pos)
            prompt = (hay + "\n\nQuestion: What is the secret access code mentioned "
                      "in the text above? Answer with just the number.\nAnswer:")
            try:
                text, ptoks = chat(a.url, a.model, prompt, api_key)
            except Exception as e:
                print(f"depth={depth:4.2f} trial={t} ERROR: {e}")
                fail += 1
                continue
            hit = magic in text
            ok += hit; fail += (not hit)
            print(f"depth={depth:4.2f} trial={t} prompt_tokens={ptoks} "
                  f"{'PASS' if hit else 'FAIL'} (got: {text.strip()[:40]!r})")
    print(f"\nresult: {ok} pass / {fail} fail")
    sys.exit(0 if fail == 0 else 1)

if __name__ == "__main__":
    main()
