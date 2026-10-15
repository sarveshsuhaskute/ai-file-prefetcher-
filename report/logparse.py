import re


def parse_summary(path):
    """Return {baseline-number: dict} from an eval_runner log's SUMMARY section."""
    txt = open(path).read()
    if "SUMMARY" not in txt:
        return {}
    out = {}
    for blk in txt.split("SUMMARY", 1)[1].split("Result: ")[1:]:
        name = blk.split("\n", 1)[0].strip()
        key = name.split(".", 1)[0]
        d = {"name": name}
        m = re.search(r"Mean Cache Hit Rate: ([\d.]+)% \+/- ([\d.]+)% \(n=(\d+)\) \| reads/run ~(\d+)", blk)
        d.update(hit=float(m[1]), sd=float(m[2]), n=int(m[3]), reads=int(m[4]))
        for k in ("mean", "p50", "p95", "p99"):
            d[k] = float(re.search(rf"read latency {k}: ([\d.]+) ms", blk)[1])
        d["runs_reads"] = [int(x) for x in re.search(r"reads: \[([\d, ]+)\]", blk)[1].split(",")]
        m = re.search(r"Useful Prefetches: (\d+) / (\d+)", blk)
        if m:
            d["useful"], d["issued"] = int(m[1]), int(m[2])
        m = re.search(r"Avg model\+dispatch time per open event: ([\d.]+) us", blk)
        if m:
            d["us_per_event"] = float(m[1])
        out[key] = d
    return out


def pct(new, old):
    return 100.0 * (new - old) / old
