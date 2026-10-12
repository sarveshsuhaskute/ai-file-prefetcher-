# Context-Aware eBPF Prefetch Daemon

This project is a userspace, context-aware AI prefetching daemon for Linux. It uses eBPF to trace `sys_enter_read` and `sys_exit_openat` syscalls to dynamically learn file access sequences using a Markov chain model. Based on these learned sequences, it proactively loads subsequent files into the Linux page cache via `posix_fadvise(POSIX_FADV_WILLNEED)`.

The fundamental goal of this project was to determine whether a dynamically trained userspace AI could outperform the default Linux kernel's highly optimized readahead mechanisms under varying memory pressures. 

**Spoiler:** It does not (re-confirmed on a cleaned-up harness, see [evaluation_v2](docs/evaluation_v2.md)). The true contribution of this project is the diagnostic isolation of exactly *why* userspace AI struggles against mature kernel subsystems, providing concrete architectural lessons for future implementations.

## Results Summary
The system was evaluated strictly against the default Linux kernel under both normal load and memory-exhaustion stress tests. 

- **Under normal conditions (4GB working set / 11GB RAM):** The AI achieves exact performance parity with the kernel (both at 99.25% hit rate). The OS caches the data entirely, rendering the AI's 99.9% precision functionally redundant.
- **Under memory pressure (14GB working set / 11GB RAM):** The AI loses to the kernel, suffering a ~26% throughput collapse. This was traced directly to userspace computational overhead (context switching and tree updates) and an eviction-blindness flaw where the AI continuously prefetches data that the OS immediately evicts.

**Current results:** [docs/evaluation_v2.md](docs/evaluation_v2.md). The original [Final Evaluation Report](docs/final_evaluation_report.md) is kept for the record, but its "26% collapse" and its causal explanations did not reproduce.

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
Details and caveats in [docs/evaluation_v2.md](docs/evaluation_v2.md). The pressure signal is now reclaim throughput
(`--reclaim-mb-s`), because `MemAvailable` never fired. `eval_runner.py` also takes `--workload real`
(`realworld.py`) and `--ballast-gb` to create controlled memory pressure.

---

## Lessons Learned & Future Work
To outperform the default Linux kernel, the architecture must evolve beyond userspace intervention:
1. **Memory-Aware Eviction:** Eviction logic must query system memory pressure and proactively evict the tail of the LRU cache when free memory drops, rather than relying solely on predictive confidence.
2. **eBPF Migration:** The userspace overhead tax must be eliminated by moving the Markov chain logic entirely into kernel-space eBPF maps.
