"""Memory-aware prefetch actuation.

Fixes the two failure modes diagnosed in docs/final_evaluation_report.md:
  * eviction blindness -> prefetching is throttled by real memory pressure
    (/proc/meminfo) and useless prefetches are reclaimed with DONTNEED;
  * per-event overhead -> top-k fan-out, de-duplication cooldown, and
    fadvise calls moved off the event-consumer thread.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Linux values as fallback so the module (and tests) import on macOS too.
FADV_WILLNEED = getattr(os, "POSIX_FADV_WILLNEED", 3)
FADV_DONTNEED = getattr(os, "POSIX_FADV_DONTNEED", 4)


class PrecisionTracker:
    def __init__(self, window=2.0):
        self.window = window
        self.prefetches = {}            # file -> issue time
        self.useful = 0
        self.total = 0
        self.evictions = 0
        self._lock = threading.Lock()

    def record_prefetch(self, f):
        with self._lock:
            self.prefetches[f] = time.monotonic()
            self.total += 1

    def record_read(self, f):
        with self._lock:
            t = self.prefetches.pop(f, None)
            if t is not None and time.monotonic() - t <= self.window:
                self.useful += 1

    def pop_stale(self):
        """Prefetched but never read within the window: candidates to reclaim."""
        cutoff = time.monotonic() - self.window
        with self._lock:
            stale = [f for f, t in self.prefetches.items() if t < cutoff]
            for f in stale:
                del self.prefetches[f]
        return stale

    def print_stats(self):
        prec = 100.0 * self.useful / self.total if self.total else 0.0
        print(f"\n[Precision Stats] Useful Prefetches: {self.useful} / {self.total} ({prec:.1f}%)")
        print(f"[Precision Stats] Total DONTNEED Evictions: {self.evictions}")


class MemoryMonitor:
    """Memory-pressure signal for throttling.

    Pressure if ANY of:
      * reclaim throughput >= reclaim_mb_s: pages stolen by kswapd/direct reclaim
        per second (/proc/vmstat pgsteal_*). This is the signal that tracks
        page-cache churn: measured ~1 GB/s when a 10 GB working set is squeezed
        into ~3-7 GB of cache, ~0 when everything fits.
      * PSI memory "some avg10" >= psi_threshold (task stalls; catches direct
        reclaim / swap, but stayed ~0 under pure clean-cache churn).
      * MemAvailable < low_frac * MemTotal (anonymous-memory exhaustion only;
        MemAvailable counts reclaimable cache, so it never trips on churn).
    """

    def __init__(self, psi_threshold=5.0, low_frac=0.10, ttl=0.25, reclaim_mb_s=200.0,
                 meminfo="/proc/meminfo", psi="/proc/pressure/memory", vmstat="/proc/vmstat"):
        self.psi_threshold = psi_threshold
        self.reclaim_mb_s = reclaim_mb_s
        self.vmstat = vmstat
        self._vm_prev = None            # (time, pages_reclaimed)
        self._reclaim_rate = 0.0
        self.last_reclaim_mb_s = None
        self.low_frac = low_frac
        self.ttl = ttl
        self.meminfo = meminfo
        self.psi = psi
        self._t = -1e9
        self._pressure = False
        self.last_psi = None

    def under_pressure(self):
        now = time.monotonic()
        if now - self._t >= self.ttl:
            self._t = now
            self._pressure = bool(self._read_reclaim() | self._read_psi() | self._read_meminfo())
        return self._pressure

    def _read_reclaim(self):
        """MB/s reclaimed, recomputed at most once per second."""
        try:
            now = time.monotonic()
            if self._vm_prev is not None and now - self._vm_prev[0] < 1.0:
                return self._reclaim_rate >= self.reclaim_mb_s
            pages = 0
            with open(self.vmstat) as fh:
                for line in fh:
                    if line.startswith("pgsteal_"):
                        pages += int(line.split()[1])
            if self._vm_prev is not None:
                dt = now - self._vm_prev[0]
                self._reclaim_rate = (pages - self._vm_prev[1]) * 4096 / 1048576 / dt
                self.last_reclaim_mb_s = self._reclaim_rate
            self._vm_prev = (now, pages)
            return self._reclaim_rate >= self.reclaim_mb_s
        except (OSError, ValueError):
            return False

    def _read_psi(self):
        try:
            with open(self.psi) as fh:
                for line in fh:
                    if line.startswith("some"):
                        fields = dict(kv.split("=") for kv in line.split()[1:])
                        self.last_psi = float(fields["avg10"])
                        return self.last_psi >= self.psi_threshold
        except (OSError, KeyError, ValueError):
            self.last_psi = None
        return False

    def _read_meminfo(self):
        try:
            vals = {}
            with open(self.meminfo) as fh:
                for line in fh:
                    k, v = line.split(":", 1)
                    if k in ("MemTotal", "MemAvailable"):
                        vals[k] = int(v.split()[0])
                    if len(vals) == 2:
                        break
            return vals["MemAvailable"] < self.low_frac * vals["MemTotal"]
        except (OSError, KeyError, ValueError):
            return False                 # unknown -> don't block prefetching


WILLNEED_CHUNK = 1 << 20         # see _fadvise
MAX_PREFETCH_BYTES = 64 << 20    # don't pull in more than this per file


def _fadvise(path, advice, max_bytes=MAX_PREFETCH_BYTES):
    """posix_fadvise(WILLNEED) over a whole file only loads ~1.2 MB on Linux 6.8
    (measured: 12% of a 10 MB file, with or without readahead(2)); the rest is
    silently ignored. Issuing it in 1 MB chunks loads the full range (measured 100%),
    so large-file prefetches work only when chunked."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if advice == FADV_WILLNEED:
            size = min(os.fstat(fd).st_size, max_bytes)
            for off in range(0, size, WILLNEED_CHUNK):
                os.posix_fadvise(fd, off, min(WILLNEED_CHUNK, size - off), advice)
        else:
            os.posix_fadvise(fd, 0, 0, advice)
    finally:
        os.close(fd)


