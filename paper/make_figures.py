"""Draws the paper's charts as SVG from the raw logs and converts them to PNG (macOS qlmanage + sips).

    python3 paper/make_figures.py
"""
import json
import os
import subprocess
import sys
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "report"))
from logparse import parse_summary  # noqa: E402

RAW = os.path.join(ROOT, "docs", "raw")
FIG = os.path.join(HERE, "figures")
os.makedirs(FIG, exist_ok=True)

INK, INK2, MUTED, GRID, SURF = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#ffffff"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
FONT = "Helvetica, Arial, sans-serif"


def text(x, y, s, size=13, fill=INK, anchor="start", weight="normal"):
    s = escape(str(s))
    # white halo keeps labels legible where they cross grid or reference lines
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}" stroke="{SURF}" stroke-width="3" '
            f'paint-order="stroke" stroke-linejoin="round">{s}</text>')


def bar(x, y0, w, h, fill):
    """Vertical bar from baseline y0 upward by h, 4px rounded data end, square at the baseline."""
    if h <= 0:
        return ""
    r = min(4, h, w / 2)
    top = y0 - h
    return (f'<path d="M{x:.1f},{y0:.1f} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} '
            f'H{x + w - r:.1f} Q{x + w:.1f},{top:.1f} {x + w:.1f},{top + r:.1f} V{y0:.1f} Z" fill="{fill}"/>')


def save(name, w, h, body):
    """Square canvas (qlmanage thumbnails are square), content centred, then crop to w x h."""
    side = max(w, h)
    dx, dy = (side - w) / 2, (side - h) / 2
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" viewBox="0 0 {side} {side}">'
           f'<rect width="{side}" height="{side}" fill="{SURF}"/><g transform="translate({dx},{dy})">{body}</g></svg>')
    p = os.path.join(FIG, name + ".svg")
    open(p, "w").write(svg)
    scale = 3
    subprocess.run(["qlmanage", "-t", "-s", str(side * scale), "-o", FIG, p], capture_output=True, check=True)
    png = p + ".png"
    subprocess.run(["sips", "-c", str(h * scale), str(w * scale), png, "--out", os.path.join(FIG, name + ".png")],
                   capture_output=True, check=True)
    os.remove(png)
    print("wrote", name + ".png")


# ------------------------------------------------------------------ Fig: regime outcomes (column-width small multiples)
def hbar(x0, y, w, h, fill):
    """Horizontal bar from x0 rightward by w, 4px rounded data end."""
    if w <= 0:
        return ""
    r = min(4, w, h / 2)
    return (f'<path d="M{x0:.1f},{y:.1f} H{x0 + w - r:.1f} Q{x0 + w:.1f},{y:.1f} {x0 + w:.1f},{y + r:.1f} '
            f'V{y + h - r:.1f} Q{x0 + w:.1f},{y + h:.1f} {x0 + w - r:.1f},{y + h:.1f} H{x0:.1f} Z" fill="{fill}"/>')


def fig_regimes():
    regimes = [
        ("Pressure", parse_summary(f"{RAW}/v6_pressure.log"), "4"),
        ("30% noise", parse_summary(f"{RAW}/v6_noise.log"), "4"),
        ("Slow disk", parse_summary(f"{RAW}/v3_slowdisk400MBs_think100ms_ballast9GB_chunked_willneed.log"), "5"),
    ]
    panels = [("(a) Throughput, reads per run", "reads", "higher is better"),
              ("(b) Median read latency (p50)", "p50", "lower is better"),
              ("(c) Tail read latency (p99)", "p99", "lower is better")]
    W = 440
    lx, x0, x1, vmax = 10, 112, 400, 3.0
    def X(v): return x0 + (x1 - x0) * min(v, vmax) / vmax
    body, y = "", 0
    for title, key, note in panels:
        body += text(lx, y + 22, title, 15, INK, weight="bold") + text(lx, y + 42, f"daemon \u00f7 no daemon, {note}", 13, INK2)
        top = y + 54
        for t in (0, 1, 2, 3):
            body += (f'<line x1="{X(t):.1f}" y1="{top}" x2="{X(t):.1f}" y2="{top + 3 * 28}" stroke="{MUTED if t == 1 else GRID}" '
                     f'stroke-width="{1.5 if t == 1 else 1}" {"stroke-dasharray=\"5 4\"" if t == 1 else ""}/>')
        for ri, (rname, d, dk) in enumerate(regimes):
            ratio = d[dk][key] / d["1"][key]
            good = ratio >= 1 if key == "reads" else ratio <= 1
            by = top + 4 + ri * 28
            body += text(lx, by + 15, rname, 13, INK2)
            body += hbar(x0, by, X(ratio) - x0, 20, BLUE if good else ORANGE)
            body += text(X(ratio) + 6, by + 15, f"{ratio:.2f}\u00d7", 13, INK, weight="bold")
        y = top + 3 * 28 + 6
        for t in (0, 1, 2, 3):
            body += text(X(t), y + 10, f"{t}\u00d7", 12, MUTED, "middle")
        y += 22
    body += (f'<rect x="{lx}" y="{y + 4}" width="13" height="13" rx="2" fill="{BLUE}"/>' + text(lx + 19, y + 15, "better", 13, INK2)
             + f'<rect x="{lx + 90}" y="{y + 4}" width="13" height="13" rx="2" fill="{ORANGE}"/>' + text(lx + 109, y + 15, "worse", 13, INK2)
             + f'<line x1="{lx + 180}" y1="{y + 10}" x2="{lx + 210}" y2="{y + 10}" stroke="{MUTED}" stroke-width="1.5" stroke-dasharray="5 4"/>'
             + text(lx + 216, y + 15, "parity with no daemon", 13, INK2))
    save("fig_regimes", W, int(y + 26), body)


