import argparse
import os
import re
import shlex
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time

def dirty_kb():
    kb = 0
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith(("Dirty:", "Writeback:")):
                kb += int(line.split()[1])
    return kb


def drop_caches():
    """Flush and wait out writeback BEFORE dropping caches, so a previous run's
    (or the setup phase's) dirty pages cannot contaminate this run's disk I/O."""
    print("    [System] Settling writeback + dropping caches...")
    subprocess.run(["sync"])
    deadline = time.time() + 120
    while dirty_kb() > 20_000 and time.time() < deadline:
        time.sleep(1)
    with open("/proc/sys/vm/drop_caches", "w") as f:
        f.write("3\n")
    time.sleep(2)


class Ballast:
    """Holds anonymous memory so the page cache has less room (controlled pressure)."""

    def __init__(self, gb):
        self.proc = None
        if gb > 0:
            code = ("import time; b = bytearray(int(%f * 2**30)); "
                    "[b.__setitem__(slice(i, i + 1), b'x') for i in range(0, len(b), 4096)]; "
                    "print('ready', flush=True); time.sleep(10**6)" % gb)
            self.proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
            self.proc.stdout.readline()
            with open("/proc/meminfo") as fh:
                avail = [l.split()[1] for l in fh if l.startswith("MemAvailable")][0]
            print(f"    [Ballast] holding {gb} GB; MemAvailable now {int(avail) // 1024} MB")

    def close(self):
        if self.proc:
            self.proc.kill()


def psi_some_avg10():
    try:
        with open("/proc/pressure/memory") as fh:
            return float(fh.readline().split()[1].split("=")[1])
    except (OSError, IndexError, ValueError):
        return 0.0


def reclaim_counters():
    """Kernel page-reclaim activity so far (/proc/vmstat): pages scanned (all, and by direct reclaim in the
    allocating task), pages reclaimed, and direct-reclaim stalls. Differences over a run show reclaim work.
    Exact names: pgscan_anon/pgscan_file repeat the same totals split by type."""
    groups = {"pgscan": ("pgscan_kswapd", "pgscan_direct", "pgscan_khugepaged"), "pgscan_direct": ("pgscan_direct",),
              "pgsteal": ("pgsteal_kswapd", "pgsteal_direct", "pgsteal_khugepaged")}
    out = dict.fromkeys(list(groups) + ["allocstall"], 0)
    try:
        with open("/proc/vmstat") as fh:
            vals = dict(line.split() for line in fh)
        for key, names in groups.items():
            out[key] = sum(int(vals.get(n, 0)) for n in names)
        out["allocstall"] = sum(int(v) for k, v in vals.items() if k.startswith("allocstall_"))
    except (OSError, ValueError):
        pass
    return out


def run_workload_iteration(daemon_mode, duration, scale, noise, workload, think_ms=10.0, extra_daemon_args=()):
    drop_caches()

    daemon_proc = log = None
    if daemon_mode:
        # Log to a file, not a PIPE: an undrained pipe can stall the daemon, and
        # the old "[Interval Stats]" filter never matched "[Interval Stats at t=..".
        log = tempfile.NamedTemporaryFile("w+", suffix=".daemon.log", delete=False)
        daemon_proc = subprocess.Popen(
            [sys.executable, "-u", "prefetch_daemon.py", "--mode", daemon_mode, *extra_daemon_args],
            stdout=log, stderr=subprocess.STDOUT)
        time.sleep(3)  # let BPF compile + attach

    psi_max = [0.0]
    stop = threading.Event()

    def sample():
        while not stop.wait(1.0):
            psi_max[0] = max(psi_max[0], psi_some_avg10())
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    vm0 = reclaim_counters()
    workload = subprocess.run(
        ([sys.executable, "realworld.py", str(duration)] if workload == "real" else
         [sys.executable, "realapps.py", str(duration)] if workload == "apps" else
         [sys.executable, "workload.py", str(duration), "--scale", str(scale), "--noise", str(noise),
          "--think-ms", str(think_ms)]),
        capture_output=True, text=True)

    stop.set()
    vm1 = reclaim_counters()
    hit_rate, total_reads, time_series, lat = None, None, [], {}
    for line in workload.stdout.split("\n"):
        m = re.search(r"Overall Cache Hit Rate: ([\d.]+%|n/a) \(Total Reads: (\d+)\)", line)
        if m:
            hit_rate = float("nan") if m[1] == "n/a" else float(m[1].rstrip("%"))
            total_reads = int(m[2])
        elif line.startswith("[apps] "):
            name, rest = line[7:].split(":", 1)
            lat.setdefault("apps", {})[name] = {k: float(v) for k, v in re.findall(r"(\w+)=([\d.]+)", rest)}
        elif line.startswith("Read Latency ms:"):
            lat.update({k: float(v) for k, v in re.findall(r"(\w+)=([\d.]+)", line)})
        elif line.startswith("["):
            time_series.append(line)
    if hit_rate is None:
        sys.exit(f"WORKLOAD FAILED:\n{workload.stderr}")

    daemon_lines = []
    if daemon_proc:
        daemon_proc.send_signal(signal.SIGINT)
        try:
            daemon_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()
        log.seek(0)
        daemon_lines = [l.strip() for l in log if l.startswith(
            ("[Precision", "[Interval Stats", "[Overhead]", "[Actuator]", "[Adaptive I/O]", "[Dead-file", "[Timeliness", "[Model]"))]
        log.close()
        os.unlink(log.name)
    lat["psi_max"] = psi_max[0]
    lat.update({k: vm1[k] - vm0[k] for k in vm0})
    return hit_rate, total_reads, lat, daemon_lines


