# Offline Simulation: Where Does a Learned Prefetcher Help?

Produced by `python3 simulate.py --markdown` (seed 7, 4000 accesses x 3 processes). It replays
synthetic traces through an LRU cache model, using the real `PrefetchModel`. Cells are
**demand hit rate % (prefetch precision %)**.

| Cache | Trace | none hit% (prec%) | name-oracle hit% (prec%) | markov hit% (prec%) | markov-memaware hit% (prec%) |
|---|---|---|---|---|---|
| ample cache (400 files) | cyclic (workload.py) | 97.5 (0) | 100.0 (99) | 97.5 (0) | 97.5 (0) |
| ample cache (400 files) | cyclic + 10% noise | 97.5 (0) | 99.6 (99) | 97.5 (0) | 97.5 (0) |
| ample cache (400 files) | branching (build graph) | 97.7 (0) | 98.7 (82) | 97.7 (0) | 97.7 (0) |
| ample cache (400 files) | zipf (no structure) | 96.7 (0) | 98.2 (95) | 96.7 (0) | 96.7 (0) |
| tight cache (100 files) | cyclic (workload.py) | 0.0 (0) | 99.0 (99) | 97.5 (100) | 97.5 (100) |
| tight cache (100 files) | cyclic + 10% noise | 6.3 (0) | 84.2 (88) | 82.2 (89) | 82.0 (85) |
| tight cache (100 files) | branching (build graph) | 57.0 (0) | 45.9 (37) | 73.7 (82) | 73.8 (60) |
| tight cache (100 files) | zipf (no structure) | 70.4 (0) | 63.7 (33) | 69.8 (79) | 70.0 (21) |

Policies: `none` = demand paging only; `name-oracle` = prefetch the file with the next
number in its name (an *oracle* on the cyclic traces, whose files are numbered in access
order, and able to touch never-seen files, which a learner cannot); `markov` = the daemon's model, top-2, confidence >= 0.5;
`markov-memaware` = same, but the number of unused prefetched files is capped at 10% of the cache
and the oldest unused ones are reclaimed (the policy `prefetch_actuator.py` implements with `DONTNEED`).

## What this shows
- **Ample cache:** every policy is within noise of `none`. The only misses are cold first
  touches, which no learner can predict. This matches the real 4 GB / 11 GB parity result.
- **Tight cache + structure (cyclic, branching):** the learned prefetcher gives large gains
  (cyclic: 0% -> 97.5%, branching: 57% -> 74%). `name-oracle` is *worse* than no prefetching on
  the branching trace (46% vs 57%) because it pollutes the cache with wrong guesses.
- **No structure (zipf):** the confidence gate keeps the model nearly harmless (69.8% vs 70.4% for
  `none`), while the number-guessing oracle loses 7 points.
- **Noise:** with 10% random accesses the model keeps most of the benefit (82% vs 6%).

## Caveats (read before citing these numbers)
- This is a *model*, not Linux. The simulated LRU is stricter than the kernel's active/inactive
  lists, so the baseline's 0% on the cyclic trace overstates how badly Linux would do; real
  confirmation needs `eval_runner.py` on hardware.
- Prefetches are assumed to finish before the next open: no I/O latency, no CPU overhead. The
  real daemon's Python overhead (see the Phase 6 report) is not captured here.
- **The memory-aware policy showed no measurable benefit here** (same hit rate and issued-prefetch
  count as plain `markov`; its lower "precision" is just early reclaim of unused prefetches). These
  traces are too predictable to over-prefetch, so this does not test the Phase 6 failure mode. Whether
  memory-aware throttling fixes the 14 GB regression is an open question for `eval_runner.py`
  (`--baselines full,full-nomem --scale 3.5`) on real hardware.