# ------------------------------------------------------------------ Fig: misprediction penalty vs think time
def fig_misprediction():
    mc = {k: json.load(open(f"{RAW}/misprediction_{k}.json")) for k in ("uncapped", "cap100")}
    gaps = [0, 25, 50, 100, 200]
    W, H = 440, 330
    x0, x1, y0, y1 = 62, 425, 250, 30
    vmax = 250

    def X(g): return x0 + (x1 - x0) * g / 200
    def Y(v): return y0 - (y0 - y1) * max(v, 0) / vmax
    body = ""
    for t in range(0, 251, 50):
        body += f'<line x1="{x0}" y1="{Y(t):.1f}" x2="{x1}" y2="{Y(t):.1f}" stroke="{GRID}"/>' + text(x0 - 6, Y(t) + 4, f"{t}", 12, MUTED, "end")
    body += text(8, 14, "extra delay (ms)", 13, INK2)
    for g in gaps:
        body += text(X(g), y0 + 18, f"{g}", 12, MUTED, "middle")
    body += text((x0 + x1) / 2, y0 + 38, "think time before the real read (ms)", 13, INK2, "middle")
    body += f'<polyline points="{X(0)},{Y(200)} {X(200)},{Y(0)}" fill="none" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6 5"/>'
    for key, col in (("cap100", BLUE), ("uncapped", ORANGE)):
        pts = [(X(g), Y(mc[key]["E4_penalty_vs_no_prefetch_ms"][f"gap_{g}ms"]["mean_ms"])) for g in gaps]
        body += f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="none" stroke="{col}" stroke-width="2"/>'
        for a, b in pts:
            body += f'<circle cx="{a:.1f}" cy="{b:.1f}" r="5" fill="{col}" stroke="{SURF}" stroke-width="2"/>'
    v0 = mc["cap100"]["E4_penalty_vs_no_prefetch_ms"]["gap_0ms"]["mean_ms"]
    body += text(X(0) + 10, Y(v0) - 6, f"{v0:.0f} ms", 13, INK, weight="bold")
    body += text(X(200), Y(150), "model: 20 MB \u00f7 100 MB/s", 13, INK2, "end")
    body += text(X(200), Y(150) + 17, "\u2212 think time", 13, INK2, "end")
    body += text(X(100), Y(0) - 10, "uncapped disk: under 1 ms", 13, INK2, "middle")
    ly = 312
    body += (f'<line x1="{x0}" y1="{ly}" x2="{x0 + 22}" y2="{ly}" stroke="{BLUE}" stroke-width="2"/><circle cx="{x0 + 11}" cy="{ly}" r="5" fill="{BLUE}"/>'
             + text(x0 + 28, ly + 4, "100 MB/s disk", 13, INK2)
             + f'<line x1="{x0 + 135}" y1="{ly}" x2="{x0 + 157}" y2="{ly}" stroke="{ORANGE}" stroke-width="2"/><circle cx="{x0 + 146}" cy="{ly}" r="5" fill="{ORANGE}"/>'
             + text(x0 + 163, ly + 4, "uncapped", 13, INK2)
             + f'<line x1="{x0 + 250}" y1="{ly}" x2="{x0 + 274}" y2="{ly}" stroke="{MUTED}" stroke-width="2" stroke-dasharray="6 5"/>'
             + text(x0 + 280, ly + 4, "model", 13, INK2))
    save("fig_misprediction", W, H, body)


# ------------------------------------------------------------------ Fig: WILLNEED residency
def fig_willneed():
    p = f"{RAW}/willneed_probe.json"
    if not os.path.exists(p):
        print("skip fig_willneed (run willneed_probe.py first)")
        return
    rows = json.load(open(p))["results"]
    W = 440
    lx, x0, x1 = 8, 170, 390
    def X(v): return x0 + (x1 - x0) * v / 100
    body = text(lx, 16, "resident 600 ms after the request (% of 10 MB file)", 13, INK2)
    top = 30
    for t in (0, 50, 100):
        body += f'<line x1="{X(t):.1f}" y1="{top}" x2="{X(t):.1f}" y2="{top + len(rows) * 34}" stroke="{GRID}"/>'
    for i, r in enumerate(rows):
        v = 100 * r["resident"]
        by = top + 6 + i * 34
        body += text(lx, by + 16, r["label"].replace("|", " "), 13, INK2)
        body += hbar(x0, by, X(v) - x0, 22, BLUE if r["chunked"] else ORANGE)
        body += text(X(v) + 6, by + 16, f"{v:.0f}%", 13, INK, weight="bold")
    y = top + len(rows) * 34 + 4
    for t in (0, 50, 100):
        body += text(X(t), y + 12, f"{t}%", 12, MUTED, "middle")
    y += 26
    body += (f'<rect x="{lx}" y="{y}" width="13" height="13" rx="2" fill="{ORANGE}"/>' + text(lx + 19, y + 11, "one call, whole file", 13, INK2)
             + f'<rect x="{lx + 175}" y="{y}" width="13" height="13" rx="2" fill="{BLUE}"/>' + text(lx + 194, y + 11, "1 MB chunks", 13, INK2))
    save("fig_willneed", W, int(y + 20), body)


if __name__ == "__main__":
    fig_regimes()
    fig_misprediction()
    fig_willneed()
