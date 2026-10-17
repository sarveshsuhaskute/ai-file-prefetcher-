"""Collects every number the paper quotes from docs/raw into paper/data.json."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "report"))
from logparse import parse_summary  # noqa: E402

RAW = os.path.join(ROOT, "docs", "raw")


def load(name):
    p = os.path.join(RAW, name)
    return parse_summary(p) if os.path.exists(p) else {}


data = {
    "pressure": load("v6_pressure.log"),            # 5 runs: none, ra-tuned, full, full-gate
    "noise": load("v6_noise.log"),                  # 5 runs: same baselines, 30% random accesses
    "pressure_v3": load("v3_synthetic_14GB_ballast4GB_chunked_willneed.log"),   # independent replication (4 runs)
    "noise_v3": load("v3_synthetic_noise30_ballast4GB_chunked_willneed.log"),
    "slowdisk": load("v3_slowdisk400MBs_think100ms_ballast9GB_chunked_willneed.log"),
    "prefix": load("synthetic_14GB_ballast4GB.log"),   # before the WILLNEED fix
    "mispred": {k: json.load(open(os.path.join(RAW, f"misprediction_{k}.json"))) for k in ("uncapped", "cap400", "cap100")},
    "willneed": json.load(open(os.path.join(RAW, "willneed_probe.json"))) if os.path.exists(os.path.join(RAW, "willneed_probe.json")) else None,
}
json.dump(data, open(os.path.join(HERE, "data.json"), "w"), indent=1)
print("pressure:", sorted(data["pressure"]), "noise:", sorted(data["noise"]), "willneed:", bool(data["willneed"]))
