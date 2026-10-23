# Evaluation v3: real applications, new policies, statistics, a second machine

Raw logs: [`docs/raw/`](raw/) (`v7_*`, `v8_*`, `vm2_*`). Significance tests for every log: [`docs/statistics.md`](statistics.md) (`python3 stats.py`).
Same harness as [v2](evaluation_v2.md): writeback settled and cache dropped before every run, one discarded warm-up, configurations interleaved.
Every difference below is relative to no daemon unless stated: mean difference, 95% confidence interval (Welch), two-sided Welch's t-test p-value.
With 4-5 runs per configuration, only p < 0.05 is treated as an effect.

## Corrections to earlier results

1. **Slow-disk think time.** The v3 slow-disk run was reported as 100 ms of think time per file. `eval_runner.py` never passed
   `--think-ms` to the workload, so it used the 10 ms default (the logged run times confirm it: about 78 ms per file at ~66 ms of read time).
   The log is renamed `v3_slowdisk400MBs_think10ms_...`; both settings are measured again below.
2. **Significance.** On the v6 logs (5 runs):
   - memory pressure: throughput +29.1% [+27.2, +31.1], p<0.001, median latency -55.7% [-59.5, -52.0], p<0.001: real;
     p99 -8.4% [-21.1, +4.4], p=0.148: **not significant** (earlier text said -16%);
   - 30% random accesses: throughput +1.7% [-2.9, +6.4], p=0.415, p99 +14.6% [-10.3, +39.5], p=0.194: **neither is significant**
     (earlier text said the tail was ~15% worse);
   - tuned readahead under pressure: throughput -0.1% [-2.1, +2.0], p=0.915 (no effect), p99 +28.1% [+15.4, +40.9], p=0.003 (worse).

## A. Bandwidth-limited disk, measured with the think time actually applied
400 MB/s cgroup read cap, 9 GB ballast, 4 GB working set, 4 runs of 60 s. Configuration 10 is the adaptive I/O limit (Section C).

| Configuration | Hit rate | Reads/run | p50 / p99 (ms) | Precision |
|---|---|---|---|---|
| 100 ms think, no daemon | 25.5% ± 1.3 | 1588 | 9.8 / 15.9 | - |
| 100 ms think, daemon | 74.7% ± 0.5 | 1600 | 8.9 / 15.6 | 99.6% |
| 100 ms think, adaptive limit | 74.5% ± 0.4 | 1588 | 9.8 / 16.2 | 99.3% |
| 10 ms think, no daemon | 34.4% ± 0.8 | 3472 | 9.5 / 95.9 | - |
| 10 ms think, daemon | 58.5% ± 0.4 | 3468 | 5.9 / 221.4 | 99.7% |
| 10 ms think, adaptive limit | 35.0% ± 0.1 | 3493 | 9.3 / 96.1 | 36.4% |

- **100 ms:** hit rate triples, but throughput +0.7% [-3.3, +4.7], p=0.688 and p99 -2.1% [-33.0, +28.7], p=0.852:
  no effect. The steps are think-bound and the disk is idle often enough to absorb the prefetches.
- **10 ms:** median -37.8% [-48.4, -27.1], p=0.001, p99 **+130.9% [+71.9, +189.9], p=0.006**, throughput
  -0.1% [-1.8, +1.6], p=0.862. Precision 99.7%: correct prefetches queue ahead of demand reads.
  This reproduces the v3 result (p99 98 -> 286 ms) and is now the only setting where the tail cost is confirmed.

## B. Real applications
`realapps.py`: three real programs on the PostgreSQL source tree, concurrently, 60 s per run, 5 runs. `make` of `src/port` + `src/common`;
`grep -rn` + `git grep` over `src/backend`; `python3` importing 20 stdlib modules. Daemon run with `--include-libs` (start-up opens are
the workload). Memory limits via `systemd-run --scope -p MemoryMax=...`, which contains the daemon too, so its memory counts.

| Limit | Configuration | Build (s) | Python start-up (ms) | Search (ms) |
|---|---|---|---|---|
| none | no daemon | 11.26 | 57.8 | 62.8 |
| none | daemon | 12.36 | 70.3 | 69.8 |
| 400 MB | no daemon | 11.30 | 57.9 | 63.7 |
| 400 MB | daemon | 12.58 | 69.7 | 77.4 |
| 300 MB | no daemon | 11.42 | 58.6 | 63.6 |
| 300 MB | daemon | 14.07 | 84.7 | 133.1 |

Change with the daemon (time per operation):

