"""Checks that site/model.js (the in-browser model) matches prefetch_model.py exactly.

Generates the simulator's traces in Python, runs both implementations on them, and compares
hit rate, prefetch count, precision and every individual prediction.
    python3 site/parity_check.py      (needs node)
"""
import json
import os
import random
import subprocess
import sys
import tempfile
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import simulate  # noqa: E402
from prefetch_model import PrefetchModel  # noqa: E402


def run(trace, cap, policy, mode):
    cache = simulate.LRUCache(cap)
    model = PrefetchModel(mode) if policy == "markov" else None
    hits = issued = useful = 0
    pending, preds = OrderedDict(), []
    for pid, comm, f in trace:
        if pending.pop(f, None) is not None:
            useful += 1
        hits += cache.touch(f)
        if model:
            model.process_access(pid, comm, 0, f)
            p = model.predict_next(pid, comm, 0, top_k=2, min_conf=0.5)
            preds.append([[x, c] for x, c in p])
            for pf, _ in p:
                if cache.prefetch(pf):
                    issued += 1
                    pending[pf] = True
    return {"hit": 100 * hits / len(trace), "issued": issued,
            "precision": 100 * useful / issued if issued else 0, "preds": preds}


def main():
    traces = {n: g(1500, random.Random(7)) for n, g in simulate.TRACES.items()}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(traces, fh)
    js = json.loads(subprocess.run(["node", os.path.join(HERE, "parity_runner.js"), fh.name],
                                   capture_output=True, text=True, check=True).stdout)
    os.unlink(fh.name)
    cases = preds = bad = 0
    for name, trace in traces.items():
        for cap in (100, 400):
            for pol, mode in (("none", "seq"), ("markov", "seq"), ("markov", "full")):
                a, b = run(trace, cap, pol, mode), js[f"{name}|{cap}|{pol}|{mode}"]
                cases += 1
                same = abs(a["hit"] - b["hit"]) < 1e-9 and a["issued"] == b["issued"] and abs(a["precision"] - b["precision"]) < 1e-9
                for pa, pb in zip(a["preds"], b["preds"]):
                    preds += 1
                    if [x for x, _ in pa] != [x for x, _ in pb] or any(abs(ca - cb) > 1e-9 for (_, ca), (_, cb) in zip(pa, pb)):
                        same = False
                bad += not same
    print(f"{cases} cases, {preds} predictions compared: {'identical' if not bad else f'{bad} cases differ'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
