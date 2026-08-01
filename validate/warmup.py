#!/usr/bin/env python3
"""Post-boot JIT warmup for a live vLLM OpenAI server.

  validate/warmup.py --url http://127.0.0.1:8000 --model NAME

After a cold start, first inferences pay Triton/DeepGEMM/DSpark JIT
(markov, rejection sample, block FP8, …). That does not move steady-state
tok/s much, but it spikes first-token / first-batch latency and makes the
first client after restart look flaky.

This is pure stability: short + medium prompts, concurrency 1 and 4,
streaming and non-streaming, small max_tokens. Not a throughput benchmark
(see validate/bench_serve.py for measured numbers with per-level warmup).

Exit 0 only if every phase succeeds.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.request

# Keep max_tokens small: we only need the engine to take a few decode steps
# so rejection-sample / DSpark paths compile. Prompt length varies the
# prefill shape (short vs medium batch tokens).
PHASES = [
    # (label, approx_input_tokens, max_tokens, concurrency, stream)
    ("short/c1/sync", 64, 16, 1, False),
    ("short/c1/stream", 64, 16, 1, True),
    ("short/c4/sync", 64, 16, 4, False),
    ("short/c4/stream", 64, 16, 4, True),
    ("medium/c1/sync", 512, 32, 1, False),
    ("medium/c1/stream", 512, 32, 1, True),
    ("medium/c4/sync", 512, 32, 4, False),
    ("medium/c4/stream", 512, 32, 4, True),
]


def _words(n: int, seed: int) -> str:
    vocab = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa "
        "lambda mu nu xi omicron pi rho sigma tau upsilon "
    ).split()
    return " ".join(vocab[(seed + i) % len(vocab)] for i in range(max(1, n)))


def make_prompt(n_tokens: int, seed: int) -> str:
    # Vary by seed so prefix caching does not collapse every request into one
    # compiled shape. Synthetic is fine: we want kernel coverage, not draft
    # acceptance (use --prompt-style natural in bench_serve for that).
    return f"[warmup {seed}] Continue this sequence: {_words(n_tokens - 8, seed)}"


def resolve_model(url: str, model: str | None) -> str:
    if model:
        return model
    req = urllib.request.Request(url.rstrip("/") + "/v1/models")
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    ids = [m["id"] for m in data.get("data", [])]
    if not ids:
        raise RuntimeError(f"no models listed at {url}/v1/models")
    return ids[0]


def one_request(
    url: str,
    model: str,
    prompt: str,
    max_tokens: int,
    stream: bool,
    timeout: float,
) -> dict:
    body = {
        "model": model,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if not stream:
            payload = json.load(r)
            ntok = (payload.get("usage") or {}).get("completion_tokens")
        else:
            ntok = None
            for raw in r:
                if not raw.startswith(b"data:"):
                    continue
                chunk = raw[5:].strip()
                if chunk == b"[DONE]":
                    break
                d = json.loads(chunk)
                if d.get("usage"):
                    ntok = d["usage"].get("completion_tokens")
    return {"s": time.perf_counter() - t0, "ntok": ntok}


def run_phase(
    url: str,
    model: str,
    label: str,
    in_toks: int,
    max_tokens: int,
    conc: int,
    stream: bool,
    timeout: float,
) -> None:
    seeds = list(range(conc))
    t0 = time.perf_counter()
    errors: list[str] = []
    times: list[float] = []

    def work(seed: int) -> dict:
        return one_request(
            url,
            model,
            make_prompt(in_toks, seed + (1000 if stream else 0) + in_toks),
            max_tokens,
            stream,
            timeout,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
        futs = [pool.submit(work, s) for s in seeds]
        for f in concurrent.futures.as_completed(futs):
            try:
                times.append(f.result()["s"])
            except Exception as e:  # noqa: BLE001 — surface any client/server failure
                errors.append(f"{type(e).__name__}: {e}")

    wall = time.perf_counter() - t0
    if errors:
        raise RuntimeError(f"{label}: {len(errors)}/{conc} failed: {errors[0]}")
    p50 = sorted(times)[len(times) // 2]
    print(
        f"  {label:<18} conc={conc} stream={str(stream):<5} "
        f"wall={wall:5.1f}s  req_p50={p50:5.1f}s",
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", default=None, help="served-model-name; default: first /v1/models id")
    ap.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="per-request timeout seconds (cold JIT can be slow)",
    )
    ap.add_argument(
        "--phases",
        choices=["all", "quick"],
        default="all",
        help="all = short+medium × c=1,4 × stream/sync (default); "
        "quick = short c=1 stream+sync only",
    )
    a = ap.parse_args()

    try:
        model = resolve_model(a.url, a.model)
    except Exception as e:  # noqa: BLE001
        print(f"[warmup] cannot resolve model: {e}", file=sys.stderr)
        return 2

    phases = PHASES if a.phases == "all" else [p for p in PHASES if p[0].startswith("short/c1/")]
    print(f"[warmup] {a.url} model={model} phases={len(phases)}", flush=True)
    t0 = time.perf_counter()
    for label, in_toks, max_toks, conc, stream in phases:
        try:
            run_phase(a.url, model, label, in_toks, max_toks, conc, stream, a.timeout)
        except Exception as e:  # noqa: BLE001
            print(f"[warmup] FAILED at {label}: {e}", file=sys.stderr)
            return 1
    print(f"[warmup] ok in {time.perf_counter() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
