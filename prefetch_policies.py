"""Two optional daemon policies.

IOPressureController (--adaptive-io)
    Prefetch I/O competes with the programs' own reads; on a bandwidth-limited disk this is
    what raised tail latency. The kernel's I/O pressure stall information (/proc/pressure/io,
    the "some" total in microseconds) says how much of the time tasks were stalled on I/O.
    Every interval the controller turns that into a stall fraction and adjusts the bytes of
    prefetch allowed in flight: halve when the stall fraction is above target, grow by a
    fixed step when below (AIMD, as in TCP congestion control).
    With a fixed target the controller cannot tell prefetch-caused stall from the disk's own: on
    a disk that stalls above the target with no prefetching it can only switch prefetching off.
    In auto mode (auto_margin set) it periodically pauses prefetching for a moment, measures the
    stall the workload causes on its own, and sets the target that much plus the margin.

DeadFileEvictor (--evict-dead)
    Under LRU a file whose reuse distance exceeds the cache is evicted before it is used
    again, so keeping it after use only pushes out other data. The evictor estimates each
    file's reuse distance in bytes (opens between successive opens of that file x average
    file size) and, when a file is closed and that distance exceeds the page cache available
    to us, advises the kernel to drop it (POSIX_FADV_DONTNEED). This is the eviction half of
    the original design: the model's view of the access pattern now decides what leaves the
    cache, not only what enters it.
"""
import ctypes
import mmap
import os
import time

MB = 1 << 20


def cgroup_memory_max(cgroup_root="/sys/fs/cgroup", proc_cgroup="/proc/self/cgroup"):
    """memory.max of this process's cgroup v2 in bytes, or None when unlimited or unknown."""
    try:
        rel = open(proc_cgroup).read().strip().split("::", 1)[1]
        val = open(os.path.join(cgroup_root, rel.lstrip("/"), "memory.max")).read().strip()
        return None if val == "max" else int(val)
    except (OSError, IndexError, ValueError):
        return None


def model_context_budget(limit_bytes, share=0.15, bytes_per_context=700, floor=20_000, default=200_000):
    """Contexts the model may keep. Under a memory limit the daemon's own memory counts against the
    programs it serves (it was OOM-killed at 300 MB with 183k contexts), so give the model a share of it."""
    if not limit_bytes:
        return default
    return max(floor, min(default, int(share * limit_bytes / bytes_per_context)))


class IOPressureController:
    def __init__(self, target=0.3, start_mb=32, min_mb=1, max_mb=128, step_mb=8, interval=0.25,
                 psi="/proc/pressure/io", clock=time.monotonic, auto_margin=None, probe_every=10.0, probe_len=1.5,
                 signal="stall", device="vda", diskstats="/proc/diskstats"):
        self.target, self.interval, self.psi, self.clock = target, interval, psi, clock
        # signal "stall": share of time some task waited for I/O (PSI). signal "latency": the device's mean
        # read wait in ms (/proc/diskstats), which grows with queueing even when the disk is always busy;
        # in auto mode its target is relative: baseline * (1 + auto_margin).
        self.signal, self.device, self.diskstats = signal, device, diskstats
        self.auto_margin, self.probe_every, self.probe_len = auto_margin, probe_every, probe_len
        self._probe_end = None           # end time of the current probe (prefetching held at the minimum)
        self._next_probe = None
        self._probe_stalls = []
        self._saved_w = None
        self.baseline = None             # stall fraction measured with prefetching paused
        self.probes = 0
        self.w, self.w_min, self.w_max, self.step = start_mb * MB, min_mb * MB, max_mb * MB, step_mb * MB
        self._t = self._total = None
        self.samples = self.cuts = 0
        self.w_sum = 0
        self.last_stall = None

    def _read_total(self):
        if self.signal == "latency":
            try:
                with open(self.diskstats) as fh:
                    for line in fh:
                        f = line.split()
                        if len(f) > 6 and f[2] == self.device:
                            return (int(f[3]), int(f[6]))        # reads completed, ms spent reading
            except (OSError, ValueError):
                return None
            return None
        try:
            with open(self.psi) as fh:
                for line in fh:
                    if line.startswith("some"):
                        return int(line.rsplit("total=", 1)[1])
        except (OSError, ValueError, IndexError):
            return None
        return None

    def _probe(self, now, stall):
        """Auto mode: True while prefetching is paused to measure the workload's own stall."""
        if self._next_probe is None:
            self._next_probe = now                        # first probe straight away
        if self._probe_end is None:
            if now < self._next_probe:
                return False
            self._probe_end, self._probe_stalls, self._saved_w = now + self.probe_len, [], self.w
            self.w = self.w_min
            return True
        if stall is not None:
            self._probe_stalls.append(stall)
        if now < self._probe_end:
            return True
        tail = self._probe_stalls[1:] or self._probe_stalls   # the first sample still holds queued prefetch I/O
        if tail:
            m = sum(tail) / len(tail)
            self.baseline = m if self.baseline is None else 0.3 * self.baseline + 0.7 * m
            self.target = (self.baseline * (1 + self.auto_margin) if self.signal == "latency"
                           else min(0.95, self.baseline + self.auto_margin))
            self.probes += 1
        self.w, self._probe_end, self._next_probe = self._saved_w, None, now + self.probe_every
        return True

    def limit(self):
        """Current allowed prefetch bytes in flight (always >= one chunk)."""
        now = self.clock()
        if self._t is None or now - self._t >= self.interval:
            total = self._read_total()
            if total is not None:
                stall = None
                if self._t is not None and now > self._t:
                    if self.signal == "latency":
                        dn = total[0] - self._total[0]
                        stall = (total[1] - self._total[1]) / dn if dn > 0 else None
                    else:
                        stall = (total - self._total) / ((now - self._t) * 1e6)
                    self.last_stall = stall
                if self.auto_margin is not None and self._probe(now, stall):
                    self._t, self._total = now, total
                    return self.w
                if stall is not None:
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
        out = {"samples": self.samples, "cuts": self.cuts, "avg_limit_mb": round(avg, 1)}
        if self.auto_margin is not None:
            out.update(probes=self.probes, baseline=None if self.baseline is None else round(self.baseline, 2),
                       target=round(self.target, 2))
        return out


