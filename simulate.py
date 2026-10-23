"""Offline cache simulator: where does a learned file prefetcher help?

Replays synthetic multi-process open traces through an LRU page-cache model
(capacity in files) under several prefetch policies, using the *real*
PrefetchModel. Runs anywhere (no Linux/eBPF needed). Assumes a prefetch
completes before the next open, i.e. it measures prediction *quality* and
cache-pollution cost, not I/O latency. Use eval_runner.py for real hardware.

    python3 simulate.py            # print the results table
    python3 simulate.py --markdown # emit markdown (docs/simulation_results.md)
"""
import argparse
import random
import re
from collections import OrderedDict

from prefetch_model import PrefetchModel


# ---------------------------------------------------------------- traces
def interleave(streams, rng):
    """Round-robin-ish interleave of per-pid streams (like concurrent actors)."""
    streams = [list(s) for s in streams]
    idx = [0] * len(streams)
    out = []
    while any(i < len(s) for i, s in zip(idx, streams)):
        k = rng.randrange(len(streams))
        if idx[k] < len(streams[k]):
            out.append(streams[k][idx[k]])
            idx[k] += 1
    return out


def trace_cyclic(n, rng, noise=0.0):
    """Original workload.py: 3 actors looping fixed file orders (100/80/120)."""
    sizes = {"builder": 100, "logger": 80, "processor": 120}
    streams = []
    for pid, (comm, m) in enumerate(sizes.items(), start=1):
        s = []
        for i in range(n):
            j = rng.randrange(m) if rng.random() < noise else i % m
            s.append((pid, comm, f"/data/{comm}_{j}.dat"))
        streams.append(s)
    return interleave(streams, rng)


def scrambled_names(files, rng, prefix):
    ids = list(range(files))
    rng.shuffle(ids)
    return [f"{prefix}{i}.dat" for i in ids]


def trace_branching(n, rng, files=300, fanout=3):
    """Each file has `fanout` successors with skewed probabilities (e.g. build graphs)."""
    names = scrambled_names(files, rng, "/src/f")
    succ = {f: rng.sample(range(files), fanout) for f in range(files)}
    weights = [0.6, 0.3, 0.1][:fanout]
    streams = []
    for pid in (1, 2, 3):
        cur, s = rng.randrange(files), []
        for _ in range(n):
            s.append((pid, "app", names[cur]))
            cur = rng.choices(succ[cur], weights)[0]
        streams.append(s)
    return interleave(streams, rng)


def trace_zipf(n, rng, files=400):
    """Unstructured popularity-skewed access: a control where learning shouldn't help."""
    names = scrambled_names(files, rng, "/z/")
    w = [1.0 / (r + 1) for r in range(files)]
    streams = [[(pid, "app", names[i]) for i in rng.choices(range(files), w, k=n)]
               for pid in (1, 2, 3)]
    return interleave(streams, rng)


TRACES = {
    "cyclic (workload.py)": lambda n, r: trace_cyclic(n, r),
    "cyclic + 10% noise": lambda n, r: trace_cyclic(n, r, noise=0.10),
    "branching (build graph)": trace_branching,
    "zipf (no structure)": trace_zipf,
}


# --------------------------------------------------------------- policies
class LRUCache:
    def __init__(self, cap):
        self.cap, self.d = cap, OrderedDict()

    def touch(self, f):                         # demand access
        hit = f in self.d
        self.d[f] = None
        self.d.move_to_end(f)
        self._evict()
        return hit

    def prefetch(self, f):
        if f in self.d:
            return False
        self.d[f] = None
        self._evict()
        return True

    def drop(self, f):                          # POSIX_FADV_DONTNEED analogue
        self.d.pop(f, None)

    def _evict(self):
        while len(self.d) > self.cap:
            self.d.popitem(last=False)

    def occupancy(self):
        return len(self.d) / self.cap


def run_policy(trace, cap, policy):
    cache = LRUCache(cap)
    model = PrefetchModel("seq") if policy.startswith("markov") else None
    budget = max(2, int(0.10 * cap))            # memaware: max unused prefetched files
    hits = issued = useful = 0
    pending = OrderedDict()                     # prefetched, not yet demanded
    for pid, comm, f in trace:
        if pending.pop(f, None) is not None:
            useful += 1
        hits += cache.touch(f)
        if policy == "name-oracle":             # next-numbered file; an oracle on cyclic traces
            m = re.search(r"^(.*?)(\d+)(\.\w+)$", f)
            if m:
                nxt = f"{m[1]}{int(m[2]) + 1}{m[3]}"
                if cache.prefetch(nxt):
                    issued += 1
                    pending[nxt] = True
        elif model:
            model.process_access(pid, comm, 0, f)
            for pf, conf in model.predict_next(pid, comm, 0, top_k=2, min_conf=0.5):
                if policy == "markov-memaware":
                    while len(pending) >= budget:   # reclaim oldest unused prefetch
                        cache.drop(pending.popitem(last=False)[0])
                if cache.prefetch(pf):
                    issued += 1
                    pending[pf] = True
    n = len(trace)
    return {"hit": 100.0 * hits / n, "issued": issued,
            "precision": 100.0 * useful / issued if issued else 0.0}


POLICIES = ["none", "name-oracle", "markov", "markov-memaware"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4000, help="accesses per process")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    out = []
    for cap_label, cap in (("ample cache (400 files)", 400), ("tight cache (100 files)", 100)):
        out.append((cap_label, []))
        for tname, gen in TRACES.items():
            trace = gen(args.n, random.Random(args.seed))
            row = {p: run_policy(trace, cap, p) for p in POLICIES}
            out[-1][1].append((tname, row))

    if args.markdown:
        print("| Cache | Trace | " + " | ".join(f"{p} hit% (prec%)" for p in POLICIES) + " |")
        print("|---|---|" + "---|" * len(POLICIES))
        for cap_label, rows in out:
            for tname, row in rows:
                cells = " | ".join(f"{row[p]['hit']:.1f} ({row[p]['precision']:.0f})" for p in POLICIES)
                print(f"| {cap_label} | {tname} | {cells} |")
    else:
        for cap_label, rows in out:
            print(f"\n== {cap_label} ==")
            print(f"{'trace':26}" + "".join(f"{p:>20}" for p in POLICIES))
            for tname, row in rows:
                print(f"{tname:26}" + "".join(
                    f"{row[p]['hit']:>12.1f}% ({row[p]['precision']:>3.0f}%)" for p in POLICIES))


if __name__ == "__main__":
    main()
