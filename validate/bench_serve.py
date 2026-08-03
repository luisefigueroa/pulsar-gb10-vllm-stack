#!/usr/bin/env python3
"""Throughput/latency benchmark with warmup discipline.

  validate/bench_serve.py --url http://127.0.0.1:8000 --model NAME \
      [--concurrency 1 2 4 8 16] [--num-requests 2x-concurrency] \
      [--input-tokens 512] [--output-tokens 256]

Per level: WARM UP at that concurrency first (Triton JITs kernels per batch
shape — cold numbers are ~100x artifacts), then measure TTFT, decode tok/s
per stream, aggregate tok/s. Streaming, temperature 0.
"""
import argparse, asyncio, json, statistics, sys, time
import urllib.request

from http_auth import api_headers, resolve_api_key

async def one_request(url, model, prompt, out_toks, results, api_key):
    t0 = time.perf_counter()
    ttft = None
    ntok = 0
    body = {"model": model, "prompt": prompt, "max_tokens": out_toks,
            "temperature": 0, "stream": True, "ignore_eos": True,
            # CRITICAL: count tokens from usage, NOT from SSE chunk count.
            # Under speculative decoding one chunk carries a whole accepted
            # block (measured 3.46x undercount on DSpark) — chunk-counting
            # silently divides throughput by the acceptance factor.
            "stream_options": {"include_usage": True}}

    def blocking():
        nonlocal ttft, ntok
        req = urllib.request.Request(url + "/v1/completions",
                                     data=json.dumps(body).encode(),
                                     headers=api_headers(api_key, content_type=True))
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    break
                d = json.loads(payload)
                if d.get("usage"):
                    ntok = d["usage"]["completion_tokens"]
                if d.get("choices") and d["choices"][0].get("text"):
                    if ttft is None:
                        ttft = time.perf_counter() - t0

    await asyncio.get_running_loop().run_in_executor(None, blocking)
    t1 = time.perf_counter()
    if ttft is not None and ntok > 1:
        results.append({"ttft": ttft, "decode_tps": (ntok - 1) / (t1 - t0 - ttft),
                        "total_s": t1 - t0, "ntok": ntok})

PROMPT_STYLE = "synthetic"

_TOPICS = [
    "the history of ocean navigation", "how photosynthesis works",
    "the architecture of medieval castles", "sorting algorithms in Python",
    "the economics of renewable energy", "a mystery story set in Venice",
    "how vaccines train the immune system", "the geology of volcanic islands",
    "the design of suspension bridges", "training a neural network from scratch",
    "the culture of Edo-period Japan", "how weather forecasting models work",
]


def make_prompt(n_tokens, seed):
    if PROMPT_STYLE == "natural":
        # Coherent open-ended prompts: spec-decode acceptance on these is
        # representative; the synthetic repeat-prompts below collapse draft
        # acceptance and are ADVERSARIAL to speculative decoding (measured:
        # 14 vs 40 tok/s on the same server — see docs/VALIDATION.md).
        # Pad with a numbered preamble to reach the target input length
        # without creating repetitive continuations.
        topic = _TOPICS[seed % len(_TOPICS)]
        pad_sent = (f"Background note {seed}: consider aspect %d of the topic "
                    "before writing, including causes, context, and examples. ")
        pad, i = "", 0
        while len(pad.split()) < max(0, n_tokens - 40):
            pad += pad_sent % i
            i += 1
        return f"[req {seed}] {pad}\nWrite a detailed essay about {topic}:"
    # ~1 token per word for common words; vary by seed to dodge prefix cache
    words = ("alpha beta gamma delta epsilon zeta eta theta iota kappa "
             "lambda mu nu xi omicron pi rho sigma tau upsilon ").split()
    body = " ".join(words[(seed + i) % len(words)] for i in range(n_tokens - 8))
    return f"[req {seed}] Repeat this sequence: {body}"

async def run_level(url, model, conc, nreq, in_toks, out_toks, warm, api_key):
    results = []
    n = conc if warm else nreq
    seeds = range(1000 + n) if warm else range(n)
    tasks = []
    sem = asyncio.Semaphore(conc)
    async def guarded(s):
        async with sem:
            await one_request(url, model, make_prompt(in_toks, s),
                              out_toks if not warm else min(32, out_toks),
                              results, api_key)
    t0 = time.perf_counter()
    await asyncio.gather(*(guarded(s) for s in list(seeds)[:n]))
    wall = time.perf_counter() - t0
    return results, wall

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None,
                    help="API key; defaults to VLLM_API_KEY or API_KEY environment")
    ap.add_argument("--concurrency", nargs="+", type=int, default=[1, 2, 4, 8, 16])
    ap.add_argument("--input-tokens", type=int, default=512)
    ap.add_argument("--output-tokens", type=int, default=256)
    ap.add_argument("--prompt-style", choices=["synthetic", "natural"],
                    default="synthetic",
                    help="natural = coherent prompts (REQUIRED for spec-decode "
                         "A/Bs; synthetic collapses draft acceptance). Default "
                         "stays synthetic for comparability with recorded runs.")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    api_key = resolve_api_key(a.api_key)
    global PROMPT_STYLE
    PROMPT_STYLE = a.prompt_style

    all_rows = []
    print(f"{'conc':>4} {'n':>3} {'TTFT p50 ms':>12} {'decode tok/s':>13} {'agg tok/s':>10} {'wall s':>7}")
    for c in a.concurrency:
        await run_level(a.url, a.model, c, c, a.input_tokens, a.output_tokens,
                        warm=True, api_key=api_key)
        nreq = max(2 * c, 4)
        results, wall = await run_level(a.url, a.model, c, nreq, a.input_tokens,
                                        a.output_tokens, warm=False, api_key=api_key)
        if not results:
            print(f"{c:>4} FAILED (no results)")
            continue
        ttft = statistics.median(r["ttft"] for r in results) * 1000
        dtps = statistics.median(r["decode_tps"] for r in results)
        agg = sum(r["ntok"] for r in results) / wall
        row = {"concurrency": c, "n": len(results), "ttft_p50_ms": round(ttft, 1),
               "decode_tps_p50": round(dtps, 2), "aggregate_tps": round(agg, 2),
               "wall_s": round(wall, 1)}
        all_rows.append(row)
        print(f"{c:>4} {len(results):>3} {ttft:>12.1f} {dtps:>13.2f} {agg:>10.2f} {wall:>7.1f}")
    if a.out:
        json.dump(all_rows, open(a.out, "w"), indent=1)
        print(f"wrote {a.out}")

if __name__ == "__main__":
    asyncio.run(main())
