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
    """Reports memory pressure from MemAvailable/MemTotal (cached, cheap)."""

    def __init__(self, low_frac=0.10, ttl=0.25, meminfo="/proc/meminfo"):
        self.low_frac = low_frac
        self.ttl = ttl
        self.meminfo = meminfo
        self._t = 0.0
        self._pressure = False

    def under_pressure(self):
        now = time.monotonic()
        if now - self._t >= self.ttl:
            self._t = now
            self._pressure = self._read()
        return self._pressure

    def _read(self):
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


def _fadvise(path, advice):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
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
