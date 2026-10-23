"""What does a WRONG prefetch cost? (Linux only; run as root for cache eviction.)

Measures, with the daemon's real prefetch routine (chunked WILLNEED):
  E1  load time of a wrongly-chosen file: from issuing the prefetch until 100% resident
  E2  latency of the file the app actually needs, cold, with no prefetch      (L_none)
  E3  same file when the prediction was RIGHT (it was prefetched in time)     (L_right)
  E4  same file when the prediction was WRONG (K wrong files are loading)     (L_wrong)
      -> misprediction penalty = L_wrong - L_none, swept over the gap `d`
         between the wrong prefetch and the real read (think time hides it)

    sudo python3 misprediction_cost.py [--files-dir /tmp] [--trials 20] [--k 2]
"""
import argparse
import ctypes
import json
import os
import statistics
import sys
import time

from prefetch_actuator import _fadvise, FADV_DONTNEED, FADV_WILLNEED

libc = ctypes.CDLL("libc.so.6")
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_longlong]
libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]


def residency(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        addr = libc.mmap(None, size, 1, 1, fd, 0)
        n = (size + 4095) // 4096
        vec = ctypes.create_string_buffer(n)
        libc.mincore(addr, size, vec)
        libc.munmap(addr, size)
        return vec.raw.count(1) / n
    finally:
        os.close(fd)


def evict(*paths):
    for p in paths:
        _fadvise(p, FADV_DONTNEED)


def read_all(path):
    t0 = time.perf_counter()
    fd = os.open(path, os.O_RDONLY)
    try:
        while os.read(fd, 1 << 20):
            pass
    finally:
        os.close(fd)
    return (time.perf_counter() - t0) * 1000.0


def prefetch(*paths):
    for p in paths:
        _fadvise(p, FADV_WILLNEED)


def stats(xs):
    xs = sorted(xs)
    return {"mean": round(statistics.mean(xs), 2), "p50": round(xs[len(xs) // 2], 2),
            "p95": round(xs[min(len(xs) - 1, int(len(xs) * .95))], 2), "n": len(xs)}


def pick(files_dir, n):
    names = sorted(f for f in os.listdir(files_dir) if f.startswith("builder_") and f.endswith(".dat"))
    if len(names) < n:
        sys.exit(f"need {n} builder_*.dat files in {files_dir} (python3 workload.py --setup)")
    return [os.path.join(files_dir, f) for f in names[:n]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files-dir", default="/tmp")
    ap.add_argument("--trials", type=int, default=20)
    ap.add_argument("--k", type=int, default=2, help="wrong files prefetched per misprediction (daemon top-k)")
    ap.add_argument("--gaps-ms", default="0,25,50,100,200")
    args = ap.parse_args()
    k, T = args.k, args.trials
    files = pick(args.files_dir, T * (k + 1))
    size_mb = os.path.getsize(files[0]) / 2**20
    out = {"file_mb": size_mb, "k_wrong": k, "trials": T}

    # Warm any host/virtio-level cache so every measured read is a steady "cold-in-guest" read
    # (otherwise first-touch files look slower than re-read ones and penalties go negative).
    for p in files:
        read_all(p)
    evict(*files)
    time.sleep(0.5)

    # E1: load time of one wrong file (issue -> fully resident)
    load = []
    for i in range(T):
        w = files[i]
        evict(w)
        time.sleep(0.05)
        t0 = time.perf_counter()
        prefetch(w)
        issue = (time.perf_counter() - t0) * 1000
        while residency(w) < 0.999 and time.perf_counter() - t0 < 10:
            time.sleep(0.0005)
        load.append((time.perf_counter() - t0) * 1000)
    out["E1_wrong_file_time_to_resident_ms"] = stats(load)
    out["E1_effective_MBps"] = round(size_mb / (statistics.mean(load) / 1000), 1)

    # E2/E3: needed file, no prefetch vs correctly prefetched
    none, right = [], []
    for i in range(T):
        a = files[i]
        evict(a)
        time.sleep(0.05)
        none.append(read_all(a))
        evict(a)
        prefetch(a)
        while residency(a) < 0.999:
            time.sleep(0.0005)
        right.append(read_all(a))
    out["E2_needed_file_no_prefetch_ms"] = stats(none)
    out["E3_needed_file_prefetched_right_ms"] = stats(right)

    # E4: needed file when k wrong files were just prefetched; sweep gap d
    out["E4_needed_file_after_wrong_prefetch_ms"] = {}
    out["E4_penalty_vs_no_prefetch_ms"] = {}
    base = statistics.mean(none)
    for d in [float(x) for x in args.gaps_ms.split(",")]:
        lat = []
        for i in range(T):
            a = files[i]
            ws = files[T + i * k: T + (i + 1) * k]
            evict(a, *ws)
            time.sleep(0.05)
            prefetch(*ws)                      # predictor chose wrong
            time.sleep(d / 1000.0)             # time until the app really asks for A
            lat.append(read_all(a))
        s = stats(lat)
        out["E4_needed_file_after_wrong_prefetch_ms"][f"gap_{int(d)}ms"] = s
        out["E4_penalty_vs_no_prefetch_ms"][f"gap_{int(d)}ms"] = {
            "mean_ms": round(s["mean"] - base, 2),
            "pct": round(100 * (s["mean"] - base) / base, 1)}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
