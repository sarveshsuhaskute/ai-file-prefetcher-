# Evaluation v2: re-measurement on a cleaned-up harness

> **PROVISIONAL.** All tables below except the last section were measured **before** the WILLNEED fix: a single
> `posix_fadvise(WILLNEED)` over a 10 MB file loads only ~12% of it on Linux 6.8 (1 MB chunks load 100%), so the
> prefetcher was mostly a no-op for large files in those runs. Conclusions 1, 3 and 4 below may not hold. Only the
> slow-disk experiment (last section) has been rerun with the fixed actuator; the 14 GB-pressure and 30%-noise reruns are pending.

Raw logs: [`docs/raw/`](raw/). Environment: Lima VM, Linux 6.8, 11.9 GB RAM, virtio disk, 4 vCPU.
All runs: page cache dropped (after waiting out writeback) before every run, one discarded warm-up,
baselines interleaved across iterations. "Hit rate" = `mincore()` residency just before each read.

## Why the first evaluation could not be trusted
- The original 14 GB set was **not** under memory pressure. Its concurrent footprint (~10.5 GB)
  nearly fit in ~11.4 GB of available RAM: PSI stayed 0 and there was no reclaim churn
  (`docs/raw/synthetic_14GB_no_ballast_CONTAMINATED.log`).
- Run 1 after data creation was contaminated by writeback: the *same* "full" configuration scored
  26.8% in iteration 1 and 71.9% / 71.6% in iterations 2-3; "no daemon" scored 56.2% vs 73.5% / 72.2%.
  That is the same size as the reported "26% collapse", so **the original loss is very likely a harness
  artifact**. (Not proven for the original runs, which we cannot replay; a clean harness does not reproduce it.)
- The old throttling signal (`MemAvailable`) never fired (`throttled_mem: 0`): it counts reclaimable cache as available.

## Results (real pressure: synthetic 14 GB set + 4 GB ballast, 5 iterations)
| Baseline | Hit rate | Reads/run | Latency p50 / p95 | Prefetches (precision) |
|---|---|---|---|---|
| No daemon | 12.2% ± 0.4 | 5379 | 9.95 / 20.4 ms | - |
| Predict-only (model, no prefetch) | 12.4% ± 0.3 | 5377 | 10.1 / 20.7 ms | 0 |
| Full AI, throttling on | 13.1% ± 0.7 | 5430 | 9.8 / 19.9 ms | 3 issued, 3827 throttled |
| Full AI, throttling off | 17.8% ± 4.7 (median 19.8) | 4508* | 11.2 / 21.1 ms | 571 (99.1%) |

\*One run in this row was an unexplained outlier (9.4%, 1617 reads; no OOM/kernel warning). The other four:
19.7-20.0% hit, 5128-5299 reads.

Reading: the model costs nothing (predict-only == no daemon). Throttling fires on almost every event, so
throttled "full" is effectively "no daemon". Un-throttled prefetching is 99% accurate and raises
residency ~7.5 points, but **throughput and latency do not improve** (the workload is disk-bandwidth-bound:
prefetching reorders I/O, it cannot add bandwidth).

## 30% random accesses (same setup, 3 iterations)
| Baseline | Hit rate | Reads/run | p50 latency | Precision |
|---|---|---|---|---|
| No daemon | 42.8% ± 1.3 | 6755 | 5.59 ms | - |
| Full, throttling on | 41.5% ± 0.8 | 6616 | 6.11 ms | 35.0% |
| Full, throttling off | 41.4% ± 0.3 | 6431 | 6.56 ms | 44.5% |

With unpredictable accesses, prefetching is mildly **harmful** (about -2% reads/run throttled, -5% unthrottled;
latency +9% / +17%), and throttling reduces the harm. n=3; differences are ~1-2 s.d., treat as suggestive.

## Real data (`realworld.py`: walks of /usr/share and /usr/lib + #include DFS; ballast 10.2 GB; 3 iterations)
| Baseline | Hit rate | Reads/run | p50 latency |
|---|---|---|---|
| No daemon | 75.7% ± 0.8 | ~135,957 | 0.05 ms |
| Predict-only | 41.2% ± 9.8 | ~134,088 | 0.11 ms |
| Full, throttling on | 34.0% ± 6.3 | ~136,229 | 0.10 ms |
| Full, throttling off | 68.3% ± 0.9 | ~140,372 | 0.06 ms |

**This regime cannot separate the baselines on performance:** all four are CPU-bound on tiny files
(sub-0.2 ms reads), and reads/run are within ~5% of each other. The large residency gaps are suspicious:
predict-only never prefetches yet differs from "no daemon" by 34 points. Our hypothesis (**untested**) is that
the daemon's own footprint (bcc/clang compile, ~170 MB RSS) shrinks the page cache enough to matter when only
~1.3 GB is available. Un-throttled prefetching then partly refills the cache. Do not cite the hit-rate column here as
evidence about prediction quality.

## Conclusions
1. **The headline holds: no configuration beat default Linux on throughput or latency.**
2. **The original explanations were not reproduced.** The 26% throughput collapse is most likely a harness artifact;
   per-event model/dispatch cost is 20-80 µs (~1-8% of a core), not a bottleneck.
3. **Prediction is accurate on structured traces (95-99% precision) but accuracy is not the constraint.** On disk-bound
   workloads, prefetch cannot create bandwidth; on cache-resident workloads there is nothing to gain.
4. **Memory throttling works mechanically** (reclaim-rate signal; it also issued 217 `DONTNEED` reclaims in the noisy run) but its threshold
   (200 MB/s) is so low that under sustained thrash it simply disables prefetching. It reduced harm under noise;
   no benefit was measured elsewhere.

## Limits
Single VM, small n (3-5), 40-60 s runs, one unexplained outlier, synthetic workload except the last table, whole-file
`WILLNEED` only, relative-path opens cannot be prefetched. Where prefetching could help (idle I/O to overlap, slower
storage, partial-file/offset prediction) was not tested; the virtio disk here is very fast.

## Rerun with the WILLNEED fix: slow disk + think time (the regime where prefetch should help)
Setup: cgroup disk cap 400 MB/s, 100 ms think time per file, 9 GB ballast (cache ~2.4 GB vs ~3 GB active set), 4 iterations,
60 s runs. Log: `docs/raw/v3_slowdisk400MBs_think100ms_ballast9GB_chunked_willneed.log`.

| Baseline | Hit rate | Reads/run | Latency mean / p50 / p95 / p99 (ms) | Prefetches (precision) |
|---|---|---|---|---|
| No daemon | 1.1% ± 0.2 | 2319 | 65.8 / 79.5 / 94.4 / 97.8 | - |
| Full, throttling on | 1.2% ± 0.4 | 2311 | 66.2 / 80.1 / 93.9 / 97.5 | 26 issued, 1931 throttled |
| Full, throttling off | 43.8% ± 1.7 | 2443 (+5.4%) | 62.1 / 73.5 / 167.5 / 285.6 | 1924 (99.3%) |

Reading: with prefetching actually working, residency goes from 1% to 44% and throughput rises ~5%, mean latency
falls ~6% and p50 ~8%. But **tail latency gets much worse** (p95 94 -> 167 ms, p99 98 -> 286 ms): prefetch I/O competes
with demand reads for the capped disk. The throttled daemon is identical to no daemon (the reclaim threshold trips on
nearly every event). So: a modest mean/median win and a clear tail-latency cost, on one workload, n=4.
