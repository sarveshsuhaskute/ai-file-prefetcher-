# Evaluation v2 (post-fix): the prefetcher wins under memory pressure

Raw logs: [`docs/raw/`](raw/) (`v3_*` = post-fix). Environment: Lima VM, Linux 6.8, 11.9 GB RAM, virtio disk, 4 vCPU.
Every run: writeback settled, page cache dropped, one discarded warm-up, baselines interleaved across iterations.
"Hit rate" = `mincore()` residency just before each read. "Throttling on" = the old `--reclaim-mb-s 200` setting;
"throttling off" = no pressure throttling (now the default).

## The bug that invalidated the earlier results
`posix_fadvise(WILLNEED)` over a whole 10 MB file loads only **~12%** of it on Linux 6.8 (measured with `mincore`;
`readahead(2)` behaves the same). Issuing it in **1 MB chunks loads 100%**. The daemon's reported "99% precision" only
meant "predicted file was read within 2 s", not "pages were resident". The actuator now chunks WILLNEED
(`prefetch_actuator._fadvise`, capped at 64 MB/file). All earlier "prefetching does nothing" results measured a
mostly no-op prefetcher; they are kept in the appendix as superseded.

## Results with working prefetching (3 regimes, all with 4 GB-ballast-style pressure)

### A. 14 GB working set squeezed by a 4 GB ballast (4 iterations, 40 s)
| Baseline | Hit rate | Reads/run | Latency mean / p50 / p95 / p99 (ms) |
|---|---|---|---|
| No daemon | 14.1% ± 0.6 | 5638 | 9.9 / 9.1 / 18.4 / 25.1 |
| Full, throttling on | 13.8% ± 0.7 | 5568 | 10.3 / 9.5 / 19.0 / 26.9 |
| **Full, throttling off** | **70.0% ± 0.2** | **7242 (+28%)** | **5.7 / 4.2 / 13.5 / 21.1** |

Un-throttled: 4910 prefetches, 99.8% later read, mean latency -42%, p50 -54%, p95 -26%, p99 -16%, model cost 78 µs/event.
The throttled daemon issued 6 prefetches and rejected 4131, so it is identical to no daemon.

### B. 30% unpredictable accesses, same pressure (3 iterations)
| Baseline | Hit rate | Reads/run | Latency mean / p50 / p95 / p99 (ms) | Precision |
|---|---|---|---|---|
| No daemon | 42.3% ± 0.6 | 6725 | 6.6 / 5.6 / 13.5 / 19.6 | - |
| Full, throttling on | 41.6% ± 0.8 | 6647 | 7.0 / 5.9 / 14.5 / 21.1 | 36.9% (374 issued) |
| Full, throttling off | 54.5% ± 0.4 | 6838 (+1.7%) | 6.7 / 5.0 / 15.5 / 22.4 | 46.2% (4186 issued) |

Gain shrinks: residency +12 points, p50 -11%, throughput +1.7%, but mean latency is unchanged and **tail latency is ~15% worse**
(wrong guesses cost disk bandwidth).

### C. Slow disk (400 MB/s cgroup cap), 100 ms think time per file, 9 GB ballast (4 iterations, 60 s)
| Baseline | Hit rate | Reads/run | Latency mean / p50 / p95 / p99 (ms) |
|---|---|---|---|
| No daemon | 1.1% ± 0.2 | 2319 | 65.8 / 79.5 / 94.4 / 97.8 |
| Full, throttling on | 1.2% ± 0.4 | 2311 | 66.2 / 80.1 / 93.9 / 97.5 |
| Full, throttling off | 43.8% ± 1.7 | 2443 (+5.4%) | 62.1 / 73.5 / 167.5 / 285.6 |

Modest mean/median gain, **much worse tail** (prefetch I/O competes with demand reads on the capped disk).

## Conclusions
1. **On predictable access under cache pressure the learned prefetcher beats default Linux**: +28% throughput and ~half the
   median latency (A). This reverses the project's original headline, which was measured with a broken prefetch call.
2. **The gain depends on predictability and spare I/O.** With 30% random accesses it is small (B); on a disk-bound,
   bandwidth-capped setup it trades a modest median win for a large tail-latency loss (C). When the working set fits in RAM
   there is nothing to gain (see appendix: no-pressure runs were at parity).