class ABController:
    """--adaptive-io ab: decide online whether prefetching pays, by testing both ways.

    Every `period` seconds the current mode (prefetching on or off) is flipped for `probe` seconds, and
    the rate at which the traced programs open files in that window is compared with the window just
    before it. Opens per second track the programs' progress (on the synthetic workload they are exactly
    its throughput). The faster mode is kept until the next test. On a device that is saturated by the
    programs' own reads, prefetching loses (measured: -56% throughput at 99% precision) and is switched
    off; where it wins, it stays on. The cost is the probe windows: probe / period of the time.
    """

    def __init__(self, period=20.0, probe=8.0, settle=4.0, margin=0.05, max_period=160.0, clock=time.monotonic):
        # The first `settle` seconds of each probe are ignored: the page cache needs about one pass over the
        # programs' files to reflect the new mode (measured: 2 s probes missed a -71% loss on a slow device).
        # After a test that confirms the current mode the period doubles, so a steady workload pays less.
        self.period, self.probe, self.margin, self.clock = period, probe, margin, clock
        self.settle, self.base_period, self.max_period = settle, period, max_period
        self.on = True                    # current mode outside probes
        self.t0 = None                    # start of the current phase
        self.probing = False
        self.opens = []                   # open timestamps in the last period + probe
        self.tests = self.flips = 0
        self.on_time = 0.0
        self._last = None

    def _rate(self, a, b):
        return sum(1 for t in self.opens if a <= t < b) / max(b - a, 1e-9)

    def allow(self):
        """Record one open; True if this open may trigger prefetching."""
        now = self.clock()
        if self.t0 is None:
            self.t0 = now
        if self._last is not None and (self.on != self.probing):
            self.on_time += now - self._last          # time spent with prefetching enabled
        self._last = now
        self.opens.append(now)
        if not self.probing and now - self.t0 >= self.period:
            self.probing, self.t_probe = True, now
        elif self.probing and now - self.t_probe >= self.probe:
            span = self.probe - self.settle
            flipped = self._rate(self.t_probe + self.settle, now)
            current = self._rate(self.t_probe - span, self.t_probe)
            self.tests += 1
            if flipped > current * (1 + self.margin):
                self.on = not self.on
                self.flips += 1
                self.period = self.base_period
            else:
                self.period = min(self.max_period, self.period * 2)
            self.probing, self.t0 = False, now
            cutoff = now - self.probe
            self.opens = [t for t in self.opens if t >= cutoff]
        return self.on != self.probing

    def summary(self):
        return {"tests": self.tests, "flips": self.flips, "prefetching": "on" if self.on else "off",
                "on_time_s": round(self.on_time, 1)}


def residency(path, max_bytes=64 << 20):
    """Fraction of the file's first max_bytes resident in the page cache (mmap + mincore), or None."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        size = min(os.fstat(fd).st_size, max_bytes)
        if size == 0:
            return None
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
        libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p]
        libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        addr = libc.mmap(None, size, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
        if addr is None or addr == ctypes.c_void_p(-1).value:
            return None
        try:
            n = (size + mmap.PAGESIZE - 1) // mmap.PAGESIZE
            vec = ctypes.create_string_buffer(n)
            if libc.mincore(addr, size, vec) != 0:
                return None
            return sum(1 for b in vec.raw if b & 1) / n
        finally:
            libc.munmap(addr, size)
    finally:
        os.close(fd)


class TimelinessGate:
    """--timeliness-gate: stop prefetching when prefetches do not arrive in time.

    A prefetch is useful only if its pages are resident when the program reads the file. On each first
    read of a prefetched file the daemon measures how much of it is resident (mincore). When the running
    average falls below `low`, prefetching drops to a trickle (one open in `trickle`), which keeps the
    measurement going; it resumes above `high`. On a device already saturated by the programs' own reads,
    prefetches arrive late or are evicted before use, so they are turned off; where they arrive in time
    they stay on. Unlike an on/off test it needs no probe and reacts within a few dozen reads.
    """

    def __init__(self, low=0.3, high=0.5, alpha=0.1, min_samples=20, trickle=8, measure=residency):
        self.low, self.high, self.alpha, self.min_samples, self.trickle = low, high, alpha, min_samples, trickle
        self.measure = measure
        self.ewma = 1.0
        self.samples = 0
        self.on = True
        self.opens = self.skipped = self.switches = 0

    def on_useful_read(self, path):
        r = self.measure(path)
        if r is None:
            return
        self.samples += 1
        self.ewma += self.alpha * (r - self.ewma)
        if self.on and self.samples >= self.min_samples and self.ewma < self.low:
            self.on, self.switches = False, self.switches + 1
        elif not self.on and self.ewma > self.high:
            self.on, self.switches = True, self.switches + 1

    def allow(self):
        self.opens += 1
        if self.on or self.opens % self.trickle == 0:
            return True
        self.skipped += 1
        return False

    def summary(self):
        return {"samples": self.samples, "residency_ewma": round(self.ewma, 2), "switches": self.switches,
                "state": "on" if self.on else "trickle", "skipped_opens": self.skipped}


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
