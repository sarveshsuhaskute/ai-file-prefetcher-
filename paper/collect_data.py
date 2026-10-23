"""Collects every number the paper quotes from docs/raw into paper/data.json."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "report"))
from logparse import parse_summary  # noqa: E402
sys.path.insert(0, ROOT)
import stats  # noqa: E402

RAW = os.path.join(ROOT, "docs", "raw")


def load(name):
    p = os.path.join(RAW, name)
    return parse_summary(p) if os.path.exists(p) else {}


def sig(name):
    """Welch tests from per-run values: {config number: {metric: [diff %, ci lo, ci hi, p]}}, vs no daemon and vs Full AI."""
    p = os.path.join(RAW, name)
    if not os.path.exists(p):
        return {}
    out = {}
    for tag, rows in (("vs_none", stats.report(p)), ("vs_full", stats.report(p, base_prefix="4.", only={"8", "9", "10", "11", "12", "15", "16", "18", "19", "20", "21"})),
                      ("vs_libs", stats.report(p, base_prefix="13.", only={"4", "12", "14", "17"}))):
        for cfg, metric, _na, _nb, d, lo, hi, pv in rows:
            out.setdefault(tag, {}).setdefault(cfg.split(".")[0], {})[metric] = [round(d, 2), round(lo, 2), round(hi, 2), pv]
    return out


def apps(name):
    """Real-application runs: {config number: {actor: mean ms over runs}}."""
    p = os.path.join(RAW, name)
    if not os.path.exists(p):
        return {}
    out = {}
    for cfg, runs in stats.per_run(p).items():
        actors = {a for r in runs for a in r.get("apps", {})}
        out[cfg.split(".")[0]] = {a: sum(r["apps"][a]["mean"] for r in runs) / len(runs) for a in actors}
    return out


def daemon_cost(name):
    """Per-configuration daemon figures from a SUMMARY section: precision, events, CPU seconds, peak RSS."""
    p = os.path.join(RAW, name)
    if not os.path.exists(p) or "SUMMARY" not in open(p).read():
        return {}
    out = {}
    for blk in open(p).read().split("SUMMARY", 1)[1].split("Result: ")[1:]:
        d = {}
        for key, rx in (("useful", r"Useful Prefetches: (\d+)"), ("issued", r"Useful Prefetches: \d+ / (\d+)"),
                        ("events", r"per open event: [\d.]+ us \((\d+) events\)"), ("us_per_event", r"per open event: ([\d.]+) us"),
                        ("cpu_user", r"CPU Time: User ([\d.]+)s"), ("cpu_sys", r"Sys ([\d.]+)s"), ("rss_kb", r"Max RSS \(RAM\): (\d+) KB"),
                        ("evicted_dead", r"evicted (\d+) files after use"), ("adapt_cuts", r"'cuts': (\d+)"),
                        ("adapt_samples", r"'samples': (\d+)"), ("adapt_avg_mb", r"'avg_limit_mb': ([\d.]+)"),
                        ("ab_tests", r"'tests': (\d+)"), ("ab_flips", r"'flips': (\d+)"), ("ab_on_s", r"'on_time_s': ([\d.]+)")):
            m = re.findall(rx, blk)
            if m:
                d[key] = sum(float(x) for x in m) / len(m)     # mean over runs where printed once per run
        out[blk.split(".", 1)[0]] = d
    return out


LOGS = ["v6_pressure.log", "v6_noise.log", "v3_slowdisk400MBs_think10ms_ballast9GB_chunked_willneed.log",
        "v7_apps_unlimited.log", "v7_apps_mem400.log", "v7_apps_mem300.log", "v8_slowdisk_think100.log",
        "v8_slowdisk_think10.log", "v8_pressure.log", "v8_apps_mem400.log", "vm2_pressure.log", "vm2_noise.log", "v9_apps_mem300.log", "v9_apps_unlimited.log",
        "v9_pressure.log", "v9_slowdisk_think10.log", "v10_slowdev_think10.log", "v10_apps_mem300_evict.log",
        "v10_cgroupcap_idle.log", "v11_slowdev_ab.log", "v11_pressure_ab.log", "v12_slowdev_ab.log"]


data = {
    "pressure": load("v6_pressure.log"),            # 5 runs: none, ra-tuned, full, full-gate
    "noise": load("v6_noise.log"),                  # 5 runs: same baselines, 30% random accesses
    "pressure_v3": load("v3_synthetic_14GB_ballast4GB_chunked_willneed.log"),   # independent replication (4 runs)
    "noise_v3": load("v3_synthetic_noise30_ballast4GB_chunked_willneed.log"),
    "slowdisk": load("v3_slowdisk400MBs_think10ms_ballast9GB_chunked_willneed.log"),
    "prefix": load("synthetic_14GB_ballast4GB.log"),   # before the WILLNEED fix
    "mispred": {k: json.load(open(os.path.join(RAW, f"misprediction_{k}.json"))) for k in ("uncapped", "cap400", "cap100")},
    "v8_slow100": load("v8_slowdisk_think100.log"),
    "v8_slow10": load("v8_slowdisk_think10.log"),
    "v8_pressure": load("v8_pressure.log"),
    "vm2_pressure": load("vm2_pressure.log"),
    "vm2_noise": load("vm2_noise.log"),
    "apps": {k: apps(f"v7_apps_{k}.log") for k in ("unlimited", "mem400", "mem300")} | {"evict400": apps("v8_apps_mem400.log")},
    "apps9": {k: apps(f"v9_apps_{k}.log") for k in ("mem300", "unlimited")},
    "v9_pressure": load("v9_pressure.log"),
    "v9_slow10": load("v9_slowdisk_think10.log"),
    "v10_slowdev": load("v10_slowdev_think10.log"),
    "v10_cgcap": load("v10_cgroupcap_idle.log"),
    "v11_slowdev": load("v11_slowdev_ab.log"),
    "v11_pressure": load("v11_pressure_ab.log"),
    "v12_slowdev": load("v12_slowdev_ab.log"),
    "apps10": apps("v10_apps_mem300_evict.log"),
    "cost": {n.rsplit(".", 1)[0]: daemon_cost(n) for n in ("v7_apps_mem300.log", "v8_apps_mem400.log", "v8_pressure.log",
                                                            "v8_slowdisk_think10.log", "v8_slowdisk_think100.log", "vm2_pressure.log",
                                                            "v9_apps_mem300.log", "v9_apps_unlimited.log", "v9_pressure.log",
                                                            "v9_slowdisk_think10.log", "v10_slowdev_think10.log", "v11_pressure_ab.log",
                                                            "v11_slowdev_ab.log", "v12_slowdev_ab.log")},
    "sig": {n.rsplit(".", 1)[0]: sig(n) for n in LOGS},
    "willneed": json.load(open(os.path.join(RAW, "willneed_probe.json"))) if os.path.exists(os.path.join(RAW, "willneed_probe.json")) else None,
}
json.dump(data, open(os.path.join(HERE, "data.json"), "w"), indent=1)
print("pressure:", sorted(data["pressure"]), "noise:", sorted(data["noise"]), "willneed:", bool(data["willneed"]))
