import os
import ctypes
import time
import argparse
import multiprocessing
import random
import sys

libc = ctypes.CDLL("libc.so.6" if sys.platform.startswith("linux") else None)  # workload itself needs Linux (mincore/prctl)

def set_comm(name):
    libc.prctl(15, name.encode()[:15], 0, 0, 0)

def check_incore(fd, size):
    if size == 0: return 1.0
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_longlong]
    addr = libc.mmap(None, size, 1, 1, fd, 0)
    if addr == -1 or addr == 0xFFFFFFFFFFFFFFFF or addr == 0:
        return 0.0
    page_size = os.sysconf("SC_PAGE_SIZE")
    num_pages = (size + page_size - 1) // page_size
    vec = ctypes.create_string_buffer(num_pages)
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
    libc.mincore(addr, size, vec)
    resident_pages = sum(1 for i in range(num_pages) if vec[i][0] & 1)
    
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    libc.munmap(addr, size)
    return resident_pages / num_pages

def do_read(filepath):
    size = os.path.getsize(filepath)
    fd = os.open(filepath, os.O_RDONLY)
    hit_rate = check_incore(fd, size)
    with os.fdopen(fd, "rb") as f:
        f.read()
    return hit_rate

def counts(scale):
    """Per-actor working-set sizes (files). scale=1.0 -> 400 files = 4 GB."""
    return int(100 * scale), int(80 * scale), int(120 * scale)


def pick(i, n, noise):
    """Next file index: the fixed cyclic order, or a random jump with prob `noise`."""
    return random.randrange(n) if noise and random.random() < noise else i % n


def run_actor(name, prefix, n, duration, stats_queue, noise, shift_to=None):
    try:
        set_comm(name)
        random.seed(os.getpid())
        start = time.time()
        end = start + duration
        shift_time = start + duration / 2.0
        hits, i = [], 0
        while time.time() < end:
            now = time.time()
            base = n if (shift_to and now >= shift_time) else 0   # context shift at t/2
            f = f"/tmp/{prefix}_{base + pick(i, n, noise)}.dat"
            hits.append((int((now - start) // 5), do_read(f)))
            time.sleep(0.01)
            i += 1
        stats_queue.append((name, hits))
    except BaseException as e:
        print(f"{name} worker error: {e}")


def setup_files(scale):
    b, t, p = counts(scale)
    total = 2 * b + t + p
    print(f"Creating {total} test files ({total * 10 / 1024:.1f} GB)...")
    chunk = os.urandom(10 * 1024 * 1024)
    for prefix, n in (("builder", 2 * b), ("tailer", t), ("processor", p)):
        for i in range(n):
            with open(f"/tmp/{prefix}_{i}.dat", "wb") as out:
                out.write(chunk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("duration", nargs="?", type=int, default=40)
    ap.add_argument("--setup", action="store_true", help="create the data files and exit")
    ap.add_argument("--scale", type=float, default=1.0, help="working-set multiplier (3.5 ~ 14 GB)")
    ap.add_argument("--noise", type=float, default=0.0, help="probability of a random (unpredictable) access")
    args = ap.parse_args()

    if args.setup:
        setup_files(args.scale)
        return

    b, t, p = counts(args.scale)
    manager = multiprocessing.Manager()
    results = manager.list()
    procs = [
        multiprocessing.Process(target=run_actor, args=("builder", "builder", b, args.duration, results, args.noise, True)),
        multiprocessing.Process(target=run_actor, args=("logger", "tailer", t, args.duration, results, args.noise)),
        multiprocessing.Process(target=run_actor, args=("processor", "processor", p, args.duration, results, args.noise)),
    ]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join()

    bucket_hits, bucket_reads = {}, {}
    for _, hits in results:
        for bucket, hr in hits:
            bucket_hits[bucket] = bucket_hits.get(bucket, 0.0) + hr
            bucket_reads[bucket] = bucket_reads.get(bucket, 0) + 1

    print("--- TIME SERIES HIT RATE ---")
    for bk in sorted(bucket_hits):
        avg = bucket_hits[bk] / bucket_reads[bk] * 100
        print(f"[{bk * 5}s - {(bk + 1) * 5}s]: {avg:.2f}% (Reads: {bucket_reads[bk]})")
    total_hits, total_reads = sum(bucket_hits.values()), sum(bucket_reads.values())
    overall = total_hits / total_reads * 100 if total_reads else 0
    print(f"RAW DATA -> Total Hits: {total_hits:.1f}, Total Reads: {total_reads}")
    print(f"Overall Cache Hit Rate: {overall:.2f}% (Total Reads: {total_reads})")


if __name__ == "__main__":
    main()