def summarize(name, runs):
    rates = [r[0] for r in runs]
    sd = statistics.stdev(rates) if len(rates) > 1 and all(x == x for x in rates) else 0.0
    print(f"\nResult: {name}")
    if all(x != x for x in rates):      # hit rate not measured (real-application workload)
        print(f"Mean Cache Hit Rate: n/a (n={len(runs)}) | ops/run ~{statistics.mean(r[1] for r in runs):.0f}")
    else:
        print(f"Mean Cache Hit Rate: {statistics.mean(rates):.2f}% +/- {sd:.2f}% (n={len(runs)}) | "
              f"reads/run ~{statistics.mean(r[1] for r in runs):.0f}")
    for actor in sorted({a for r in runs for a in r[2].get("apps", {})}):
        vals = [r[2]["apps"][actor] for r in runs if actor in r[2].get("apps", {})]
        m = lambda k: statistics.mean(v[k] for v in vals)
        sdm = statistics.stdev([v["mean"] for v in vals]) if len(vals) > 1 else 0.0
        print(f"   [apps] {actor}: ops/run {m('ops'):.1f} | mean {m('mean'):.1f} ms +/- {sdm:.1f} | "
              f"p50 {m('p50'):.1f} ms | p95 {m('p95'):.1f} ms | per-run means {[round(v['mean'], 1) for v in vals]}")
    print(f"   peak memory PSI some-avg10: {[round(r[2].get('psi_max', 0), 1) for r in runs]}")
    for k in ("mean", "p50", "p95", "p99"):
        vals = [r[2][k] for r in runs if r[2]]
        if vals:
            print(f"   read latency {k}: {statistics.mean(vals):.2f} ms")
    print(f"   runs: {[f'{x:.2f}%' for x in rates]}  reads: {[r[1] for r in runs]}")
    for l in runs[-1][3]:
        print("   ", l)


def set_readahead(kb, dev):
    """Kernel's own knob: per-device readahead window. Returns the previous value."""
    path = f"/sys/block/{dev}/queue/read_ahead_kb"
    with open(path) as fh:
        old = fh.read().strip()
    with open(path, "w") as fh:
        fh.write(str(kb))
    return old


BASELINES = {
    "full-kf": ("12. Full AI, in-kernel filtering (first read only, no close events)", "full", ("--kernel-filter",)),
    "full-libs": ("13. Full AI, tracing libraries and bytecode (--include-libs)", "full", ("--include-libs",)),
    "full-libs-kf": ("14. Full AI, libraries + in-kernel filtering", "full", ("--include-libs", "--kernel-filter")),
    "full-adapt-auto": ("15. Full AI + adaptive I/O limit, target from paused-prefetch stall", "full", ("--adaptive-io", "auto")),
    "full-evict-kf": ("16. Full AI + dead-file eviction, in-kernel filtering", "full", ("--evict-dead", "--kernel-filter")),
    "full-idle": ("18. Full AI, prefetch I/O at idle priority", "full", ("--prefetch-ioprio", "idle")),
    "full-adapt-lat": ("19. Full AI + adaptive I/O limit, latency signal", "full", ("--adaptive-io", "latency")),
    "full-ab": ("20. Full AI + on/off self-test (prefetch only when it pays)", "full", ("--adaptive-io", "ab")),
    "full-tgate": ("21. Full AI + timeliness gate (prefetch while prefetches arrive in time)", "full", ("--timeliness-gate",)),
    "full-only-build": ("17. Full AI, tracing only the build (--only-comm make,cc1)", "full",
                        ("--kernel-filter", "--only-comm", "make,cc1")),
    "full-adapt": ("10. Full AI + adaptive I/O limit (target stall 0.3)", "full", ("--adaptive-io", "0.3")),
    "full-evict": ("11. Full AI + dead-file eviction", "full", ("--evict-dead",)),
    "full-gate": ("9. Full AI + precision-aware gate (min precision 0.6)", "full", ("--min-precision", "0.6")),
    "ra-tuned": ("7. No daemon, kernel readahead tuned (read_ahead_kb=8192)", None, ()),
    "full-cap": ("8. Full AI, in-flight prefetch capped at 32 MB", "full", ("--max-inflight-mb", "32")),
    "none": ("1. No Daemon (Default Linux Readahead)", None, ()),
    "static": ("2. Static Miner (Offline-Trained)", "static", ()),
    "seq": ("3. Sequence-Only Online Miner (Ablation)", "seq", ()),
    "full": ("4. Full Context-Aware AI", "full", ()),
    "predict-only": ("6. Predict-only (model runs, no prefetching)", "train", ()),
    "full-nomem": ("5. Full AI, memory throttling OFF", "full", ("--no-mem-aware",)),
}