| Limit | Build | Start-up | Search |
|---|---|---|---|
| none | +9.7% [-0.2, +19.7], p=0.053 | +21.6% [+7.0, +36.2], p=0.014 | +11.1% [+2.9, +19.3], p=0.018 |
| 400 MB | +11.3% [+8.5, +14.2], p<0.001 | +20.4% [+16.1, +24.7], p<0.001 | +21.4% [+18.0, +24.9], p<0.001 |
| 300 MB | +23.2% [+20.3, +26.1], p<0.001 | +44.5% [+40.4, +48.7], p<0.001 | +109.2% [+101.0, +117.4], p<0.001 |

**The daemon slows every real program, more as the limit tightens.** Prediction is accurate (93% of prefetches read),
but there is nothing to gain: without the daemon the build time barely changes from no limit to 300 MB, so the files stay cached.
The cost: 1.0 M open events per 60 s at 45 µs, 55 s CPU (~0.9 of 4 cores),
197 MB RSS charged to the same limit. In the synthetic tests each open is followed by 10 MB of disk reads; here by a few
KB already in memory, so the per-event cost dominates. Reducing it needs in-kernel filtering (e.g. by process or by first read only).

## C. Prediction-driven eviction and adaptive I/O limit (`prefetch_policies.py`)
- `--evict-dead`: on close, drop a file (`DONTNEED`) when its estimated reuse distance (EWMA of opens between its opens x mean file
  size) exceeds the page cache available (cgroup file pages if limited, else `Cached`), unless it was just prefetched.
- `--adaptive-io T`: every 250 ms read `/proc/pressure/io` (some, total); halve the prefetch bytes-in-flight limit when the stall
  fraction > T, else add 8 MB (1-128 MB). AIMD.

Memory pressure (14 GB set, 4 GB ballast), 5 runs of 40 s:

| Configuration | Hit rate | Reads/run | p50 / p99 (ms) | Precision |
|---|---|---|---|---|
| No daemon | 13.7% ± 0.9 | 5589 | 9.1 / 27.3 | - |
| Daemon | 69.7% ± 0.3 | 7355 | 3.7 / 20.3 | 99.9% |
| Daemon + dead-file eviction | 64.6% ± 1.0 | 7727 | 3.6 / 18.1 | 98.7% |
| Daemon + adaptive I/O limit | 69.8% ± 0.3 | 7296 | 3.9 / 20.4 | 99.9% |

- Daemon vs no daemon (replication of v6): throughput +31.6% [+28.5, +34.7], p<0.001, p50 -59.4% [-64.9, -54.0], p<0.001, p99 -25.5% [-38.4, -12.7], p=0.004.
- **Dead-file eviction vs plain daemon: throughput +5.1% [+2.7, +7.5], p=0.004, p99 -10.9% [-19.2, -2.6], p=0.017.**
  6356 files dropped in one 40 s run. Lower hit rate (64.6% vs 69.7%) yet more throughput:
  the cause is less reclaim work, measured in section E (-95% pages scanned).
- Adaptive limit vs plain daemon under memory pressure: throughput -0.8% [-1.4, -0.2], p=0.018: no practical effect (disk not the bottleneck).
- Adaptive limit on the 10 ms slow disk vs plain daemon: p99 -56.6% [-82.1, -31.0], p=0.006, p50 +57.8% [+50.4, +65.3], p<0.001.
  It cut 205/209 times to ~1.5 MB: stall stays above 0.3 even with prefetching nearly off, so it acts
  as an off switch. A target set from the no-prefetch stall level is the next step.

