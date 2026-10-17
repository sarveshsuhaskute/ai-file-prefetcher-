"""How much of a file does one prefetch request actually load? (Linux only; run as root.)

For a 10 MB file evicted from the page cache, issue each request style and measure residency
with mincore() 600 ms later. Writes docs/raw/willneed_probe.json.

    sudo python3 willneed_probe.py [--file /tmp/builder_7.dat] [--trials 5]
"""
import argparse
import ctypes
import json
import os
import platform
import statistics
import time

from misprediction_cost import residency

libc = ctypes.CDLL("libc.so.6")
libc.readahead.argtypes = [ctypes.c_int, ctypes.c_longlong, ctypes.c_size_t]
WILLNEED, DONTNEED = os.POSIX_FADV_WILLNEED, os.POSIX_FADV_DONTNEED
MB = 1 << 20


def evict(path):
    fd = os.open(path, os.O_RDONLY)
    os.posix_fadvise(fd, 0, 0, DONTNEED)
    os.close(fd)


STYLES = [
    ("posix_fadvise|whole file", False, lambda fd, s: os.posix_fadvise(fd, 0, s, WILLNEED)),
    ("readahead(2)|whole file", False, lambda fd, s: libc.readahead(fd, 0, s)),
    ("posix_fadvise|1 MB chunks", True, lambda fd, s: [os.posix_fadvise(fd, o, MB, WILLNEED) for o in range(0, s, MB)]),
    ("readahead(2)|1 MB chunks", True, lambda fd, s: [libc.readahead(fd, o, MB) for o in range(0, s, MB)]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="/tmp/builder_7.dat")
    ap.add_argument("--trials", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "raw", "willneed_probe.json"))
    args = ap.parse_args()
    size = os.path.getsize(args.file)
    results = []
    for label, chunked, fn in STYLES:
        vals = []
        for _ in range(args.trials):
            evict(args.file)
            time.sleep(0.2)
            fd = os.open(args.file, os.O_RDONLY | os.O_NONBLOCK)
            fn(fd, size)
            os.close(fd)
            time.sleep(0.6)
            vals.append(residency(args.file))
        results.append({"label": label, "chunked": chunked, "resident": statistics.mean(vals),
                        "min": min(vals), "max": max(vals), "trials": args.trials})
        print(f"{label:28} resident {100 * statistics.mean(vals):5.1f}%  (min {100 * min(vals):.1f}, max {100 * max(vals):.1f})")
    with open("/sys/block/vda/queue/read_ahead_kb") as fh:
        ra = fh.read().strip()
    json.dump({"file": args.file, "file_mb": size / MB, "kernel": platform.release(), "read_ahead_kb": ra,
               "results": results}, open(args.out, "w"), indent=2)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