def main():
    ap = argparse.ArgumentParser(description="Isolated-baseline evaluation harness (run as root on Linux)")
    ap.add_argument("--baselines", default="none,seq,full,full-nomem",
                    help=f"comma list from: {','.join(BASELINES)}")
    ap.add_argument("--iterations", type=int, default=5)
    ap.add_argument("--duration", type=int, default=40, help="seconds per run")
    ap.add_argument("--scale", type=float, default=1.0, help="1.0 = 4 GB working set, 3.5 = 14 GB")
    ap.add_argument("--noise", type=float, default=0.0, help="fraction of unpredictable accesses")
    ap.add_argument("--skip-setup", action="store_true")
    ap.add_argument("--workload", choices=["synthetic", "real", "apps"], default="synthetic",
                    help="synthetic = workload.py; real = realworld.py (walks /usr, #include graph); "
                         "apps = realapps.py (real builds, searches and Python start-ups on PostgreSQL)")
    ap.add_argument("--daemon-args", default="", help="extra flags for every daemon run, e.g. '--include-libs'")
    ap.add_argument("--ballast-gb", type=float, default=0.0, help="anonymous memory held to squeeze the page cache")
    ap.add_argument("--think-ms", type=float, default=10.0, help="per-file processing time in workload.py")
    ap.add_argument("--ra-dev", default="vda", help="block device whose read_ahead_kb the ra-tuned baseline changes")
    ap.add_argument("--warmup", type=int, default=1, help="discarded no-daemon runs before measuring")
    args = ap.parse_args()

    chosen = [b for b in args.baselines.split(",") if b]
    if args.workload in ("real", "apps"):
        args.skip_setup = True
    ballast = Ballast(args.ballast_gb)
    if "static" in chosen:       # static baseline needs a model trained on a prior run
        print("[Init] Training offline model for the static baseline...")
        drop_caches()
        if not args.skip_setup:
            subprocess.run([sys.executable, "workload.py", "--setup", "--scale", str(args.scale)], check=True)
        t = subprocess.Popen([sys.executable, "prefetch_daemon.py", "--mode", "train"])
        time.sleep(3)
        subprocess.run([sys.executable, "realworld.py" if args.workload == "real" else "workload.py", "40"]
                       + ([] if args.workload == "real" else ["--scale", str(args.scale), "--noise", str(args.noise)]))
        t.send_signal(signal.SIGINT)
        t.wait(timeout=15)
    elif not args.skip_setup:
        subprocess.run([sys.executable, "workload.py", "--setup", "--scale", str(args.scale)], check=True)

    print(f"[Config] scale={args.scale} noise={args.noise} duration={args.duration}s iterations={args.iterations}")
    # Interleave baselines across iterations so slow VM/disk drift hits all of them equally.
    results = {k: [] for k in chosen}
    for w in range(args.warmup):
        print(f"\n--- warm-up {w + 1}/{args.warmup} (discarded)")
        run_workload_iteration(None, duration=args.duration, scale=args.scale, noise=args.noise,
                               workload=args.workload, think_ms=args.think_ms)
    for it in range(args.iterations):
        for key in chosen:
            name, mode, extra = BASELINES[key]
            old_ra = set_readahead(8192, args.ra_dev) if key == "ra-tuned" else None
            print(f"\n--- iteration {it + 1}/{args.iterations}: {name}")
            results[key].append(run_workload_iteration(
                mode, duration=args.duration, scale=args.scale, noise=args.noise,
                workload=args.workload, think_ms=args.think_ms,
                extra_daemon_args=tuple(extra) + tuple(shlex.split(args.daemon_args))))
            if old_ra is not None:
                set_readahead(old_ra, args.ra_dev)
            print(f"    hit={results[key][-1][0]:.2f}% reads={results[key][-1][1]} lat={results[key][-1][2]}")
    print(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    for key in chosen:
        summarize(BASELINES[key][0], results[key])
    ballast.close()
    print("\nEvaluation Complete.")


if __name__ == "__main__":
    main()
