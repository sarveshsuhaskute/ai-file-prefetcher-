# Design, complexity and limits

## Data path
```
app open()/read()/close()
   |  tracepoints: sys_enter/exit_openat, sys_enter_read, sys_enter_close   (kernel, eBPF)
   v  per-PID fd -> filename map, noise filter, 8-page BPF ring buffer
prefetch_daemon.py  (userspace, ring_buffer_poll)
   |-- PrefetchModel.process_access / predict_next   variable-order Markov (orders 1..5) + context re-weighting
   |-- Actuator.submit                                top-k, cooldown dedupe, pressure + in-flight-byte guards
   v   worker pool -> posix_fadvise(WILLNEED) in 1 MB chunks      (page cache fill, off the hot path)
```
Why chunked: a single `WILLNEED` over a 10 MB file loads only ~12% on Linux 6.8 (measured, `docs/evaluation_v2.md`).

## Model
For history `h` (last W=5 files of this PID) and next file `x`, every suffix `h[i:]` keeps counts of what followed it.
Prediction blends suffix distributions weighted by suffix length L: `p_seq(x) = sum_L L*P(x|suffix_L) / sum_L L`.
`full` mode mixes in `0.4 * mean(P(comm|x), P(time-bucket|x), P(ext|comm))`. Confidence >= 0.5 triggers a prefetch.

## Complexity (W = window, F = distinct successors of a context, k = top-k, S = file size, c = 1 MB chunk)
| Operation | Time |
|---|---|
| `process_access` | O(W^2) (W suffix slices + hashes) |
| `predict_next` | O(W^2 + W*F + M log M), M <= W*F |
| `Actuator.submit` | O(k) |
| one prefetch syscall work | O(min(S, 64 MB) / c), on a worker thread |
| prune (when contexts > cap) | O(C) amortised, O(C log C) worst case |
| memory | O(C*F), C <= 200k contexts; per-PID history O(4096*W) |
Cost of a wrong prediction: compute identical to a right one; I/O O(k*S); delay to the needed read
O(max(0, k*S/B - g)) for disk bandwidth B and app think time g (fits the 100 MB/s data within a few ms, `docs/misprediction_cost.md`).

## Safeguards
- Bounded model memory (context pruning, PID cap); JSON (not pickle) persistence with atomic replace.
- `--max-inflight-mb`: bounds queued prefetch bytes, because tail-latency damage comes from queueing, not only from wrong guesses.
- Optional pressure throttle (`--reclaim-mb-s`, PSI, MemAvailable); off by default after it measurably hurt.

## Known limits
Relative-path opens cannot be prefetched (daemon resolves paths from its own cwd); whole-file granularity only (no offset
prediction); Python userspace on the hot path (20-140 us/event); evaluated on one VM with synthetic + `/usr` workloads and
a cgroup-capped (not physically slow) disk.