3. **The memory-pressure throttle was counterproductive** at 200 MB/s reclaim: it fired on nearly every event and turned the
   prefetcher off in all three regimes, forfeiting the gain in A and the median gain in B/C. It is now opt-in
   (`--reclaim-mb-s`, default 0). Whether any throttle helps (for example one keyed to tail latency or to wrong-guess rate) is open.
4. **Model overhead is not the bottleneck**: 50-140 µs per open event.
5. The original "26% collapse" remains unreproduced (first-run writeback contamination, see appendix), and "eviction blindness"
   is not supported: pressure did not hurt the un-throttled daemon here.

## Limits
Synthetic cyclic workload with identical 10 MB files (B adds noise); one VM, n=3-4, 40-60 s runs; a very fast virtual disk
except in C. The real-data workload (`realworld.py`) and the predict-only ablation have **not** been rerun with the fix; the
pre-fix real-data hit-rate gaps were confounded and should be disregarded. The default (throttle off) was smoke-tested but not
re-run as a separate experiment: it is equivalent to the "throttling off" rows because PSI and MemAvailable never tripped there.
One unexplained outlier run appears in the pre-fix appendix data.

---

# Appendix: pre-fix results (SUPERSEDED: measured with the ~12% WILLNEED bug)
### Why the first evaluation could not be trusted
- The original 14 GB set was **not** under memory pressure. Its concurrent footprint (~10.5 GB)
  nearly fit in ~11.4 GB of available RAM: PSI stayed 0 and there was no reclaim churn
  (`docs/raw/synthetic_14GB_no_ballast_CONTAMINATED.log`).
- Run 1 after data creation was contaminated by writeback: the *same* "full" configuration scored
  26.8% in iteration 1 and 71.9% / 71.6% in iterations 2-3; "no daemon" scored 56.2% vs 73.5% / 72.2%.
  That is the same size as the reported "26% collapse", so **the original loss is very likely a harness
  artifact**. (Not proven for the original runs, which we cannot replay; a clean harness does not reproduce it.)
- The old throttling signal (`MemAvailable`) never fired (`throttled_mem: 0`): it counts reclaimable cache as available.

### Results (real pressure: synthetic 14 GB set + 4 GB ballast, 5 iterations)
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

### 30% random accesses (same setup, 3 iterations)
| Baseline | Hit rate | Reads/run | p50 latency | Precision |
|---|---|---|---|---|
| No daemon | 42.8% ± 1.3 | 6755 | 5.59 ms | - |
| Full, throttling on | 41.5% ± 0.8 | 6616 | 6.11 ms | 35.0% |
| Full, throttling off | 41.4% ± 0.3 | 6431 | 6.56 ms | 44.5% |

With unpredictable accesses, prefetching is mildly **harmful** (about -2% reads/run throttled, -5% unthrottled;
latency +9% / +17%), and throttling reduces the harm. n=3; differences are ~1-2 s.d., treat as suggestive.

### Real data (`realworld.py`: walks of /usr/share and /usr/lib + #include DFS; ballast 10.2 GB; 3 iterations)
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

### Conclusions
1. **The headline holds: no configuration beat default Linux on throughput or latency.**
2. **The original explanations were not reproduced.** The 26% throughput collapse is most likely a harness artifact;
   per-event model/dispatch cost is 20-80 µs (~1-8% of a core), not a bottleneck.
3. **Prediction is accurate on structured traces (95-99% precision) but accuracy is not the constraint.** On disk-bound
   workloads, prefetch cannot create bandwidth; on cache-resident workloads there is nothing to gain.
4. **Memory throttling works mechanically** (reclaim-rate signal; it also issued 217 `DONTNEED` reclaims in the noisy run) but its threshold
   (200 MB/s) is so low that under sustained thrash it simply disables prefetching. It reduced harm under noise;
   no benefit was measured elsewhere.

### Limits
Single VM, small n (3-5), 40-60 s runs, one unexplained outlier, synthetic workload except the last table, whole-file
`WILLNEED` only, relative-path opens cannot be prefetched. Where prefetching could help (idle I/O to overlap, slower
storage, partial-file/offset prediction) was not tested; the virtio disk here is very fast.


Pre-fix tables (synthetic 14 GB + 4 GB ballast, 30% noise, real data, slow disk) are in `docs/raw/*.log` without the `v3_` prefix.
