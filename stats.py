"""Significance tests for every comparison in the raw logs (standard library only).

For each eval_runner log, each configuration is compared with "No Daemon" on per-run values:
throughput (reads or ops per run), median and p99 latency, and per-actor operation time for
the real-application workload. Reported: mean difference (%), its 95% confidence interval
(Welch), and the two-sided Welch's t-test p-value.

    python3 stats.py docs/raw/v6_pressure.log docs/raw/v6_noise.log ...   (default: all v3/v6/v7/v8 and vm2 logs)
"""
import ast
import glob
import math
import re
import sys


# ---------------------------------------------------------------- Student t distribution
def _betacf(a, b, x):
    """Continued fraction for the regularized incomplete beta function (Numerical Recipes)."""
    qab, qap, qam, c, d = a + b, a + 1, a - 1, 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for num in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1 + num * d
            d = 1 / (d if abs(d) > 1e-300 else 1e-300)
            c = 1 + num / c if abs(1 + num / c) > 1e-300 else 1e-300
            h *= d * c
        if abs(d * c - 1) < 1e-12:
            break
    return h


def betai(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbt) * _betacf(a, b, x) / a
    return 1 - math.exp(lbt) * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t, df):
    return betai(df / 2, 0.5, df / (df + t * t))


def t_crit(df, alpha=0.05):
    """Critical t for a two-sided interval, by bisection on the p-value."""
    lo, hi = 0.0, 100.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def welch(a, b):
    """Difference of means b - a with 95% CI and p-value (Welch's unequal-variance t-test)."""
    na, nb = len(a), len(b)
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    diff = mb - ma
    if se == 0:
        return diff, (diff, diff), 0.0 if diff else 1.0
    df = (va / na + vb / nb) ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    t = diff / se
    h = t_crit(df) * se
    return diff, (diff - h, diff + h), t_two_sided_p(abs(t), df)


# ---------------------------------------------------------------- log parsing
def per_run(path):
    """{config name: [ {reads, p50, p99, apps:{actor:{mean..}}}, ... ]} from iteration lines."""
    out, cur = {}, None
    for line in open(path):
        m = re.match(r"--- iteration \d+/\d+: (.+)", line)
        if m:
            cur = m[1].strip()
            continue
        m = re.match(r"\s+hit=([\d.na]+)% reads=(\d+) lat=(\{.*\})", line)
        if m and cur:
            lat = ast.literal_eval(m[3])
            out.setdefault(cur, []).append({"reads": int(m[2]), **lat})
            cur = None
    return out


def fmt_p(p):
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def report(path, base_prefix="1.", only=None):
    """Rows comparing every configuration (or those whose number is in `only`) with the one named base_prefix."""
    runs = per_run(path)
    base_name = next((k for k in runs if k.startswith(base_prefix)), None)
    if not base_name or len(runs[base_name]) < 2:
        return []
    base = runs[base_name]
    rows = []
    metrics = [("reads", "throughput (per run)"), ("p50", "median latency"), ("p99", "p99 latency"),
               ("pgscan", "pages scanned by reclaim"), ("pgscan_direct", "pages scanned by direct reclaim"),
               ("allocstall", "direct-reclaim stalls")]
    actors = sorted({a for r in base for a in r.get("apps", {})})
    for name, rr in runs.items():
        if name == base_name or len(rr) < 2 or (only and name.split(".")[0] not in only):
            continue
        for key, label in metrics:
            a = [r[key] for r in base if key in r]
            b = [r[key] for r in rr if key in r]
            if len(a) > 1 and len(b) > 1 and (sum(a) or sum(b)):
                d, (lo, hi), p = welch(a, b)
                m = sum(a) / len(a) or 1.0
                rows.append((name, label, len(a), len(b), 100 * d / m, 100 * lo / m, 100 * hi / m, p))
        for act in actors:
            a = [r["apps"][act]["mean"] for r in base if act in r.get("apps", {})]
            b = [r["apps"][act]["mean"] for r in rr if act in r.get("apps", {})]
            if len(a) > 1 and len(b) > 1:
                d, (lo, hi), p = welch(a, b)
                m = sum(a) / len(a)
                rows.append((name, f"{act} time per op", len(a), len(b), 100 * d / m, 100 * lo / m, 100 * hi / m, p))
    return rows


def table(rows):
    print("| Configuration | Metric | n (base, config) | Difference | 95% CI | p |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, label, na, nb, d, lo, hi, pv in rows:
        print(f"| {name.split('. ', 1)[-1]} | {label} | {na}, {nb} | {d:+.1f}% | [{lo:+.1f}%, {hi:+.1f}%] | {fmt_p(pv)} |")
    print()


def main():
    paths = sys.argv[1:] or sorted(p for p in glob.glob("docs/raw/v[3678]_*.log") + glob.glob("docs/raw/vm2_*.log")
                                   if "crashed" not in p)    # partial runs killed by the OOM killer
    print("# Significance tests\n")
    print("Each row compares a configuration with No Daemon on per-run values. Difference = (config - no daemon) / no daemon. "
          "95% CI and p-value from Welch's t-test (unequal variances). For time and latency, negative is better. "
          "Where a log also has Full AI variants (configurations 8-12, 15, 16), a second table compares them with plain Full AI; "
          "on the real-program logs, the in-kernel-filtering variants (12, 14) are compared with the library-tracing daemon (13).\n")
    for p in paths:
        rows = report(p)
        if not rows:
            continue
        print(f"## {p.split('/')[-1]}\n")
        table(rows)
        variants = report(p, base_prefix="4.", only={"8", "9", "10", "11", "12", "15", "16"})
        if variants:
            print("Against Full AI:\n")
            table(variants)
        filtered = report(p, base_prefix="13.", only={"4", "12", "14", "17"})
        if filtered:
            print("Against Full AI tracing libraries:\n")
            table(filtered)


if __name__ == "__main__":
    main()
