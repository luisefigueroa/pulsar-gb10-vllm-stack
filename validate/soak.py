#!/usr/bin/env python3
"""Load soak: sustained concurrent traffic + resource sampling.

  validate/soak.py --url http://127.0.0.1:8000 --model NAME \
      --minutes 30 --concurrency 8 [--out results/soak-x.json]

Runs a closed-loop pool of workers with mixed prompt lengths, samples
MemAvailable/GPU temp/power every 30 s, counts errors, and reports memory
growth (first-decile vs last-decile of samples) at the end. Any error or
>5% memory-pool shrink over the run is a finding.
"""
import argparse, json, random, subprocess, threading, time
import urllib.request

STOP = False
ERRORS = []
COMPLETED = [0]
LOCK = threading.Lock()

def worker(url, model, wid):
    rng = random.Random(wid)
    while not STOP:
        n_in = rng.choice([64, 256, 1024, 4096])
        n_out = rng.choice([64, 256, 512])
        words = "one two three four five six seven eight nine ten".split()
        prompt = f"[w{wid}-{rng.randrange(1<<30)}] " + " ".join(rng.choice(words) for _ in range(n_in))
        body = {"model": model, "prompt": prompt, "max_tokens": n_out,
                "temperature": 0.7, "seed": rng.randrange(1 << 31)}
        try:
            req = urllib.request.Request(url + "/v1/completions",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=1800) as r:
                json.load(r)
            with LOCK:
                COMPLETED[0] += 1
        except Exception as e:
            with LOCK:
                ERRORS.append(f"{time.strftime('%H:%M:%S')} w{wid}: {type(e).__name__} {e}")

def sample():
    out = {"t": time.time()}
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                out["mem_avail_gib"] = round(int(line.split()[1]) / 1048576, 2)
    except Exception:
        pass
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,power.draw,clocks.sm",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        t, p, c = [x.strip() for x in q.stdout.strip().split(",")]
        out.update(gpu_temp=int(t), power_w=None if "N/A" in p else float(p), sm_mhz=int(c))
    except Exception:
        pass
    return out

def main():
    global STOP
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--model", required=True)
    ap.add_argument("--minutes", type=float, required=True)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    threads = [threading.Thread(target=worker, args=(a.url, a.model, i), daemon=True)
               for i in range(a.concurrency)]
    for t in threads:
        t.start()
    samples = []
    t_end = time.time() + a.minutes * 60
    while time.time() < t_end:
        s = sample()
        samples.append(s)
        with LOCK:
            done, errs = COMPLETED[0], len(ERRORS)
        print(f"{time.strftime('%H:%M:%S')} done={done} errors={errs} "
              f"mem_avail={s.get('mem_avail_gib')}GiB temp={s.get('gpu_temp')}C "
              f"sm={s.get('sm_mhz')}MHz", flush=True)
        time.sleep(30)
    STOP = True
    time.sleep(5)

    mem = [s["mem_avail_gib"] for s in samples if "mem_avail_gib" in s]
    k = max(1, len(mem) // 10)
    growth = (sum(mem[:k]) / k) - (sum(mem[-k:]) / k)
    temps = [s["gpu_temp"] for s in samples if "gpu_temp" in s]
    clocks = [s["sm_mhz"] for s in samples if "sm_mhz" in s]
    report = {
        "minutes": a.minutes, "concurrency": a.concurrency,
        "completed": COMPLETED[0], "errors": ERRORS,
        "mem_avail_start_gib": mem[0] if mem else None,
        "mem_avail_end_gib": mem[-1] if mem else None,
        "mem_shrink_gib": round(growth, 2),
        "gpu_temp_max": max(temps) if temps else None,
        "sm_clock_min": min(clocks) if clocks else None,
        "samples": samples,
    }
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=1))
    if a.out:
        json.dump(report, open(a.out, "w"), indent=1)
        print(f"wrote {a.out}")

if __name__ == "__main__":
    main()
