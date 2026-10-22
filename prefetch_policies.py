"""Two optional daemon policies.

IOPressureController (--adaptive-io)
    Prefetch I/O competes with the programs' own reads; on a bandwidth-limited disk this is
    what raised tail latency. The kernel's I/O pressure stall information (/proc/pressure/io,
    the "some" total in microseconds) says how much of the time tasks were stalled on I/O.
    Every interval the controller turns that into a stall fraction and adjusts the bytes of
    prefetch allowed in flight: halve when the stall fraction is above target, grow by a
    fixed step when below (AIMD, as in TCP congestion control).

DeadFileEvictor (--evict-dead)
    Under LRU a file whose reuse distance exceeds the cache is evicted before it is used
    again, so keeping it after use only pushes out other data. The evictor estimates each
    file's reuse distance in bytes (opens between successive opens of that file x average
    file size) and, when a file is closed and that distance exceeds the page cache available
    to us, advises the kernel to drop it (POSIX_FADV_DONTNEED). This is the eviction half of
    the original design: the model's view of the access pattern now decides what leaves the
    cache, not only what enters it.
"""
import os
import time

MB = 1 << 20


class IOPressureController:
    def __init__(self, target=0.3, start_mb=32, min_mb=1, max_mb=128, step_mb=8, interval=0.25,
                 psi="/proc/pressure/io", clock=time.monotonic):
        self.target, self.interval, self.psi, self.clock = target, interval, psi, clock
        self.w, self.w_min, self.w_max, self.step = start_mb * MB, min_mb * MB, max_mb * MB, step_mb * MB
        self._t = self._total = None
        self.samples = self.cuts = 0
        self.w_sum = 0
        self.last_stall = None

    def _read_total(self):
        try:
            with open(self.psi) as fh:
                for line in fh:
                    if line.startswith("some"):
                        return int(line.rsplit("total=", 1)[1])
        except (OSError, ValueError, IndexError):
            return None
        return None

    def limit(self):
        """Current allowed prefetch bytes in flight (always >= one chunk)."""
        now = self.clock()
        if self._t is None or now - self._t >= self.interval:
            total = self._read_total()
            if total is not None:
                if self._t is not None and now > self._t:
                    stall = (total - self._total) / ((now - self._t) * 1e6)
                    self.last_stall = stall
                    if stall > self.target:
                        self.w = max(self.w_min, self.w // 2)
                        self.cuts += 1
                    else:
                        self.w = min(self.w_max, self.w + self.step)
                    self.samples += 1
                    self.w_sum += self.w
                self._t, self._total = now, total
        return self.w

    def summary(self):
        avg = self.w_sum / self.samples / MB if self.samples else self.w / MB
        return {"samples": self.samples, "cuts": self.cuts, "avg_limit_mb": round(avg, 1)}


class DeadFileEvictor:
    def __init__(self, margin=0.9, refresh=1.0, meminfo="/proc/meminfo", cgroup_root="/sys/fs/cgroup",
                 sizeof=None, clock=time.monotonic, max_files=50_000):
        self.margin, self.refresh, self.meminfo, self.cgroup_root = margin, refresh, meminfo, cgroup_root
        self.max_files = max_files
        self.sizeof = sizeof or self._stat_size
        self.clock = clock
        self.opens = 0
        self.last = {}            # file -> open counter at its last open
        self.interval = {}        # file -> EWMA of opens between its opens
        self.avg_size = 0.0
        self.sizes = {}
        self._t = -1e9
        self._cache = None
        self.evicted = 0

    @staticmethod
    def _stat_size(path):
        try:
            return os.stat(path).st_size
        except OSError:
            return 0

    def _size(self, f):
        s = self.sizes.get(f)
        if s is None:
            if len(self.sizes) > self.max_files:
                self.sizes.clear()
            s = self.sizes[f] = self.sizeof(f)
        return s

    def cache_bytes(self):
        """Page cache we can use: the memory cgroup's file pages if it has a limit, else system Cached."""
        now = self.clock()
        if now - self._t < self.refresh and self._cache is not None:
            return self._cache
        self._t = now
        val = None
        try:
            rel = open("/proc/self/cgroup").read().strip().split("::", 1)[1]
            base = os.path.join(self.cgroup_root, rel.lstrip("/"))
            if open(os.path.join(base, "memory.max")).read().strip() != "max":
                for line in open(os.path.join(base, "memory.stat")):
                    if line.startswith("file "):
                        val = int(line.split()[1])
                        break
        except (OSError, IndexError, ValueError):
            val = None
        if val is None:
            try:
                for line in open(self.meminfo):
                    if line.startswith("Cached:"):
                        val = int(line.split()[1]) * 1024
                        break
            except (OSError, ValueError):
                val = None
        self._cache = val
        return val

    def on_open(self, f):
        self.opens += 1
        prev = self.last.get(f)
        if prev is not None:
            gap = self.opens - prev
            old = self.interval.get(f)
            self.interval[f] = gap if old is None else 0.5 * old + 0.5 * gap
        self.last[f] = self.opens
        size = self._size(f)
        self.avg_size += (size - self.avg_size) * (0.02 if self.opens > 50 else 1 / self.opens)
        if len(self.last) > self.max_files:     # bounded: the daemon's memory counts against the same cgroup
            self.last.clear()
            self.interval.clear()

    def is_dead(self, f):
        """True if, under LRU, this file would be evicted before its next use anyway."""
        gap = self.interval.get(f)
        cache = self.cache_bytes()
        if gap is None or not cache:
            return False
        return gap * self.avg_size > self.margin * cache
