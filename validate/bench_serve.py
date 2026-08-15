#!/usr/bin/env python3
"""Throughput/latency benchmark with warmup discipline.

  validate/bench_serve.py --url http://127.0.0.1:8000 --model NAME \
      [--concurrency 1 2 4 8 16] [--num-requests N] \
      [--input-tokens 512] [--output-tokens 256] [--result-json PATH]

Per level: WARM UP at that concurrency first (Triton JITs kernels per batch
shape — cold numbers are ~100x artifacts), then measure TTFT, decode tok/s
per stream, aggregate tok/s. Streaming, temperature 0.

``--num-requests`` sets the measured request count at every concurrency
level. When omitted, the historical default remains max(2 * concurrency, 4).
Optional ``--result-json`` writes a closed measurement document. Historical
``--out`` remains the row-array format.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
import urllib.request
from typing import Any

from http_auth import api_headers, resolve_api_key
from validator_measurement import (
    build_benchmark_measurement,
    decimal_from_number,
    empty_benchmark_level,
    write_measurement,
)


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


def requested_request_count(concurrency: int, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    return max(2 * concurrency, 4)


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one sample")
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percent / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


async def run_level(url, model, conc, nreq, in_toks, out_toks, warm, api_key):
    results = []
    n = conc if warm else nreq
    seeds = range(1000, 1000 + n) if warm else range(n)
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


def complete_level_payload(
    *,
    concurrency: int,
    requested: int,
    results: list[dict[str, Any]],
    wall: float,
) -> dict[str, Any]:
    ttfts = [row["ttft"] for row in results]
    decode = [row["decode_tps"] for row in results]
    return {
        "concurrency": concurrency,
        "requested_request_count": requested,
        "measured_request_count": len(results),
        "completion": "complete",
        "reason": "completed",
        "ttft_p50_ms": decimal_from_number(percentile(ttfts, 50.0) * 1000),
        "ttft_p95_ms": decimal_from_number(percentile(ttfts, 95.0) * 1000),
        "decode_tps_p50": decimal_from_number(statistics.median(decode)),
        "aggregate_tps": decimal_from_number(sum(row["ntok"] for row in results) / wall),
        "wall_s": decimal_from_number(wall),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None,
                    help="API key; defaults to VLLM_API_KEY or API_KEY environment")
    ap.add_argument("--concurrency", nargs="+", type=int, default=[1, 2, 4, 8, 16])
    ap.add_argument(
        "--num-requests",
        type=int,
        default=None,
        help="measured request count at each concurrency (default: 2x concurrency, min 4)",
    )
    ap.add_argument("--input-tokens", type=int, default=512)
    ap.add_argument("--output-tokens", type=int, default=256)
    ap.add_argument("--prompt-style", choices=["synthetic", "natural"],
                    default="synthetic",
                    help="natural = coherent prompts (REQUIRED for spec-decode "
                         "A/Bs; synthetic collapses draft acceptance). Default "
                         "stays synthetic for comparability with recorded runs.")
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--result-json",
        default=None,
        help="write a closed measurement document; does not replace --out",
    )
    a = ap.parse_args()
    if not a.concurrency or any(c <= 0 for c in a.concurrency):
        ap.error("--concurrency values must all be positive")
    if len(a.concurrency) != len(set(a.concurrency)):
        ap.error("--concurrency values must be unique")
    if a.num_requests is not None and a.num_requests <= 0:
        ap.error("--num-requests must be positive")
    if a.num_requests is not None and a.num_requests < max(a.concurrency):
        ap.error("--num-requests must be at least the largest --concurrency value")
    if a.input_tokens <= 0:
        ap.error("--input-tokens must be positive")
    if a.output_tokens < 2:
        ap.error("--output-tokens must be at least 2")

    api_key = resolve_api_key(a.api_key)
    global PROMPT_STYLE
    PROMPT_STYLE = a.prompt_style

    all_rows = []
    measurement_levels: list[dict[str, Any]] = []
    failed_levels = 0
    first_failure_reason = None
    document = None
    try:
        print(f"{'conc':>4} {'n':>3} {'TTFT p50 ms':>12} {'decode tok/s':>13} {'agg tok/s':>10} {'wall s':>7}")
        for c in a.concurrency:
            requested = requested_request_count(c, a.num_requests)
            warm_results, _ = await run_level(
                a.url, a.model, c, c, a.input_tokens, a.output_tokens,
                warm=True, api_key=api_key
            )
            if len(warm_results) != c:
                print(f"{c:>4} FAILED (warmup results {len(warm_results)}/{c})")
                failed_levels += 1
                first_failure_reason = first_failure_reason or "warmup-failed"
                measurement_levels.append(
                    empty_benchmark_level(
                        concurrency=c,
                        requested_request_count=requested,
                        reason="warmup-failed",
                    )
                )
                continue
            nreq = requested
            results, wall = await run_level(a.url, a.model, c, nreq, a.input_tokens,
                                            a.output_tokens, warm=False, api_key=api_key)
            if len(results) != nreq:
                print(f"{c:>4} FAILED (measured results {len(results)}/{nreq})")
                failed_levels += 1
                first_failure_reason = first_failure_reason or "measured-incomplete"
                measurement_levels.append(
                    empty_benchmark_level(
                        concurrency=c,
                        requested_request_count=requested,
                        measured_request_count=len(results),
                        reason="measured-incomplete",
                    )
                )
                continue
            ttft = statistics.median(r["ttft"] for r in results) * 1000
            dtps = statistics.median(r["decode_tps"] for r in results)
            agg = sum(r["ntok"] for r in results) / wall
            row = {"concurrency": c, "n": len(results), "ttft_p50_ms": round(ttft, 1),
                   "decode_tps_p50": round(dtps, 2), "aggregate_tps": round(agg, 2),
                   "wall_s": round(wall, 1)}
            all_rows.append(row)
            measurement_levels.append(
                complete_level_payload(
                    concurrency=c,
                    requested=requested,
                    results=results,
                    wall=wall,
                )
            )
            print(f"{c:>4} {len(results):>3} {ttft:>12.1f} {dtps:>13.2f} {agg:>10.2f} {wall:>7.1f}")
        document = build_benchmark_measurement(
            completion="complete" if not failed_levels else "incomplete",
            reason="completed" if not failed_levels else (first_failure_reason or "measured-incomplete"),
            input_tokens=a.input_tokens,
            output_tokens=a.output_tokens,
            prompt_style=a.prompt_style,
            explicit_request_count=a.num_requests,
            levels=measurement_levels,
        )
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump(all_rows, f, indent=1)
            print(f"wrote {a.out}")
        return 1 if failed_levels else 0
    except (KeyboardInterrupt, asyncio.CancelledError):
        if not measurement_levels:
            measurement_levels = [
                empty_benchmark_level(
                    concurrency=a.concurrency[0],
                    requested_request_count=requested_request_count(
                        a.concurrency[0], a.num_requests
                    ),
                    reason="interrupted",
                )
            ]
        else:
            # Preserve completed levels; mark the remainder as interrupted.
            seen = {item["concurrency"] for item in measurement_levels}
            for concurrency in a.concurrency:
                if concurrency in seen:
                    continue
                measurement_levels.append(
                    empty_benchmark_level(
                        concurrency=concurrency,
                        requested_request_count=requested_request_count(
                            concurrency, a.num_requests
                        ),
                        reason="interrupted",
                    )
                )
        document = build_benchmark_measurement(
            completion="incomplete",
            reason="interrupted",
            input_tokens=a.input_tokens,
            output_tokens=a.output_tokens,
            prompt_style=a.prompt_style,
            explicit_request_count=a.num_requests,
            levels=measurement_levels,
        )
        print("interrupted", file=sys.stderr)
        return 130
    except Exception:
        if document is None:
            seen = {item["concurrency"] for item in measurement_levels}
            for concurrency in a.concurrency:
                if concurrency in seen:
                    continue
                measurement_levels.append(
                    empty_benchmark_level(
                        concurrency=concurrency,
                        requested_request_count=requested_request_count(
                            concurrency, a.num_requests
                        ),
                        reason="measured-incomplete",
                    )
                )
            if not measurement_levels:
                measurement_levels = [
                    empty_benchmark_level(
                        concurrency=a.concurrency[0],
                        requested_request_count=requested_request_count(
                            a.concurrency[0], a.num_requests
                        ),
                        reason="measured-incomplete",
                    )
                ]
            document = build_benchmark_measurement(
                completion="incomplete",
                reason="measured-incomplete",
                input_tokens=a.input_tokens,
                output_tokens=a.output_tokens,
                prompt_style=a.prompt_style,
                explicit_request_count=a.num_requests,
                levels=measurement_levels,
            )
        print("benchmark execution failed", file=sys.stderr)
        return 1
    finally:
        if a.result_json and document is not None:
            write_measurement(a.result_json, document)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
