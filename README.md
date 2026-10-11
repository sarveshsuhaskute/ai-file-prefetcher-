# Context-Aware eBPF Prefetch Daemon

This project is a userspace, context-aware AI prefetching daemon for Linux. It uses eBPF to trace `sys_enter_read` and `sys_exit_openat` syscalls to dynamically learn file access sequences using a Markov chain model. Based on these learned sequences, it proactively loads subsequent files into the Linux page cache via `posix_fadvise(POSIX_FADV_WILLNEED)`.

The fundamental goal of this project was to determine whether a dynamically trained userspace AI could outperform the default Linux kernel's highly optimized readahead mechanisms under varying memory pressures. 

**Spoiler:** It does not. The true contribution of this project is the diagnostic isolation of exactly *why* userspace AI struggles against mature kernel subsystems, providing concrete architectural lessons for future implementations.

## Results Summary
The system was evaluated strictly against the default Linux kernel under both normal load and memory-exhaustion stress tests. 

- **Under normal conditions (4GB working set / 11GB RAM):** The AI achieves exact performance parity with the kernel (both at 99.25% hit rate). The OS caches the data entirely, rendering the AI's 99.9% precision functionally redundant.
- **Under memory pressure (14GB working set / 11GB RAM):** The AI loses to the kernel, suffering a ~26% throughput collapse. This was traced directly to userspace computational overhead (context switching and tree updates) and an eviction-blindness flaw where the AI continuously prefetches data that the OS immediately evicts.

For the full breakdown of the methodology, evaluation, and diagnostic findings, read the [Final Evaluation Report](docs/final_evaluation_report.md).

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

The evaluation harness (`eval_runner.py`) generates a synthetic workload of interconnected file accesses and pits the AI daemon against the default Linux kernel. 

### Phase 6: Memory Pressure Stress Test
To run the adversarial stress test (14GB working set vs 11GB RAM), ensure the `workload.py` script is configured to generate 1400 files, and run:

```bash
# Inside the Lima VM
sudo python3 eval_runner.py
```
This will sequentially run the 4 isolated baselines (No Daemon, Static Miner, Sequence-Only, and Full AI) and print the cache hit rates and throughput metrics.

### Phase 7: Normal Load & Endurance Test
To run the endurance test (4GB working set) and verify there are no memory leaks over a 5-minute run, the evaluation harness must be bypassed (due to a known subprocess stdout capture bug in `eval_runner.py`).

1. Configure `workload.py` to `100` files per worker (~4GB total).
2. Run the isolated test:
```bash
# Start the workload in the background
sudo python3 workload.py 300 &

# Start the daemon directly for 5 minutes and capture interval stats
sudo timeout 305 python3 -u prefetch_daemon.py --mode full > /tmp/daemon_raw.log 2>&1
```
3. Check the logs for memory stability:
```bash
grep "Interval Stats" /tmp/daemon_raw.log
```

---

## Lessons Learned & Future Work
To outperform the default Linux kernel, the architecture must evolve beyond userspace intervention:
1. **Memory-Aware Eviction:** Eviction logic must query system memory pressure and proactively evict the tail of the LRU cache when free memory drops, rather than relying solely on predictive confidence.
2. **eBPF Migration:** The userspace overhead tax must be eliminated by moving the Markov chain logic entirely into kernel-space eBPF maps.
