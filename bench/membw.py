"""GPU memory bandwidth microbenchmark for GB10 (unified LPDDR5X).

Measures device-to-device copy and triad-style read bandwidth with large
buffers, sized well past cache, reporting the median of timed iterations.
"""
import torch

def bench(fn, iters=20, warmup=5):
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    times = []
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    for _ in range(iters):
        start.record()
        fn()
        end.record()
        torch.cuda.synchronize()
        times.append(start.elapsed_time(end) / 1000.0)
    times.sort()
    return times[len(times) // 2]

def main():
    torch.cuda.init()
    n = 2 * 1024**3  # 2 GiB per buffer
    a = torch.empty(n, dtype=torch.uint8, device="cuda")
    b = torch.empty(n, dtype=torch.uint8, device="cuda")
    a.fill_(1)

    # D2D copy: reads n + writes n
    t = bench(lambda: b.copy_(a))
    print(f"d2d_copy: {2 * n / t / 1e9:.1f} GB/s")

    # Reduction (read-dominated): reads n bytes
    af = a.view(torch.float32)
    t = bench(lambda: torch.sum(af))
    print(f"read_sum: {n / t / 1e9:.1f} GB/s")

    # Scale (read n + write n), float32
    bf = b.view(torch.float32)
    t = bench(lambda: torch.mul(af, 2.0, out=bf))
    print(f"scale:    {2 * n / t / 1e9:.1f} GB/s")

if __name__ == "__main__":
    main()
