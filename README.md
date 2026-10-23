# Context-Aware eBPF Prefetch Daemon

This project is a userspace, context-aware AI prefetching daemon for Linux. It uses eBPF to trace `sys_enter_read` and `sys_exit_openat` syscalls to dynamically learn file access sequences using a Markov chain model. Based on these learned sequences, it proactively loads subsequent files into the Linux page cache via `posix_fadvise(POSIX_FADV_WILLNEED)`.

The fundamental goal of this project was to determine whether a dynamically trained userspace AI could outperform the default Linux kernel's highly optimized readahead mechanisms under varying memory pressures. 

**Result (current):** it does, on predictable workloads under memory pressure, and only there. With a 14 GB set squeezed into ~7 GB of cache it gives **+32% throughput and ~60% lower median latency** vs. default Linux readahead (5 runs, p < 0.001, replicated across sessions), and **+38%** with prediction-driven eviction of dead files. It does not help with random access, hurts tail latency on a busy bandwidth-limited disk, and **slows real programs whose files fit in memory** (PostgreSQL build, grep, Python start-up: +10% to +109% time per operation). See [evaluation_v3](docs/evaluation_v3.md) and the significance tables in [statistics](docs/statistics.md). The earlier "it loses" result came from a broken prefetch call (`posix_fadvise(WILLNEED)` loads only ~12% of a 10 MB file unless chunked).

## Results Summary
Linux 6.8 VMs (4 vCPU / 11.9 GB, and 2 vCPU / 3.9 GB for the second machine), page cache dropped before every run, baselines interleaved. Differences are vs. no daemon,
with Welch's t-test p-values; "n.s." = not significant (p >= 0.05). Details in [evaluation_v3](docs/evaluation_v3.md).

| Setting | Hit rate (Linux -> daemon) | Throughput | Latency |
|---|---|---|---|
| 14 GB set, ~7 GB cache, predictable | 14% -> 70% | **+32%** (p<0.001) | p50 **-59%**, p99 **-26%** (p=0.004) |
| Same + dead-file eviction (`--evict-dead`) | 14% -> 65% | **+38%** (p<0.001); +5% vs daemon (p=0.004) | p99 -11% vs daemon (p=0.017) |
| Same + 30% random accesses | 42% -> 54% | +2% (n.s.) | p99 +15% (n.s.) |
| 400 MB/s disk, 10 ms think (disk busy) | 34% -> 58% | 0% (n.s.) | p50 -38%, **p99 +131%** (p=0.006) |
| Same + adaptive I/O limit (`--adaptive-io 0.3`) | 34% -> 35% | 0% (n.s.) | p99 back to baseline, median gain lost |
| 400 MB/s disk, 100 ms think (idle gaps) | 26% -> 75% | +1% (n.s.) | p99 -2% (n.s.) |
| Second machine (2 vCPU, 4 GB), 6 GB set, no ballast | 39% -> 62% | +1% (n.s.); eviction **+6% vs daemon** (p=0.004) | p50 -5% (n.s.); eviction p50 -9% vs daemon (p<0.001) |
| Real programs, 300 MB cgroup | n/a | build +23%, start-up +45%, search +109% time/op (all p<0.001) | |

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
python3 stats.py > docs/statistics.md   # confidence intervals and Welch's t-tests for every log in docs/raw
```

Other workloads and policies (inside the VM, as root):
```bash
# real programs on the PostgreSQL source (clone + configure first, see realapps.py)
sudo python3 eval_runner.py --workload apps --baselines none,full,full-evict --daemon-args=--include-libs
# prediction-driven eviction and the adaptive I/O limit
sudo python3 eval_runner.py --baselines none,full,full-evict,full-adapt --scale 3.5 --ballast-gb 4
# a second, smaller machine: limactl start --name prefetch-small ebpf-small.yaml
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

**Re-measured on hardware** (Linux 6.8 VM): a cleaned-up harness (writeback settle, warm-up, interleaving, latency percentiles) first
showed no gain; that turned out to be the `WILLNEED` limit, not the model. With chunked prefetching the daemon wins under memory
pressure ([evaluation_v2](docs/evaluation_v2.md), [evaluation_v3](docs/evaluation_v3.md)). An optional pressure throttle
(`--reclaim-mb-s`, off by default) disabled a helpful prefetcher in every experiment. `eval_runner.py` also takes `--workload real`
(`realworld.py`), `--workload apps` (`realapps.py`), `--ballast-gb` for controlled memory pressure and `--think-ms` (now actually
passed to the workload). `prefetch_policies.py` adds dead-file eviction (`--evict-dead`) and an adaptive I/O limit (`--adaptive-io`).

---

## Lessons Learned & Future Work
1. **Check that your actuator does what you think.** `posix_fadvise(WILLNEED)` on a large file loads only ~1.2 MB; chunk it (1 MB). A "99% precision" metric that counts predictions, not resident pages, hid this for the whole first evaluation.
2. **Harness hygiene decides conclusions:** wait out writeback before `drop_caches`, discard a warm-up run, interleave baselines.
3. **Tail latency is the cost on a busy disk:** correct prefetches queue ahead of demand reads. An AIMD limit driven by I/O pressure removes the cost but, with a fixed target, also removes the gain; the target should come from the disk's own stall level.
4. **Eviction matters as much as prefetching:** dropping files whose reuse distance exceeds the cache added 5% throughput on top of prefetching.
5. **Per-event cost matters on real programs:** ~45 us per open is negligible before a 10 MB read, but not before a 4 KB cached one. A real program start-up opens hundreds of files; filtering in the kernel (by process, or first read only) is the next step.
6. **Statistics:** with 4-5 runs, several earlier "effects" (the noise-case tail, the pressure-case p99 in one session) were not significant. Report confidence intervals.
