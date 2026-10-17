"""Context-aware file-access prediction model (no eBPF / bcc dependency).

Variable-order Markov model over per-PID file-open sequences, optionally
re-weighted by context signals (process name, time-of-day bucket, extension).
Kept separate from the daemon so it can be unit-tested and simulated offline.
"""
import json
import os
from collections import deque


def get_ext(filename):
    _, ext = os.path.splitext(filename)
    return ext if ext else ".none"


class PrefetchModel:
    MODES = ("full", "seq", "static", "train")

    def __init__(self, mode="full", max_window=5, max_contexts=200_000,
                 w_seq=0.6, max_pids=4096):
        if mode not in self.MODES:
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        self.max_window = max_window
        self.max_contexts = max_contexts
        self.max_pids = max_pids
        self.w_seq = w_seq
        self.history = {}                 # pid -> deque of recent files
        self.freq = {}                    # context tuple -> {next_file: count}
        self.totals = {}                  # context tuple -> total count
        self.file_total = {}
        self.file_comm = {}
        self.file_time = {}
        self.comm_total = {}
        self.comm_ext = {}

    # ---- learning -------------------------------------------------------
    def _hist(self, pid):
        h = self.history.get(pid)
        if h is None:
            if len(self.history) >= self.max_pids:      # bound per-PID state
                self.history.pop(next(iter(self.history)))
            h = self.history[pid] = deque(maxlen=self.max_window)
        return h

    def forget_pid(self, pid):
        self.history.pop(pid, None)

    def process_access(self, pid, comm, time_bucket, filename):
        hist = self._hist(pid)
        if self.mode != "static":                       # static = frozen model
            h = tuple(hist)
            for i in range(len(h)):
                ctx = h[i:]
                row = self.freq.get(ctx)
                if row is None:
                    row = self.freq[ctx] = {}
                    self.totals[ctx] = 0
                row[filename] = row.get(filename, 0) + 1
                self.totals[ctx] += 1
            self.file_total[filename] = self.file_total.get(filename, 0) + 1
            fc = self.file_comm.setdefault(filename, {})
            fc[comm] = fc.get(comm, 0) + 1
            ft = self.file_time.setdefault(filename, {})
            ft[time_bucket] = ft.get(time_bucket, 0) + 1
            ext = get_ext(filename)
            self.comm_total[comm] = self.comm_total.get(comm, 0) + 1
            ce = self.comm_ext.setdefault(comm, {})
            ce[ext] = ce.get(ext, 0) + 1
            if len(self.freq) > self.max_contexts:
                self._prune()
        hist.append(filename)

    def _prune(self):
        """Drop singleton contexts (seen once) to bound memory."""
        for ctx in [c for c, t in self.totals.items() if t <= 1]:
            del self.freq[ctx]
            del self.totals[ctx]
        # Still over budget (everything is repeated): drop the rarest half.
        if len(self.freq) > self.max_contexts:
            keep = sorted(self.totals, key=self.totals.get, reverse=True)
            for ctx in keep[self.max_contexts // 2:]:
                del self.freq[ctx]
                del self.totals[ctx]

    # ---- prediction -----------------------------------------------------
    def predict_next(self, pid, comm, time_bucket, top_k=None, min_conf=0.0):
        """Return [(file, confidence)] sorted by descending confidence."""
        hist = self.history.get(pid)
        if not hist:
            return []
        h = tuple(hist)
        scores = {}
        total_weight = 0
        for i in range(len(h)):
            ctx = h[i:]
            row = self.freq.get(ctx)
            if row is None:
                continue
            L = len(ctx)
            total_weight += L
            denom = self.totals[ctx]
            for nxt, cnt in row.items():
                scores[nxt] = scores.get(nxt, 0.0) + L * cnt / denom
        if not scores:
            return []

        ranked = []
        for f, s in scores.items():
            p_seq = s / total_weight
            if self.mode == "full":
                conf = self.w_seq * p_seq + (1 - self.w_seq) * self._p_ctx(f, comm, time_bucket)
            else:
                conf = p_seq
            if conf >= min_conf:
                ranked.append((f, conf))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:top_k] if top_k else ranked

    def _p_ctx(self, f, comm, bucket):
        ft = self.file_total.get(f, 0)
        if not ft:
            return 0.0
        p_comm = self.file_comm[f].get(comm, 0) / ft
        p_time = self.file_time[f].get(bucket, 0) / ft
        ct = self.comm_total.get(comm, 0)
        p_ext = self.comm_ext.get(comm, {}).get(get_ext(f), 0) / ct if ct else 0.0
        return (p_comm + p_time + p_ext) / 3.0

    # ---- persistence (JSON: safe to load, unlike pickle) ---------------
    def save(self, path):
        state = {
            "version": 1,
            "freq": [[list(c), r] for c, r in self.freq.items()],
            "file_total": self.file_total,
            "file_comm": self.file_comm,
            "file_time": {f: {str(k): v for k, v in d.items()} for f, d in self.file_time.items()},
            "comm_total": self.comm_total,
            "comm_ext": self.comm_ext,
        }
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)               # atomic: no torn model on crash

    def load(self, path):
        with open(path) as fh:
            s = json.load(fh)
        self.freq = {tuple(c): r for c, r in s["freq"]}
        self.totals = {c: sum(r.values()) for c, r in self.freq.items()}
        self.file_total = s["file_total"]
        self.file_comm = s["file_comm"]
        self.file_time = {f: {int(k): v for k, v in d.items()} for f, d in s["file_time"].items()}
        self.comm_total = s["comm_total"]
        self.comm_ext = s["comm_ext"]

    @property
    def size(self):
        return len(self.freq)
