import os
import ctypes
import time
import multiprocessing
import sys

libc = ctypes.CDLL("libc.so.6")

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

def worker_builder(duration, stats_queue):
    try:
        set_comm("builder")
        start_time = time.time()
        end = start_time + duration
        shift_time = start_time + (duration / 2.0)
        hits = []
        i = 0
        while time.time() < end:
            current_time = time.time()
            if current_time < shift_time:
                f = f"/tmp/builder_{i % 100}.dat"
            else:
                f = f"/tmp/builder_{100 + (i % 100)}.dat"
                
            bucket = int((current_time - start_time) // 5)
            hits.append((bucket, do_read(f)))
            time.sleep(0.01)
            i += 1
        stats_queue.append(("builder", hits))
    except BaseException as e:
        print(f"Builder worker error: {e}")

def worker_tailer(duration, stats_queue):
    try:
        set_comm("logger")
        start_time = time.time()
        end = start_time + duration
        hits = []
        i = 0
        while time.time() < end:
            current_time = time.time()
            f = f"/tmp/tailer_{i % 80}.dat"
            bucket = int((current_time - start_time) // 5)
            hits.append((bucket, do_read(f)))
            time.sleep(0.01)
            i += 1
        stats_queue.append(("logger", hits))
    except BaseException as e:
        print(f"Tailer worker error: {e}")

def worker_processor(duration, stats_queue):
    try:
        set_comm("processor")
        start_time = time.time()
        end = start_time + duration
        hits = []
        i = 0
        while time.time() < end:
            current_time = time.time()
            f = f"/tmp/processor_{i % 120}.dat"
            bucket = int((current_time - start_time) // 5)
            hits.append((bucket, do_read(f)))
            time.sleep(0.01)
            i += 1
        stats_queue.append(("processor", hits))
    except BaseException as e:
        print(f"Processor worker error: {e}")

def setup_files():
    print("Creating 400 test files (4 GB)... This takes ~3 seconds.")
    chunk = os.urandom(10 * 1024 * 1024)
    for i in range(200):
        with open(f"/tmp/builder_{i}.dat", "wb") as out: out.write(chunk)
    for i in range(80):
        with open(f"/tmp/tailer_{i}.dat", "wb") as out: out.write(chunk)
    for i in range(120):
        with open(f"/tmp/processor_{i}.dat", "wb") as out: out.write(chunk)

def main():
    if "--setup" in sys.argv:
        setup_files()
        sys.exit(0)
        
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 40 
    
    manager = multiprocessing.Manager()
    results = manager.list()
    
    p1 = multiprocessing.Process(target=worker_builder, args=(duration, results))
    p2 = multiprocessing.Process(target=worker_tailer, args=(duration, results))
    p3 = multiprocessing.Process(target=worker_processor, args=(duration, results))
    
    p1.start()
    p2.start()
    p3.start()
    
    p1.join()
    p2.join()
    p3.join()
    
    print(f"DEBUG: results has {len(results)} items")
        
    bucket_hits = {}
    bucket_reads = {}
    
    for name, hits in results:
        for bucket, hit_rate in hits:
            if bucket not in bucket_hits:
                bucket_hits[bucket] = 0.0
                bucket_reads[bucket] = 0
            bucket_hits[bucket] += hit_rate
            bucket_reads[bucket] += 1
            
    print("--- TIME SERIES HIT RATE ---")
    buckets = sorted(bucket_hits.keys())
    for b in buckets:
        avg = (bucket_hits[b] / bucket_reads[b]) * 100 if bucket_reads[b] else 0
        print(f"[{b*5}s - {(b+1)*5}s]: {avg:.2f}% (Reads: {bucket_reads[b]})")
        
    total_hits = sum(bucket_hits.values())
    total_reads = sum(bucket_reads.values())
    overall_avg = (total_hits / total_reads) * 100 if total_reads else 0
    print(f"RAW DATA -> Total Hits: {total_hits:.1f}, Total Reads: {total_reads}")
    print(f"Overall Cache Hit Rate: {overall_avg:.2f}% (Total Reads: {total_reads})")

if __name__ == "__main__":
    main()