class Actuator:
    def __init__(self, tracker, memory=None, hi=0.5, top_k=2, cooldown=5.0,
                 workers=2, max_queue=256, advise=_fadvise):
        self.tracker = tracker
        self.memory = memory or MemoryMonitor()
        self.hi = hi
        self.top_k = top_k
        self.cooldown = cooldown
        self.max_queue = max_queue
        self._advise = advise
        self._recent = {}               # file -> last prefetch time
        self._pending = 0
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fadvise")
        self.stats = {"issued": 0, "throttled_mem": 0, "deduped": 0, "dropped_queue": 0}

    def submit(self, predictions):
        """predictions: [(file, confidence)] sorted descending."""
        issued = 0
        for f, conf in predictions:
            if conf < self.hi or issued >= self.top_k:
                break
            if self.memory.under_pressure():
                self.stats["throttled_mem"] += 1
                self.reclaim()           # give memory back instead of adding to it
                break
            now = time.monotonic()
            if now - self._recent.get(f, -1e9) < self.cooldown:
                self.stats["deduped"] += 1
                continue
            with self._lock:
                if self._pending >= self.max_queue:
                    self.stats["dropped_queue"] += 1
                    continue
                self._pending += 1
            self._recent[f] = now
            self.tracker.record_prefetch(f)
            self._pool.submit(self._run, f, FADV_WILLNEED)
            self.stats["issued"] += 1
            issued += 1
        if len(self._recent) > 50_000:   # bound the dedupe table
            cutoff = time.monotonic() - self.cooldown
            self._recent = {f: t for f, t in self._recent.items() if t >= cutoff}

    def reclaim(self):
        """Under pressure, drop pages we prefetched that were never used."""
        for f in self.tracker.pop_stale():
            with self._lock:
                self._pending += 1
            self.tracker.evictions += 1
            self._pool.submit(self._run, f, FADV_DONTNEED)

    def _run(self, path, advice):
        try:
            self._advise(path, advice)
        except OSError:
            pass
        finally:
            with self._lock:
                self._pending -= 1

    def close(self):
        self._pool.shutdown(wait=True)
