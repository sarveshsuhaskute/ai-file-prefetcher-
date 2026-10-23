"""Real-data workload: replaces the synthetic, perfectly cyclic benchmark.

Three concurrent actors operate on the machine's real files (no setup step):
  indexer  - repeatedly walks /usr/share in directory order, reading each file
  scanner  - repeatedly walks /usr/lib, reading each file's first 256 KB
  compiler - picks a random C header and reads it plus everything it #includes
             (depth-first over the real /usr/include dependency graph)
Real file sizes, real directory layout, real branching. Use --ballast-gb in
eval_runner.py to shrink available RAM so the working set exceeds the page cache.

Prints the same summary lines as workload.py so eval_runner.py can parse it.
"""
import argparse
import multiprocessing
import os
import random
import re
import time

from workload import check_incore, set_comm

INCLUDE_RE = re.compile(rb'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.M)
INCLUDE_DIRS = ["/usr/include", "/usr/include/x86_64-linux-gnu", "/usr/include/aarch64-linux-gnu"]


def read_file(path, cap):
    """Open+read `cap` bytes; returns (residency_before_read, latency_ms, data) or None."""
    try:
        t0 = time.perf_counter()
        fd = os.open(path, os.O_RDONLY)
        try:
            size = min(os.fstat(fd).st_size, cap)
            hit = check_incore(fd, size) if size else 1.0
            data = os.read(fd, cap)
        finally:
            os.close(fd)
        return hit, (time.perf_counter() - t0) * 1000.0, data
    except OSError:
        return None


def walk_files(root):
    for d, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(d, f)
            if os.path.isfile(p) and not os.path.islink(p):
                yield p


def resolve_include(name):
    for d in INCLUDE_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def header_closure(root, seen, record, limit=40):
    """DFS over #include edges (real branching access pattern)."""
    order, stack = [], [root]
    while stack and len(order) < limit:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        order.append(p)
        r = read_file(p, 1 << 20)
        record(r)                                   # the one real read is the measured read
        time.sleep(0.001)
        if r:
            for m in reversed(INCLUDE_RE.findall(r[2])):
                q = resolve_include(m.decode("utf-8", "replace"))
                if q:
                    stack.append(q)
    return order


def bounded_files(root, n):
    files = []
    for p in walk_files(root):
        files.append(p)
        if len(files) >= n:
            break
    return files


def actor(name, duration, out, scale_cap, max_files):
    set_comm(name)
    random.seed(os.getpid())
    start, end = time.time(), time.time() + duration
    samples = []                                   # (bucket, hit, latency_ms)

    def record(r):
        if r:
            samples.append((int((time.time() - start) // 5), r[0], r[1]))

    if name == "compiler":
        headers = [p for p in walk_files("/usr/include") if p.endswith(".h")]
        while time.time() < end and headers:
            header_closure(random.choice(headers), set(), record)
    else:
        root, cap = ("/usr/share", scale_cap) if name == "indexer" else ("/usr/lib", 256 << 10)
        files = bounded_files(root, max_files)      # fixed set => repeated passes (learnable)
        while time.time() < end:
            for p in files:
                if time.time() >= end:
                    break
                record(read_file(p, cap))
                time.sleep(0.001)
    out.append((name, samples))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("duration", nargs="?", type=int, default=40)
    ap.add_argument("--file-cap-kb", type=int, default=8192, help="max bytes read per file for the indexer")
    ap.add_argument("--max-files", type=int, default=15000, help="files per walker actor (repeated passes)")
    ap.add_argument("--ws", action="store_true", help="print working-set size and exit")
    args = ap.parse_args()

    if args.ws:
        tot = 0
        for root, cap in (("/usr/share", args.file_cap_kb << 10), ("/usr/lib", 256 << 10)):
            tot += sum(min(os.path.getsize(p), cap) for p in bounded_files(root, args.max_files))
        print(f"walker working set: {tot / 2**20:.0f} MB")
        return

    mgr = multiprocessing.Manager()
    out = mgr.list()
    procs = [multiprocessing.Process(target=actor, args=(n, args.duration, out, args.file_cap_kb << 10, args.max_files))
             for n in ("indexer", "scanner", "compiler")]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    lat, hits, reads = [], 0.0, 0
    for _, samples in out:
        for _, h, l in samples:
            hits += h
            reads += 1
            lat.append(l)
    lat.sort()
    print(f"RAW DATA -> Total Hits: {hits:.1f}, Total Reads: {reads}")
    print(f"Overall Cache Hit Rate: {100 * hits / max(reads, 1):.2f}% (Total Reads: {reads})")
    if lat:
        print(f"Read Latency ms: mean={sum(lat) / len(lat):.2f} p50={lat[len(lat) // 2]:.2f} "
              f"p95={lat[int(len(lat) * .95)]:.2f} p99={lat[int(len(lat) * .99)]:.2f}")


if __name__ == "__main__":
    main()
