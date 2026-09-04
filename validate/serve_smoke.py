#!/usr/bin/env python3
"""Closed serving-integration smoke against an already-running server.

  validate/serve_smoke.py --url http://127.0.0.1:8000 --model NAME \
      --result-json OUT [--api-key KEY] [--timeout SECONDS] \
      [--health-timeout SECONDS]

Three phases run in order and each is recorded as complete or incomplete:

  health      GET /health answers 200 within --health-timeout
  warmup      every validate/warmup.py phase succeeds (JIT paths compiled)
  completion  one greedy /v1/completions request returns non-empty text

A later phase is never attempted after an earlier one fails; it stays
``measured-incomplete``. The measurement records phase outcomes only. It
starts or stops nothing, names no endpoint, and assigns no status.

Exit 0 when every phase completed, 1 otherwise, 2 for unusable arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Callable

_VALIDATE_DIR = Path(__file__).resolve().parent
if str(_VALIDATE_DIR) not in sys.path:
    sys.path.insert(0, str(_VALIDATE_DIR))

import warmup  # noqa: E402
from http_auth import api_headers, resolve_api_key  # noqa: E402
from validator_measurement import (  # noqa: E402
    ValidatorMeasurementError,
    build_serve_smoke_measurement,
    empty_serve_smoke_phase,
    write_measurement,
)

PHASE_NAMES = ("health", "warmup", "completion")
SMOKE_PROMPT = "2+2="
SMOKE_MAX_TOKENS = 8
HEALTH_POLL_SECONDS = 2.0


def check_health(
    url: str, api_key: str | None, *, timeout: float, health_timeout: float
) -> None:
    """Return once /health answers 200; raise after health_timeout."""
    deadline = time.monotonic() + health_timeout
    last = "no attempt"
    while True:
        request = urllib.request.Request(
            url.rstrip("/") + "/health", headers=api_headers(api_key)
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status == 200:
                    return
                last = f"HTTP {response.status}"
        except Exception as exc:  # noqa: BLE001 — any transport failure is a retry
            last = f"{type(exc).__name__}: {exc}"
        if time.monotonic() >= deadline:
            raise RuntimeError(f"health did not answer 200: {last}")
        time.sleep(HEALTH_POLL_SECONDS)


def run_warmup(url: str, model: str, api_key: str | None, *, timeout: float) -> None:
    """Run every warmup phase; warmup.run_phase raises on any failure."""
    for label, in_toks, max_toks, conc, stream in warmup.PHASES:
        warmup.run_phase(
            url, model, label, in_toks, max_toks, conc, stream, timeout, api_key
        )


def check_completion(
    url: str, model: str, api_key: str | None, *, timeout: float
) -> str:
    """Return the text of one greedy completion; raise when it is empty."""
    body = {
        "model": model,
        "prompt": SMOKE_PROMPT,
        "max_tokens": SMOKE_MAX_TOKENS,
        "temperature": 0,
    }
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=api_headers(api_key, content_type=True),
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    choices = payload.get("choices") or []
    text = str((choices[0] if choices else {}).get("text") or "")
    if not text.strip():
        raise RuntimeError("completion returned no text")
    return text


def run_phases(
    phases: list[tuple[str, Callable[[], object]]],
) -> tuple[str, str, dict[str, dict[str, str]]]:
    """Run phases in order; stop at the first failure."""
    results = {name: empty_serve_smoke_phase() for name in PHASE_NAMES}
    completion = "complete"
    reason = "completed"
    for name, action in phases:
        started = time.perf_counter()
        try:
            action()
        except KeyboardInterrupt:
            results[name] = empty_serve_smoke_phase(reason="interrupted")
            completion = "incomplete"
            reason = "interrupted"
            print(f"[smoke] {name}: interrupted", file=sys.stderr, flush=True)
            break
        except Exception as exc:  # noqa: BLE001 — record the phase as failed
            results[name] = empty_serve_smoke_phase(reason="failed")
            completion = "incomplete"
            reason = f"{name}-failed"
            print(
                f"[smoke] {name}: failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            break
        results[name] = empty_serve_smoke_phase(
            completion="complete", reason="completed"
        )
        print(
            f"[smoke] {name}: complete ({time.perf_counter() - started:.1f}s)",
            flush=True,
        )
    return completion, reason, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", required=True, help="served model name")
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key; defaults to VLLM_API_KEY or API_KEY environment",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="per-request timeout seconds (cold JIT can be slow)",
    )
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=60.0,
        help="seconds to keep polling /health before the phase fails",
    )
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.health_timeout < 0:
        parser.error("--timeout must be positive and --health-timeout non-negative")
    api_key = resolve_api_key(args.api_key)

    phases: list[tuple[str, Callable[[], object]]] = [
        (
            "health",
            lambda: check_health(
                args.url,
                api_key,
                timeout=args.timeout,
                health_timeout=args.health_timeout,
            ),
        ),
        (
            "warmup",
            lambda: run_warmup(args.url, args.model, api_key, timeout=args.timeout),
        ),
        (
            "completion",
            lambda: check_completion(
                args.url, args.model, api_key, timeout=args.timeout
            ),
        ),
    ]
    completion, reason, results = run_phases(phases)
    try:
        document = build_serve_smoke_measurement(
            completion=completion, reason=reason, payload=results
        )
        write_measurement(args.result_json, document)
    except ValidatorMeasurementError as exc:
        print(f"measurement could not be written: {exc}", file=sys.stderr)
        return 2
    print(f"serve smoke {completion} ({reason})")
    return 0 if completion == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
