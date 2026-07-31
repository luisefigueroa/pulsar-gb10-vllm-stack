#!/usr/bin/env python3
"""Summarize a torch-profiler Chrome trace from a vLLM worker.

  validate/analyze_trace.py <trace.json[.gz]> [--annotation-filter STR]

Reports, over the trace's busiest window:
  - step/round annotations found (vLLM record_function markers)
  - per-round wall time, GPU-busy time, GPU-idle (host-bound) time
  - NCCL kernel time share
  - top CUDA kernels by total duration
  - cuda_runtime sync/memcpy calls (host stall indicators)
"""
import argparse, gzip, json, sys
from collections import defaultdict


def load(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt") as f:
        return json.load(f)


def union_len(intervals):
    if not intervals:
        return 0.0
    intervals.sort()
    total, cs, ce = 0.0, intervals[0][0], intervals[0][1]
    for s, e in intervals[1:]:
        if s > ce:
            total += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    return total + (ce - cs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--annotation-filter", default="")
    ap.add_argument("--top", type=int, default=18)
    a = ap.parse_args()

    data = load(a.trace)
    events = data["traceEvents"] if isinstance(data, dict) else data
    X = [e for e in events if e.get("ph") == "X" and "dur" in e]

    kernels = [e for e in X if e.get("cat") in ("kernel", "gpu_op")]
    runtime = [e for e in X if e.get("cat") == "cuda_runtime"]
    annots = [e for e in X if e.get("cat") == "user_annotation"]

    print(f"events: {len(X)}  kernels: {len(kernels)}  "
          f"runtime: {len(runtime)}  annotations: {len(annots)}")

    # annotation inventory
    by_name = defaultdict(lambda: [0, 0.0])
    for e in annots:
        by_name[e["name"]][0] += 1
        by_name[e["name"]][1] += e["dur"]
    print("\n== annotations (count, total ms):")
    for n, (c, d) in sorted(by_name.items(), key=lambda kv: -kv[1][1])[:12]:
        print(f"  {c:5d}  {d/1000:9.1f}  {n[:90]}")

    # pick round markers
    marks = [e for e in annots if a.annotation_filter and a.annotation_filter in e["name"]]
    if not marks and annots:
        biggest = max(by_name.items(), key=lambda kv: kv[1][1])[0]
        marks = [e for e in annots if e["name"] == biggest]
        print(f"\n(using annotation {biggest!r} as the round marker)")
    marks.sort(key=lambda e: e["ts"])
    # steady state: drop first 2 and last 1
    marks = marks[2:-1] if len(marks) > 4 else marks

    if marks:
        walls, busys, nccls = [], [], []
        for m in marks:
            s, e = m["ts"], m["ts"] + m["dur"]
            ks = [(k["ts"], k["ts"] + k["dur"]) for k in kernels if k["ts"] >= s and k["ts"] < e]
            nc = sum(k["dur"] for k in kernels
                     if k["ts"] >= s and k["ts"] < e and "nccl" in k["name"].lower())
            walls.append(m["dur"] / 1000)
            busys.append(union_len(ks) / 1000)
            nccls.append(nc / 1000)
        n = len(marks)
        print(f"\n== per-round (n={n}, steady state):")
        print(f"  wall      : {sum(walls)/n:8.2f} ms")
        print(f"  GPU busy  : {sum(busys)/n:8.2f} ms")
        print(f"  GPU idle  : {sum(walls)/n - sum(busys)/n:8.2f} ms  <-- host-bound if large")
        print(f"  NCCL time : {sum(nccls)/n:8.2f} ms (sum of nccl kernel durs; overlaps possible)")

    # top kernels overall
    agg = defaultdict(lambda: [0, 0.0])
    for k in kernels:
        agg[k["name"]][0] += 1
        agg[k["name"]][1] += k["dur"]
    print(f"\n== top kernels (count, total ms):")
    for name, (c, d) in sorted(agg.items(), key=lambda kv: -kv[1][1])[: a.top]:
        print(f"  {c:6d}  {d/1000:9.1f}  {name[:88]}")

    # host stall indicators
    ragg = defaultdict(lambda: [0, 0.0])
    for r in runtime:
        ragg[r["name"]][0] += 1
        ragg[r["name"]][1] += r["dur"]
    print(f"\n== cuda_runtime calls (count, total ms):")
    for name, (c, d) in sorted(ragg.items(), key=lambda kv: -kv[1][1])[:10]:
        print(f"  {c:6d}  {d/1000:9.1f}  {name[:88]}")


if __name__ == "__main__":
    main()