- **Dead-file eviction on real programs.** 300 MB cgroup: OOM-killed (a 196 MB python3, the daemon's size) in both attempts
  (`raw/v8_apps_mem300_crashed*.log`, kernel lines in `raw/v8_apps_crash*_kernel_log.txt`); the plain daemon survives that limit.
  Daemon RSS grows ~195 -> 265 MB in 45 s on this workload with or without eviction (no limit), so headroom is thin; why eviction
  tips it over is not established. 400 MB (`raw/v8_apps_mem400.log`): runs, drops 85006 files per run,
  no significant change vs the plain daemon (throughput -3.1% [-7.3, +1.1], p=0.123, search time +1.7% [-1.4, +4.8], p=0.238).

## D. Second machine (`ebpf-small.yaml`: 2 vCPU, 3.9 GB RAM, same kernel 6.8.0-147 and BCC 0.29.1)
6 GB data set (scale 1.5), no ballast: the working set does not fit naturally. 5 runs of 40 s. Environment: [`raw/vm2_env.txt`](raw/vm2_env.txt).

| Configuration | Hit rate | Reads/run | p50 / p99 (ms) | Precision |
|---|---|---|---|---|
| No daemon | 39.3% ± 0.1 | 8029 | 3.7 / 10.8 | - |
| Daemon | 61.5% ± 0.2 | 8076 | 3.5 / 10.8 | 99.8% |
| Daemon + precision gate | 61.6% ± 0.1 | 7996 | 3.6 / 10.9 | 99.9% |
| Daemon + dead-file eviction | 32.0% ± 1.7 | 8552 | 3.2 / 11.6 | 99.9% |
| Daemon + adaptive I/O limit | 61.8% ± 0.3 | 8107 | 3.5 / 10.5 | 99.9% |

- **Daemon: hit rate +22 points but throughput +0.6% [-1.0, +2.2], p=0.413, p99 -0.6% [-15.9, +14.8], p=0.930.** Likely CPU-bound
  (2 vCPU for 3 readers + daemon; copying 10 MB from cache sets the pace). CPU use was not measured. Same lesson as the real programs.
- **Dead-file eviction vs plain daemon: throughput +5.9% [+2.9, +8.9], p=0.004, p50 -9.3% [-12.9, -5.7], p<0.001** (replicates C).
  Hit rate falls to 32.0% (below no daemon) while work done rises: consistent with saved reclaim work.
- Gate and adaptive limit: no significant change (throughput -1.0% [-2.4, +0.4], p=0.139 and +0.4% [-1.0, +1.7], p=0.532 vs daemon).

- 30% random accesses on this machine (5 runs): throughput -1.4% [-3.1, +0.3], p=0.091, p99 +8.7% [-0.0, +17.4], p=0.051: not significant, as on the main VM.

## E. Cheaper tracing, measured reclaim, automatic I/O target (`v9_*`)
- **Prediction cost.** Profiling showed ~24 candidates scored per open on real programs. Candidates whose sequence score cannot
  reach the threshold even with a perfect context score are now skipped (exact; unit-tested), and file extensions are cached:
  125 -> 37 us per open under cProfile. `--kernel-filter` (first read only, close only for eviction) changed little: the cost is
  ~25,000 opens/s, mostly grep. `--only-comm` is an in-kernel allow-list of process names.

| Limit | Configuration | Build (s) | Python start-up (ms) | Search (ms) |
|---|---|---|---|---|
| 300 MB | no daemon | 11.90 | 61.6 | 65.8 |
| 300 MB | all programs + libraries | 14.08 | 81.2 | 124.9 |
| 300 MB | all programs | 13.76 | 78.3 | 116.4 |
| 300 MB | build only (`--only-comm make,cc1`) | 12.22 | 61.5 | 68.1 |
| none | no daemon | 12.10 | 63.0 | 67.0 |
| none | all programs + libraries | 12.66 | 69.6 | 71.9 |
| none | all programs | 12.83 | 69.6 | 74.7 |
| none | build only (`--only-comm make,cc1`) | 12.68 | 65.7 | 65.1 |

- All programs traced, 300 MB: build +15.6% [+5.8, +25.5], p=0.006, search +76.8% [+59.8, +93.9], p<0.001.
- **Build only, 300 MB: no program significantly slower** (build +2.7% [-5.7, +11.0], p=0.451, start-up -0.2% [-10.6, +10.2], p=0.957,
  search +3.5% [-3.2, +10.2], p=0.239). No limit: only the traced build pays (+4.8% [+0.8, +8.8], p=0.027).
- **Reclaim (memory pressure, 40 s runs):** pages scanned 10.3 M (no daemon), 14.3 M (daemon), 0.7 M (dead-file eviction):
  eviction vs daemon -95.1% [-96.6, -93.6], p<0.001; direct-reclaim stalls 222 -> 0 per run; throughput +4.2% [+2.9, +5.5], p<0.001.
  Each direct-reclaim stall scans exactly 64 pages, so the two direct-reclaim rows in statistics.md move together.
- **Auto I/O target (`--adaptive-io auto`), busy slow disk:** baseline stall 0.62, target 0.72, 16 cuts in 176 samples; p99
  +94.4% [+93.2, +95.7], p<0.001 vs no daemon, -1.9% [-3.5, -0.3], p=0.029 vs daemon. Negative result: the PSI stall share is
  near saturation on a busy disk with or without prefetching, so it cannot see the extra per-read queueing. A per-read latency
  signal is needed.
- Note: this session's slow-disk baseline differs from v8 (no-daemon hit rate 45% vs 34%; the daemon's median gain was not
  significant here, its p99 cost was: +98%, p<0.001).

