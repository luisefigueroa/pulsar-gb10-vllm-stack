#!/usr/bin/env python3
"""Load soak: sustained concurrent traffic + resource sampling.

  validate/soak.py --url http://127.0.0.1:8000 --model NAME \
      --minutes 30 --concurrency 8 [--out results/soak-x.json]

Runs a closed-loop pool of workers with mixed prompt lengths, samples
MemAvailable/GPU temp/power every 30 s, counts errors, and reports memory
growth (first-decile vs last-decile of samples) at the end.

Exit policy (ship-set defaults):
  PASS (0)  — completed > 0 and errors <= --max-errors (default 0)
  FAIL (1)  — any excess errors, or zero completions
  Memory shrink >5% of start-decile MemAvailable is a WARN finding by
  default (exit still 0 unless --fail-on-mem-shrink). Residual pressure
  with 0 request errors can be a reviewed finding, not an automatic fail.
"""
import argparse
import json
import random
import subprocess
import sys
import threading
import time
import urllib.request

from http_auth import api_headers, resolve_api_key

STOP = False
ERRORS = []
COMPLETED = [0]
LOCK = threading.Lock()


def worker(url, model, wid, api_key):
    rng = random.Random(wid)
    while not STOP:
        n_in = rng.choice([64, 256, 1024, 4096])
        n_out = rng.choice([64, 256, 512])
        words = "one two three four five six seven eight nine ten".split()
        prompt = f"[w{wid}-{rng.randrange(1<<30)}] " + " ".join(
            rng.choice(words) for _ in range(n_in)
        )
        body = {
            "model": model,
            "prompt": prompt,
            "max_tokens": n_out,
            "temperature": 0.7,
            "seed": rng.randrange(1 << 31),
        }
        try:
            req = urllib.request.Request(
                url + "/v1/completions",
                data=json.dumps(body).encode(),
                headers=api_headers(api_key, content_type=True),
            )
            with urllib.request.urlopen(req, timeout=1800) as r:
                json.load(r)
            with LOCK:
                COMPLETED[0] += 1
        except Exception as e:
            with LOCK:
                ERRORS.append(
                    f"{time.strftime('%H:%M:%S')} w{wid}: {type(e).__name__} {e}"
                )


def sample():
    out = {"t": time.time()}
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                out["mem_avail_gib"] = round(int(line.split()[1]) / 1048576, 2)
    except Exception:
        pass
    try:
        q = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,power.draw,clocks.sm",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        t, p, c = [x.strip() for x in q.stdout.strip().split(",")]
        out["gpu_temp"] = int(t)
        out["power_w"] = None if "N/A" in p else float(p)
        out["sm_mhz"] = int(c)
    except Exception:
        pass
    return out


def main():
    global STOP
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None,
                    help="API key; defaults to VLLM_API_KEY or API_KEY environment")
    ap.add_argument("--minutes", type=float, required=True)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--max-errors",
        type=int,
        default=0,
        help="fail if error count exceeds this (default 0 = any error fails)",
    )
    ap.add_argument(
        "--fail-on-mem-shrink",
        action="store_true",
        help="also FAIL when first→last decile MemAvailable shrink exceeds 5%%",
    )
    ap.add_argument(
        "--mem-shrink-pct",
        type=float,
        default=5.0,
        help="mem shrink threshold percent of start-decile mean (default 5)",
    )
    a = ap.parse_args()
    api_key = resolve_api_key(a.api_key)

    threads = [
        threading.Thread(
            target=worker,
            args=(a.url, a.model, i, api_key),
            daemon=True,
        )
        for i in range(a.concurrency)
    ]
    for t in threads:
        t.start()
    samples = []
    t_end = time.time() + a.minutes * 60
    while time.time() < t_end:
        s = sample()
        samples.append(s)
        with LOCK:
            done, errs = COMPLETED[0], len(ERRORS)
        print(
            f"{time.strftime('%H:%M:%S')} done={done} errors={errs} "
            f"mem_avail={s.get('mem_avail_gib')}GiB temp={s.get('gpu_temp')}C "
            f"sm={s.get('sm_mhz')}MHz",
            flush=True,
        )
        time.sleep(30)
    STOP = True
    time.sleep(5)

    mem = [s["mem_avail_gib"] for s in samples if "mem_avail_gib" in s]
    k = max(1, len(mem) // 10) if mem else 1
    start_decile = (sum(mem[:k]) / k) if mem else None
    end_decile = (sum(mem[-k:]) / k) if mem else None
    # positive growth => MemAvailable fell over the run (pool pressure / shrink)
    growth = (
        (start_decile - end_decile) if (start_decile is not None and end_decile is not None) else None
    )
    temps = [s["gpu_temp"] for s in samples if "gpu_temp" in s]
    clocks = [s["sm_mhz"] for s in samples if "sm_mhz" in s]
    n_err = len(ERRORS)
    n_done = COMPLETED[0]

    mem_shrink_pct = None
    mem_finding = False
    if growth is not None and start_decile and start_decile > 0:
        mem_shrink_pct = 100.0 * growth / start_decile
        if mem_shrink_pct > a.mem_shrink_pct:
            mem_finding = True

    report = {
        "minutes": a.minutes,
        "concurrency": a.concurrency,
        "completed": n_done,
        "errors": ERRORS,
        "error_count": n_err,
        "max_errors": a.max_errors,
        "mem_avail_start_gib": mem[0] if mem else None,
        "mem_avail_end_gib": mem[-1] if mem else None,
        "mem_shrink_gib": round(growth, 2) if growth is not None else None,
        "mem_shrink_pct": round(mem_shrink_pct, 2) if mem_shrink_pct is not None else None,
        "mem_finding": mem_finding,
        "gpu_temp_max": max(temps) if temps else None,
        "sm_clock_min": min(clocks) if clocks else None,
        "samples": samples,
    }

    summary = {k: v for k, v in report.items() if k not in ("samples", "errors")}
    summary["error_count"] = n_err
    print(json.dumps(summary, indent=1))
    if a.out:
        json.dump(report, open(a.out, "w"), indent=1)
        print(f"wrote {a.out}")

    # --- verdict ---
    reasons = []
    if n_done <= 0:
        reasons.append("completed=0")
    if n_err > a.max_errors:
        reasons.append(f"errors={n_err}>{a.max_errors}")
    if mem_finding and a.fail_on_mem_shrink:
        reasons.append(
            f"mem_shrink={mem_shrink_pct:.1f}%>{a.mem_shrink_pct}% (--fail-on-mem-shrink)"
        )

    if mem_finding and not a.fail_on_mem_shrink:
        print(
            f"WARN soak mem_shrink_gib={report['mem_shrink_gib']} "
            f"({mem_shrink_pct:.1f}% of start decile) — reviewed finding, not auto-fail "
            f"(pass --fail-on-mem-shrink to fail)",
            flush=True,
        )

    if reasons:
        print(
            f"FAIL soak  completed={n_done}  errors={n_err}  "
            f"mem_shrink_gib={report['mem_shrink_gib']}  reason={';'.join(reasons)}",
            flush=True,
        )
        sys.exit(1)

    print(
        f"PASS soak  completed={n_done}  errors={n_err}  "
        f"mem_shrink_gib={report['mem_shrink_gib']}  "
        f"temp_max={report['gpu_temp_max']}  sm_min={report['sm_clock_min']}",
        flush=True,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
