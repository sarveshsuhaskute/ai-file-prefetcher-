# Context-Aware eBPF Prefetch Daemon

This project is a userspace, context-aware AI prefetching daemon for Linux. It uses eBPF to trace `sys_enter_read` and `sys_exit_openat` syscalls to dynamically learn file access sequences using a Markov chain model. Based on these learned sequences, it proactively loads subsequent files into the Linux page cache via `posix_fadvise(POSIX_FADV_WILLNEED)`.

The fundamental goal of this project was to determine whether a dynamically trained userspace AI could outperform the default Linux kernel's highly optimized readahead mechanisms under varying memory pressures. 

**Result (current):** it does, on predictable workloads under memory pressure: **+28% throughput and ~half the median latency** vs. default Linux readahead (14 GB set squeezed into ~7 GB of cache). The gain shrinks with unpredictable access and can hurt tail latency; see [evaluation_v2](docs/evaluation_v2.md). The earlier "it loses" result came from a broken prefetch call (`posix_fadvise(WILLNEED)` loads only ~12% of a 10 MB file unless chunked).

## Results Summary
Measured on a Linux 6.8 VM (11.9 GB RAM), page cache dropped before every run, baselines interleaved; details in [evaluation_v2](docs/evaluation_v2.md).

| Regime | Hit rate (default Linux -> daemon) | Throughput | Latency |
|---|---|---|---|
| 14 GB set, ~7 GB cache, predictable | 14% -> 70% | **+28%** | p50 -54%, p99 -16% |
| Same + 30% random accesses | 42% -> 54% | +1.7% | p50 -11%, **p95/p99 ~+15% worse** |
| Slow disk (400 MB/s) + think time | 1% -> 44% | +5% | p50 -8%, **p95/p99 up to 3x worse** |
| Working set fits in RAM | parity (original 4 GB result; pre-fix) | none | none |

The original "AI loses by 26%" report (kept in `docs/final_evaluation_report.md`) was caused by a prefetch bug and did not reproduce.

---

## Architecture
The system consists of two primary components operating across the user/kernel boundary:
1. **eBPF Kernel Probes:** C-based BPF programs attach to `tracepoints` for `sys_enter_read` and `sys_exit_openat`. They filter events (excluding noise from standard I/O and daemon tracking) and submit the raw file access events to a non-blocking `BPF_RINGBUF_OUTPUT` ring buffer.
2. **Python Userspace Daemon:** A Python listener consumes events from the ring buffer, builds a dynamic Markov chain representing file access sequences (context window = 5), and triggers `posix_fadvise` via a thread pool to proactively page files into memory.

## Setup Instructions (Lima VM)

This project relies heavily on eBPF and kernel tracing, which requires a Linux environment with proper headers. A Lima VM configuration is provided.

1. **Install Lima** (macOS/Linux):
   ```bash
   brew install lima
   ```
2. **Start the VM**:
   ```bash
   limactl start prefetch-dev
   ```
   *(If prompted, select the default Ubuntu template, or use the provided configuration to mount this repository into the VM).*
3. **Enter the VM shell**:
   ```bash
   limactl shell prefetch-dev
   ```
4. **Install Dependencies** (inside the VM):
   ```bash
   sudo apt update
   sudo apt install -y bpfcc-tools linux-headers-$(uname -r) python3-bpfcc
   ```

## Running the Evaluation Harness

Everything below runs inside the Lima VM as root (needs eBPF + `drop_caches`).

```bash
# Phase 7-style: 4 GB working set (fits in RAM), 3 runs per baseline
sudo python3 eval_runner.py --baselines none,seq,full --scale 1.0

# Phase 6-style: 14 GB working set vs 11 GB RAM, and the memory-throttling A/B test
sudo python3 eval_runner.py --baselines none,full,full-nomem --scale 3.5

# Add unpredictable accesses (30% random) to stop the workload being trivially cyclic
sudo python3 eval_runner.py --baselines none,static,seq,full --noise 0.3
```
The harness logs daemon output to a file (the old `[Interval Stats]` pipe/filter bug that made
interval prints vanish is fixed), so the endurance run no longer needs to bypass it:
`--duration 300` prints the per-minute RSS / model-size lines.

Daemon knobs: `--top-k` (max prefetches per open), `--threshold`, `--low-mem-frac`,
`--no-mem-aware`. Models are saved as JSON (`~/.cache/prefetch_model.json`), not pickle.

## Offline tools (work on any OS, no eBPF)
```bash
python3 -m unittest discover -s tests   # model + actuator unit tests
python3 simulate.py                     # cache simulator: which workloads benefit? -> docs/simulation_results.md
```

## What changed after the original evaluation
The first evaluation (`docs/final_evaluation_report.md`) is kept as-is for the record. Since then:
- `prefetch_model.py`: model split from the daemon, no per-event list rebuilds, bounded memory
  (context pruning, per-PID cap), JSON persistence.
- `prefetch_actuator.py`: **memory-pressure throttling** from `/proc/meminfo`, reclaim of unused
  prefetches with `DONTNEED`, top-k fan-out, de-dup cooldown, and a real worker pool
  (the original README claimed a thread pool that did not exist).
- Daemon uses `ring_buffer_poll` instead of `sleep(0.1)` polling and reports per-event overhead.
- `workload.py` takes `--scale/--noise`; `eval_runner.py` takes CLI args and runs the ablations.

**Re-measured on hardware** (Linux 6.8 VM): the daemon runs on real eBPF, and a cleaned-up harness (writeback settle,
warm-up, interleaving, latency) shows no configuration beating default Linux; the original 26% collapse did not reproduce.
Details and caveats in [docs/evaluation_v2.md](docs/evaluation_v2.md). An optional pressure throttle (`--reclaim-mb-s`, off by default) exists, but at 200 MB/s it disabled a helpful prefetcher in every experiment. `eval_runner.py` also takes `--workload real`
(`realworld.py`) and `--ballast-gb` to create controlled memory pressure.

---

## Lessons Learned & Future Work
1. **Check that your actuator does what you think.** `posix_fadvise(WILLNEED)` on a large file loads only ~1.2 MB; chunk it (1 MB). A "99% precision" metric that counts predictions, not resident pages, hid this for the whole first evaluation.
2. **Harness hygiene decides conclusions:** wait out writeback before `drop_caches`, discard a warm-up run, interleave baselines.
3. **Tail latency is the cost:** wrong guesses and prefetch I/O compete with demand reads. A throttle keyed to tail latency or wrong-guess rate (not reclaim rate) is the next thing to try.
4. **Untested:** the real-data workload and predict-only ablation with the fix, other storage speeds, partial-file/offset prediction, and moving the model into eBPF maps (per-event cost is only 50-140 us, so this is lower priority than it first looked).
