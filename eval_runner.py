import argparse
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time

def drop_caches():
    print("    [System] Dropping caches...")
    subprocess.run(["sync"])
    with open("/proc/sys/vm/drop_caches", "w") as f:
        f.write("3\n")
    time.sleep(1)

def run_workload_iteration(daemon_mode, duration, scale, noise, extra_daemon_args=()):
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

    workload = subprocess.run(
        [sys.executable, "workload.py", str(duration), "--scale", str(scale), "--noise", str(noise)],
        capture_output=True, text=True)

    hit_rate, total_reads, time_series = None, None, []
    for line in workload.stdout.split("\n"):
        m = re.search(r"Overall Cache Hit Rate: ([\d.]+)% \(Total Reads: (\d+)\)", line)
        if m:
            hit_rate, total_reads = float(m[1]), int(m[2])
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
            ("[Precision", "[Interval Stats", "[Overhead]", "[Actuator]"))]
        log.close()
        os.unlink(log.name)
    return hit_rate, total_reads, time_series, daemon_lines


def run_baseline(name, mode, iterations, **kw):
    print(f"\n{'=' * 60}\nRunning Baseline: {name}\n{'=' * 60}")
    rates, reads, last_lines = [], [], []
    for i in range(iterations):
        print(f"  -> Iteration {i + 1}/{iterations}...")
        hr, n, ts, lines = run_workload_iteration(mode, **kw)
        rates.append(hr)
        reads.append(n)
        last_lines = lines or last_lines
    mean = statistics.mean(rates)
    sd = statistics.stdev(rates) if len(rates) > 1 else 0.0
    print(f"\nResult: {name}")
    print(f"Mean Cache Hit Rate: {mean:.2f}% +/- {sd:.2f}% (n={iterations}) | "
          f"throughput ~{statistics.mean(reads):.0f} reads")
    print(f"Runs: {[f'{x:.2f}%' for x in rates]}")
    for l in last_lines:
        print("   ", l)


BASELINES = {
    "none": ("1. No Daemon (Default Linux Readahead)", None, ()),
    "static": ("2. Static Miner (Offline-Trained)", "static", ()),
    "seq": ("3. Sequence-Only Online Miner (Ablation)", "seq", ()),
    "full": ("4. Full Context-Aware AI", "full", ()),
    "full-nomem": ("5. Full AI, memory throttling OFF", "full", ("--no-mem-aware",)),
}


def main():
    ap = argparse.ArgumentParser(description="Isolated-baseline evaluation harness (run as root on Linux)")
    ap.add_argument("--baselines", default="none,seq,full,full-nomem",
                    help=f"comma list from: {','.join(BASELINES)}")
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--duration", type=int, default=40, help="seconds per run")
    ap.add_argument("--scale", type=float, default=1.0, help="1.0 = 4 GB working set, 3.5 = 14 GB")
    ap.add_argument("--noise", type=float, default=0.0, help="fraction of unpredictable accesses")
    ap.add_argument("--skip-setup", action="store_true")
    args = ap.parse_args()

    chosen = [b for b in args.baselines.split(",") if b]
    if "static" in chosen:       # static baseline needs a model trained on a prior run
        print("[Init] Training offline model for the static baseline...")
        drop_caches()
        if not args.skip_setup:
            subprocess.run([sys.executable, "workload.py", "--setup", "--scale", str(args.scale)], check=True)
        t = subprocess.Popen([sys.executable, "prefetch_daemon.py", "--mode", "train"])
        time.sleep(3)
        subprocess.run([sys.executable, "workload.py", "40", "--scale", str(args.scale), "--noise", str(args.noise)])
        t.send_signal(signal.SIGINT)
        t.wait(timeout=15)
    elif not args.skip_setup:
        subprocess.run([sys.executable, "workload.py", "--setup", "--scale", str(args.scale)], check=True)

    print(f"[Config] scale={args.scale} noise={args.noise} duration={args.duration}s iterations={args.iterations}")
    for key in chosen:
        name, mode, extra = BASELINES[key]
        run_baseline(name, mode, args.iterations, duration=args.duration,
                     scale=args.scale, noise=args.noise, extra_daemon_args=extra)
    print("\nEvaluation Complete.")


if __name__ == "__main__":
    main()
