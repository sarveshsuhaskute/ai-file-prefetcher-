# What does a wrong prefetch cost?

Script: [`misprediction_cost.py`](../misprediction_cost.py) (uses the daemon's real chunked-WILLNEED routine). Raw JSON:
`docs/raw/misprediction_{uncapped,cap400,cap100}.json`. Setup: Lima VM, 10 MB files, guest cache evicted before each
trial, host cache warmed first, 20 trials per cell, **k = 2 wrong files** prefetched per misprediction (the daemon's top-k).
`gap` = time between the wrong prefetch and the app actually asking for the right file (the app's think time).

## 1. How long does a wrongly chosen file take to load?
| Disk | Time until the 10 MB wrong file is 100% resident (mean / p95) | Effective rate |
|---|---|---|
| Uncapped virtio | 4.4 ms / 6.1 ms | ~2.3 GB/s |
| Capped 400 MB/s | 4.7 ms / 6.6 ms | burst (cap is a token bucket; a 10 MB file fits in one burst) |
| Capped 100 MB/s | 49 ms / 113 ms | ~200 MB/s burst, ~100 MB/s sustained |

## 2. What it costs the file the app really needed
Latency of the needed 10 MB file: **no prefetch** 3.8 ms / 4.1 ms / 48.7 ms (uncapped / 400 / 100 MB/s);
**prefetched correctly** 1.3 / 1.5 / 6.5 ms. A wrong prediction makes it *slower than no prefetch at all*:

| gap (think time) | uncapped penalty | 400 MB/s penalty | 100 MB/s penalty | model: 2x10 MB / 100 MB/s - gap |
|---|---|---|---|---|
| 0 ms | +0.8 ms (+21%) | +18.4 ms (+453%) | **+207 ms (+424%)** | 200 ms |
| 25 ms | +0.1 ms | -0.5 ms | +178 ms (+366%) | 175 ms |
| 50 ms | +0.8 ms | +0.5 ms | +152 ms (+312%) | 150 ms |
| 100 ms | -0.1 ms | -1.2 ms | +104 ms (+214%) | 100 ms |
| 200 ms | +0.4 ms | +7.5 ms* | +4 ms (+8%) | 0 ms |

\*isolated noisy cell (p95 13.8 ms); neighbouring gaps show no penalty. Uncapped differences below ~1 ms are within noise.

**Rule of thumb (fits the 100 MB/s data to within a few ms):**
`penalty ≈ max(0, k × file_size / disk_bandwidth − gap)`. The wrong files sit ahead of the real read in the I/O queue;
once the app's think time exceeds their transfer time the cost vanishes. On fast storage the penalty is negligible
(<1 ms); it only bites when the disk is the bottleneck. The 400 MB/s cap deviates from the formula (18 ms vs ~50 ms at gap 0)
because the throttle admits bursts.

## 3. Break-even precision (rough)
On the 100 MB/s disk a correct prediction saves ~42 ms (48.7 -> 6.5) and a wrong one costs up to 207 ms (gap 0) or 104 ms
(gap 100 ms). Treating each event as all-or-nothing, the prefetcher only pays off when
`P(correct) > penalty / (gain + penalty)`, about **83%** at gap 0 and **71%** at gap 100 ms, and at any precision once the gap
exceeds the wrong files' transfer time. (Simplified: ignores that the right file's sibling prefetches also queue.) This is
consistent with the end-to-end runs: 99%-precision predictions on the capped disk still hurt tails (see below), and 37-46%
precision under 30% noise did not net-win on tails.

## 4. In-situ wrong-prefetch rate (from the end-to-end runs, last iteration's daemon stats)
| Experiment | Prefetches issued | "Useful" (read within 2 s) | Wrong / late |
|---|---|---|---|
| 14 GB + ballast, predictable | 4910 | 99.8% | 0.2% |
| Same, 30% noise | 4186 | 46.2% | **53.8%** (~2250 x 10 MB = up to ~22 GB of speculative reads in 40 s) |
| Slow disk 400 MB/s, think ~10 ms | 1924 | 99.3% | 0.7% |

"Useful" means read within 2 s, so late-but-eventually-used prefetches count as wrong: the real waste is somewhat lower.
Note the slow-disk row: with only 0.7% wrong prefetches p99 latency was still ~3x worse, so **tail damage there comes from correct
prefetches queueing ahead of demand reads, not from mistakes**. Cutting wrong guesses alone would not fix it; limiting how far
ahead / how many bytes are in flight would.

## Caveats
Single VM; file size fixed at 10 MB; cgroup caps are bursty; the guest-cache eviction path (`DONTNEED`) and the Mac host cache
sit below this measurement; the break-even is a back-of-envelope, not a measured end-to-end result.
