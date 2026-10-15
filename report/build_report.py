"""Builds report/Project_Report.docx from the raw experiment logs in docs/raw/.

Every number in the results tables is parsed from those logs, so the text cannot drift from the data.
    python3 report/build_report.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from docx_writer import Doc          # noqa: E402
from logparse import parse_summary, pct   # noqa: E402

RAW = os.path.join(ROOT, "docs", "raw")
P_PRESS = parse_summary(f"{RAW}/v3_synthetic_14GB_ballast4GB_chunked_willneed.log")
P_NOISE = parse_summary(f"{RAW}/v3_synthetic_noise30_ballast4GB_chunked_willneed.log")
P_SLOW = parse_summary(f"{RAW}/v3_slowdisk400MBs_think100ms_ballast9GB_chunked_willneed.log")
P_PRE = parse_summary(f"{RAW}/synthetic_14GB_ballast4GB.log")
P_REAL = parse_summary(f"{RAW}/realdata_ballast10.2GB.log")
V6_PRESS = parse_summary(f"{RAW}/v6_pressure.log") if os.path.exists(f"{RAW}/v6_pressure.log") else {}
V6_NOISE = parse_summary(f"{RAW}/v6_noise.log") if os.path.exists(f"{RAW}/v6_noise.log") else {}
MC = {k: json.load(open(f"{RAW}/misprediction_{k}.json")) for k in ("uncapped", "cap400", "cap100")}


def row(label, d, extra=""):
    return [label, f"{d['hit']:.1f}% ± {d['sd']:.1f}", f"{d['reads']}", f"{d['p50']:.1f}", f"{d['p95']:.1f}", f"{d['p99']:.1f}", extra]


HDR = ["Configuration", "Hit rate", "Reads / run", "p50 (ms)", "p95 (ms)", "p99 (ms)", "Prefetches (useful)"]
W7 = [2300, 1150, 950, 800, 800, 800, 1500]


def pf(d):
    return f"{d['issued']} ({100 * d['useful'] / d['issued']:.1f}%)" if d.get("issued") else "-"


d = Doc()

# ------------------------------------------------------------------ title page
d.title("Context-Aware File Prefetching in Linux using eBPF and a Variable-Order Markov Model")
d.center("Design, implementation and evaluation of a userspace prefetch daemon against the default Linux readahead", 26, italic=True)
d.center(" ", 24)
d.center("Operating Systems Project Report", 28)
d.center(" ", 24)
d.center("Name: [Your name]          Roll No.: [Your roll number]", 24)
d.center("Course: [Course name and code]          Instructor: [Instructor name]", 24)
d.center("Institution: [Your institution]          Date: [Submission date]", 24)
d.pagebreak()

# ------------------------------------------------------------------ abstract
d.heading("Abstract", 1)
n = P_PRESS
d.para(
    "File access in many programs is repetitive: the same files tend to be opened in the same order. This project asks whether a small "
    "learned model, running next to the kernel, can use that regularity to load files into the page cache before they are requested, and "
    "whether doing so beats the Linux kernel's own readahead. The system traces `openat`, `read` and `close` with eBPF, feeds the events "
    "to a variable-order Markov model with a few context signals, and issues `posix_fadvise(POSIX_FADV_WILLNEED)` for files it is "
    "confident about.")
d.para(
    f"The first evaluation concluded that the prefetcher lost to the kernel. Investigation showed the conclusion was wrong: a single "
    f"`WILLNEED` request over a 10 MB file loads only about 12% of it on Linux 6.8, so the prefetcher was mostly doing nothing. After "
    f"issuing the request in 1 MB chunks, the daemon raised the cache hit rate from {n['1']['hit']:.0f}% to {n['5']['hit']:.0f}% and "
    f"throughput by {pct(n['5']['reads'], n['1']['reads']):.0f}% on a 14 GB working set squeezed into about 7 GB of cache, and cut median read "
    f"latency by {-pct(n['5']['p50'], n['1']['p50']):.0f}%. The benefit shrinks when 30% of accesses are unpredictable, and on a "
    f"bandwidth-limited disk it makes tail latency worse. A direct measurement of mispredictions shows why: a wrong prefetch delays the "
    f"file the program really needs by roughly (bytes prefetched / disk bandwidth) minus the program's think time.")
d.para("**Keywords:** page cache, readahead, prefetching, eBPF, Markov model, posix_fadvise, Linux kernel.")
d.pagebreak()

# ------------------------------------------------------------------ 1 introduction
d.heading("1. Introduction", 1)
d.heading("1.1 Motivation", 2)
d.para(
    "Reading a file that is not in memory costs a disk access, which is orders of magnitude slower than a memory copy. Operating systems "
    "hide part of this cost with a page cache and with readahead, which fetches the next blocks of a file that is being read sequentially. "
    "Readahead works inside one file and reacts only after the program has started reading. It cannot know that opening `a.h` is usually "
    "followed by opening `b.h` and `c.h`, which are different files.")
d.para(
    "Programs such as compilers, build systems, indexers and application launchers repeat such cross-file patterns. If a daemon learns the "
    "patterns, it can ask the kernel to load the likely next files while the program is still working on the current one.")
d.heading("1.2 Problem statement and goals", 2)
d.para("The project builds and evaluates a context-aware prefetching daemon for Linux with these goals:")
d.bullet("observe file accesses with low overhead using eBPF, without modifying applications or the kernel;")
d.bullet("learn access sequences online with a model small enough to run continuously;")
d.bullet("act on predictions through an existing kernel interface (`posix_fadvise`) and avoid harming the system when predictions are wrong;")
d.bullet("measure honestly, against the default kernel and against a tuned kernel readahead, including cases where the idea fails.")
d.heading("1.3 Contributions", 2)
d.bullet("A working eBPF tracing and prefetching pipeline with a tested model, actuator and evaluation harness.")
d.bullet("A measured kernel behaviour that is easy to miss: one `WILLNEED` call loads only about 12% of a 10 MB file; 1 MB chunks load all of it.")
d.bullet("An evaluation methodology that removes several sources of bias (writeback contamination, warm-up effects, run order, missing memory pressure).")
d.bullet("A measurement of the cost of a wrong prediction, with a simple formula that fits the data, and an estimate of the precision needed to break even.")
d.heading("1.4 Report structure", 2)
d.para("Section 2 gives background and related work. Section 3 describes the design and Section 4 the implementation. Section 5 explains the "
       "evaluation method, Section 6 gives results, Section 7 discusses them and their limits, and Section 8 concludes.")

# ------------------------------------------------------------------ 2 background
d.heading("2. Background and Related Work", 1)
d.heading("2.1 Page cache and readahead", 2)
d.para(
    "Linux keeps recently used file data in the page cache. When memory is short, the kernel reclaims cache pages, preferring those on the "
    "inactive list. Readahead (implemented in `mm/readahead.c`) detects sequential reads within a file and enlarges the read window "
    "adaptively. The window is bounded per block device by `read_ahead_kb`. The project uses this knob as a stronger baseline than the default.")
d.heading("2.2 posix_fadvise and mincore", 2)
d.para(
    "`posix_fadvise(fd, offset, len, POSIX_FADV_WILLNEED)` asks the kernel to start reading a range into the cache; `POSIX_FADV_DONTNEED` "
    "asks it to drop clean pages. `mincore` reports which pages of a mapped file are resident, which gives a direct measurement of what is "
    "actually in the cache, independent of what a prefetcher believes it has done. The project relies on `mincore` for its main metric.")
d.heading("2.3 eBPF", 2)
d.para(
    "eBPF lets small verified programs run inside the kernel at tracepoints. They can filter events and pass compact records to userspace "
    "through a ring buffer. This avoids the cost of tracing every system call from userspace with `ptrace` or `strace`. The project uses the "
    "BCC toolkit to compile and load the programs.")
d.heading("2.4 Related work", 2)
d.para(
    "Predicting file accesses from history has a long record. Griffioen and Appleton used probability graphs over file-open sequences to "
    "prefetch files [1]. Kroeger and Long extended this with context models that predict from the last several events, the idea behind the "
    "variable-order model used here [2]. Desktop daemons such as `preload` learn which application binaries to load at start-up [5]. "
    "This project differs mainly in its tracing method (eBPF), its use of process and time context, and its attention to the harm caused "
    "by wrong predictions under memory and bandwidth pressure.")

# ------------------------------------------------------------------ 3 design
d.heading("3. System Design", 1)
d.heading("3.1 Architecture", 2)
d.para(
    "Figure 1 shows the original design. The kernel side traces three system calls and emits events through a ring buffer. The userspace side "
    "consumes events, updates a model, ranks candidate next files by confidence, and sends prefetch advice back to the kernel. The "
    "implemented system follows the left and centre of the figure. The 'eviction priority hint' hook shown in the figure was not built; "
    "eviction is limited to dropping unused prefetched pages with `DONTNEED` when memory pressure is detected.")
d.picture(os.path.join(ROOT, "Architectural flow .png"), width_in=2.7)
d.caption("Figure 1. Original architecture. The eviction-priority hook (dashed line) was not implemented.")
d.heading("3.2 Event collection", 2)
d.para(
    "Four tracepoints are attached: `sys_enter_openat`, `sys_exit_openat`, `sys_enter_read` and `sys_enter_close`. The entry probe copies the "
    "path, a filter discards noise (`/proc`, `/sys`, `/dev`, `/etc`, shared libraries, bytecode and locale or cache directories), and the exit "
    "probe keeps the path only if the open succeeded, storing it in a per-process map keyed by file descriptor. Events (open, read, close) "
    "are written to a ring buffer. The daemon's own process is excluded so it does not trace itself.")
d.heading("3.3 Prediction model", 2)
d.para(
    "For each process the model keeps the last W = 5 opened files. For every suffix of that history it counts which file came next. To "
    "predict, it combines the distributions of all matching suffixes, weighting a suffix by its length L:")
d.code("p_seq(x) = sum over suffixes ( L * P(x | suffix) ) / sum over suffixes ( L )")
d.para(
    "Longer matches therefore count more, which is the idea of variable-order prediction. In the `full` mode the score is also adjusted by "
    "context: how often the file was opened by this process name, in this six-hour time-of-day bucket, and how common its extension is for "
    "this process. The final confidence is 0.6 × p_seq + 0.4 × p_context. Files with confidence of at least 0.5 are candidates.")
d.heading("3.4 Actuation", 2)
d.para("The actuator turns ranked candidates into prefetch requests and contains the safeguards that came out of the evaluation:")
d.bullet("**top-k:** at most two prefetches per open event;")
d.bullet("**cooldown:** a file prefetched in the last five seconds is not requested again;")
d.bullet("**chunked WILLNEED:** requests are issued in 1 MB chunks, up to 64 MB per file (Section 6.1 explains why);")
d.bullet("**worker pool:** system calls run on two background threads so the event loop is not blocked, with a bounded queue;")
d.bullet("**optional guards:** a cap on bytes in flight, a memory-pressure throttle (reclaim rate, PSI, available memory), and a "
         "precision gate that raises the confidence bar when recent prefetches are mostly wrong.")
d.heading("3.5 Time and space complexity", 2)
d.para("Let W be the history window, F the number of distinct successors of a context, k the number of prefetches per event, S the file "
       "size and c the 1 MB chunk size.")
d.table([
    ["Operation", "Time", "Note"],
    ["Learning one event", "O(W²)", "W suffix slices, each hashed in O(W)"],
    ["Predicting", "O(W² + W·F + M log M)", "M ≤ W·F candidates are sorted"],
    ["Dispatch", "O(k)", "stops after k prefetches"],
    ["One prefetch", "O(min(S, 64 MB) / c) syscalls", "on a worker thread"],
    ["Model memory", "O(C · F)", "C ≤ 200,000 contexts (pruned)"],
], [2600, 3200, 3200])
d.para(
    "With W fixed at 5 and small F, handling an event is effectively constant time; the measured cost was 20 to 140 microseconds per open "
    "event. A wrong prediction costs the same computation as a right one. Its extra cost is wasted I/O of O(k·S) bytes and a delay to the "
    "needed file of about max(0, k·S/B − g), where B is disk bandwidth and g is the time the program spends before it asks for the "
    "next file (Section 6.5).")

# ------------------------------------------------------------------ 4 implementation
d.heading("4. Implementation", 1)
d.table([
    ["File", "Purpose"],
    ["prefetch_daemon.py", "BPF programs, ring-buffer loop, command-line options, statistics"],
    ["prefetch_model.py", "Variable-order Markov model, pruning, JSON save/load"],
    ["prefetch_actuator.py", "Chunked WILLNEED, top-k, cooldown, pressure and precision guards, worker pool"],
    ["workload.py / realworld.py", "Synthetic cyclic workload; workload on real files (directory walks, C header includes)"],
    ["eval_runner.py", "Isolated, interleaved baseline runs with latency and pressure reporting"],
    ["simulate.py", "Offline cache simulator using the real model"],
    ["misprediction_cost.py", "Direct measurement of wrong-prefetch cost"],
    ["tests/", "22 unit tests for the model and actuator"],
], [3000, 6000])
d.para(
    "The daemon is written in Python with BCC. The BPF programs are C compiled at start-up. The model and actuator are separated from the "
    "tracing code so that they run without root or eBPF and are covered by unit tests (also run by a continuous-integration workflow). "
    "Models are saved as JSON with an atomic rename. An earlier version used `pickle`, which was replaced because loading a pickle from a "
    "world-writable directory as root can execute arbitrary code.")

# ------------------------------------------------------------------ 5 methodology
d.heading("5. Evaluation Methodology", 1)
d.heading("5.1 Environment", 2)
d.para("All measurements ran in a Lima virtual machine on an Apple-silicon Mac: Ubuntu with Linux 6.8, 4 virtual CPUs, 11.9 GB of RAM and a "
       "virtio disk. Because this disk is very fast, some experiments cap disk bandwidth with a cgroup (`io.max`) to emulate slower storage.")
d.heading("5.2 Workloads", 2)
d.bullet("**Synthetic cyclic:** three concurrent actors read 10 MB files in a fixed cyclic order (100, 80 and 120 files at scale 1; 3.5 times "
         "that at scale 3.5, about 14 GB in total). One actor switches to a new file set halfway to test adaptation. An optional noise "
         "parameter replaces a fraction of accesses with random files, and an optional think time sleeps between files.")
d.bullet("**Real data:** three actors walk `/usr/share` and `/usr/lib` in directory order and a third follows real `#include` chains "
         "through the C headers in `/usr/include`.")
d.heading("5.3 Memory pressure", 2)
d.para("A ballast process holds anonymous memory so that the page cache has less room than the working set. The 14 GB runs use a 4 GB "
       "ballast, leaving about 7 GB of cache.")
d.heading("5.4 Metrics", 2)
d.bullet("**Hit rate:** the fraction of a file resident in the page cache (`mincore`) just before it is read. This measures cache contents directly.")
d.bullet("**Throughput:** file reads completed in a fixed time (40 or 60 seconds).")
d.bullet("**Latency:** time to open and read each file, reported as mean, median (p50), p95 and p99.")
d.bullet("**Precision:** the share of prefetched files that were read within two seconds. This is the daemon's own estimate and is *not* "
         "evidence that pages are resident (see Section 6.1).")
d.heading("5.5 Removing bias from the harness", 2)
d.para("The first evaluation produced results that later proved unreliable. The harness now does the following before every run:")
d.bullet("flushes data and waits for writeback to finish before dropping the page cache, because dirty pages from creating 14 GB of test files "
         "slowed the first run (one configuration scored 26.8% in its first run and about 72% in the next two);")
d.bullet("discards one warm-up run;")
d.bullet("interleaves baselines within each iteration so that slow drift in the VM affects all of them equally;")
d.bullet("reports latency percentiles and peak memory pressure (PSI), not only the hit rate.")
d.para("The original 14 GB test also turned out not to be under memory pressure: pressure stall information stayed at zero and no pages were "
       "being reclaimed. A deliberate ballast was needed to create real pressure.")

# ------------------------------------------------------------------ 6 results
d.heading("6. Results", 1)
d.heading("6.1 The first result, and the bug behind it", 2)
pre = P_PRE
d.para(
    f"The first re-measurement on the cleaned-up harness (4 GB ballast, 5 runs per configuration) still found no benefit. The hit rate was "
    f"{pre['1']['hit']:.1f}% with no daemon, {pre['6']['hit']:.1f}% with the model running but not prefetching, and {pre['4']['hit']:.1f}% and "
    f"{pre['5']['hit']:.1f}% with prefetching (throttled and unthrottled). Throughput was no better with prefetching. At the same time "
    f"the daemon reported about 99% prefetch precision, which did not fit with a cache that stayed cold.")
d.para(
    "The explanation was found by testing the system call directly. After evicting a 10 MB file, a single "
    "`posix_fadvise(WILLNEED)` over the whole file left only about 12% of it resident, and `readahead(2)` behaved the same; issuing the "
    "request in 1 MB chunks made 100% resident, with or without a disk cap. The kernel silently limits how much one request reads. The "
    "reported precision only meant that the file was read soon after the request, not that its pages had been loaded. The actuator now "
    "issues chunked requests, and all later results use it. The earlier results are kept in the repository as superseded.")
d.heading("6.2 Memory pressure, predictable access", 2)
d.para("The 14 GB working set with a 4 GB ballast, four runs per configuration:")
d.table([HDR, row("No daemon", n["1"]), row("Daemon, throttle on (old setting)", n["4"], pf(n["4"])), row("Daemon, no throttle", n["5"], pf(n["5"]))], W7)
d.para(
    f"Without throttling, the hit rate rose from {n['1']['hit']:.1f}% to {n['5']['hit']:.1f}%, reads per run rose by "
    f"{pct(n['5']['reads'], n['1']['reads']):.0f}%, median latency fell by {-pct(n['5']['p50'], n['1']['p50']):.0f}% and p99 by "
    f"{-pct(n['5']['p99'], n['1']['p99']):.0f}%. The throttled configuration matched the no-daemon baseline: its reclaim-rate threshold "
    f"(200 MB/s) was exceeded almost every time, so it issued {n['4']['issued']} prefetches and refused thousands. The throttle was therefore "
    f"made optional and is off by default.")
d.heading("6.3 Unpredictable access", 2)
q = P_NOISE
d.para("The same setup with 30% of accesses replaced by random files, three runs per configuration:")
d.table([HDR, row("No daemon", q["1"]), row("Daemon, throttle on", q["4"], pf(q["4"])), row("Daemon, no throttle", q["5"], pf(q["5"]))], W7)
d.para(
    f"The hit rate still improved ({q['1']['hit']:.1f}% to {q['5']['hit']:.1f}%), but throughput changed by only "
    f"{pct(q['5']['reads'], q['1']['reads']):+.1f}% and the p95 and p99 latencies were worse. Only about "
    f"{100 * q['5']['useful'] / q['5']['issued']:.0f}% of prefetches were used, so more than half of the speculative reads were wasted and "
    f"competed with real reads.")
d.heading("6.4 Bandwidth-limited disk with think time", 2)
s = P_SLOW
d.para("The disk was capped at 400 MB/s, each file was followed by 100 ms of think time (idle time in which prefetching could overlap), and "
       "a 9 GB ballast was used. Four runs per configuration:")
d.table([HDR, row("No daemon", s["1"]), row("Daemon, throttle on", s["4"], pf(s["4"])), row("Daemon, no throttle", s["5"], pf(s["5"]))], W7)
d.para(
    f"Prefetching raised the hit rate from {s['1']['hit']:.1f}% to {s['5']['hit']:.1f}% and reads by {pct(s['5']['reads'], s['1']['reads']):.1f}%, "
    f"and lowered the median latency, but the p99 latency rose from {s['1']['p99']:.0f} ms to {s['5']['p99']:.0f} ms. With about 99% of prefetches "
    f"useful, wrong guesses cannot explain this. The prefetch reads themselves queue ahead of demand reads on the limited disk.")
d.heading("6.5 The cost of a wrong prediction", 2)
d.para("To measure the cost directly, `misprediction_cost.py` prefetches two wrong 10 MB files and then reads the file the program actually "
       "needs, after a gap that stands for the program's think time. It uses the daemon's own prefetch routine and 20 trials per cell.")
rows = [["Disk", "Wrong file fully loaded in", "Needed file: no prefetch", "Needed file: prefetched right"]]
for k, lab in (("uncapped", "Uncapped virtio"), ("cap400", "Capped 400 MB/s"), ("cap100", "Capped 100 MB/s")):
    m = MC[k]
    rows.append([lab, f"{m['E1_wrong_file_time_to_resident_ms']['mean']:.1f} ms", f"{m['E2_needed_file_no_prefetch_ms']['mean']:.1f} ms",
                 f"{m['E3_needed_file_prefetched_right_ms']['mean']:.1f} ms"])
d.table(rows, [2200, 2400, 2200, 2200])
rows = [["Think time before the read", "Uncapped", "Capped 400 MB/s", "Capped 100 MB/s", "Model at 100 MB/s"]]
for g in (0, 25, 50, 100, 200):
    key = f"gap_{g}ms"
    cells = [f"{g} ms"]
    for k in ("uncapped", "cap400", "cap100"):
        cells.append(f"{MC[k]['E4_penalty_vs_no_prefetch_ms'][key]['mean_ms']:+.1f} ms")
    cells.append(f"{max(0, 200 - g)} ms")
    rows.append(cells)
d.para("Extra delay to the needed file compared with not prefetching at all:")
d.table(rows, [2200, 1500, 1800, 1800, 1700])
d.para(
    "On the 100 MB/s disk the measured penalty follows the model max(0, k·S/B − g) closely: two wrong 10 MB files are 20 MB, which "
    "takes 200 ms at 100 MB/s, and each extra millisecond of think time removes a millisecond of penalty. On the fast uncapped disk the "
    "penalty is below one millisecond and within noise. The 400 MB/s cap behaved in bursts and did not follow the formula, so it should not "
    "be used to calibrate the model. Using these values on the 100 MB/s disk, a correct prediction saves about 42 ms and a wrong one "
    "costs up to 207 ms, so the prefetcher needs roughly 83% precision at zero think time, or about 71% at 100 ms, to break even. This "
    "is a rough estimate that treats each event as all-or-nothing.")
d.heading("6.6 Offline simulation", 2)
d.para("An offline simulator replays synthetic traces through an LRU cache using the same model. With a cache large enough to hold every "
       "file, all policies were within noise of demand paging, because only first accesses miss and no model can predict a file it has never "
       "seen. With a cache one third the size of the working set, the model raised the hit rate on the cyclic trace from 0% to 97.5% and on a "
       "branching trace from 57% to 74%, and was neutral on a trace with no structure. The simulated LRU is stricter than the real kernel, "
       "so these figures show direction, not magnitude.")
if V6_PRESS or V6_NOISE:
    d.heading("6.7 Comparison with tuned kernel readahead", 2)
    d.para("To check that the daemon is compared against a fair baseline, the kernel's own readahead window was raised to 8 MB "
           "(`read_ahead_kb = 8192`) and the precision gate (minimum precision 0.6, gate threshold 0.9) was added as a configuration.")
    HD2 = ["Configuration", "Hit rate", "Reads / run", "p50 (ms)", "p95 (ms)", "p99 (ms)", "n"]
    for title, V in (("14 GB working set, predictable access:", V6_PRESS), ("Same, with 30% random accesses:", V6_NOISE)):
        if V:
            d.para(title)
            d.table([HD2] + [[v["name"].split(". ", 1)[1], f"{v['hit']:.1f}% ± {v['sd']:.1f}", str(v["reads"]), f"{v['p50']:.1f}",
                              f"{v['p95']:.1f}", f"{v['p99']:.1f}", str(v["n"])] for k, v in sorted(V.items())], [3000, 1300, 1000, 800, 800, 800, 500])

# ------------------------------------------------------------------ 7 discussion
d.heading("7. Discussion", 1)
d.heading("7.1 What the results mean", 2)
d.bullet("Learned prefetching helps when three conditions hold: accesses are predictable, the working set exceeds the cache, and there is spare "
         "I/O capacity. In the pressure experiment all three held and the gain was large.")
d.bullet("When the working set fits in memory there is nothing to gain; the cache holds everything after the first pass.")
d.bullet("When accesses are partly random, the hit rate still improves but the extra reads cost as much as they save. The "
         "break-even analysis in Section 6.5 explains this.")
d.bullet("When disk bandwidth is the bottleneck, prefetching reorders I/O but cannot add bandwidth, so it trades median latency for tail latency.")
d.bullet("The model itself is cheap: tens of microseconds per event. The cost that matters is I/O.")
d.heading("7.2 Lessons from the investigation", 2)
d.bullet("An actuator must be checked against the system's real state. A precision figure based on the prefetcher's own bookkeeping "
         "hid a bug for the whole first evaluation; `mincore` exposed it.")
d.bullet("Two explanations in the first report, high userspace overhead and 'eviction blindness', were not supported once the harness was "
         "fixed. A 26% throughput loss reported at first was most likely produced by the contaminated first run.")
d.bullet("Careful experimental hygiene changed the conclusion more than any change to the model did.")
d.heading("7.3 Threats to validity and limitations", 2)
d.bullet("Everything ran on one virtual machine with 3 to 5 runs per configuration and 40 to 60 seconds per run.")
d.bullet("The main workload is synthetic and perfectly periodic; the real-data workload is small and CPU-bound, and its results with the "
         "fixed prefetcher are not conclusive. Earlier real-data runs were affected by the daemon's own memory footprint under very tight memory.")
d.bullet("The 'slow disk' is a cgroup bandwidth cap, not slow hardware, and the cap is bursty.")
d.bullet("One run in the first evaluation was an unexplained outlier and is reported as such. A later repeat of the slow-disk experiment "
         "did not reproduce the original regime (the baseline hit rate was far higher), so those results are not directly comparable.")
d.bullet("Prediction is at whole-file granularity. Files opened with relative paths cannot be prefetched, because the daemon resolves paths "
         "from its own working directory.")
d.bullet("The precision gate is implemented and unit-tested; " + ("its effect is reported in Section 6.7." if (V6_PRESS or V6_NOISE) else "its effect on performance has not been measured yet, so no benefit is claimed for it."))

# ------------------------------------------------------------------ 8 conclusion
d.heading("8. Conclusion and Future Work", 1)
d.para(
    f"A small learned model driven by eBPF traces can beat the Linux kernel's readahead when the workload is predictable and the cache is under "
    f"pressure: on the 14 GB test it improved throughput by {pct(n['5']['reads'], n['1']['reads']):.0f}% and halved median latency. It does not help "
    "when everything fits in memory, gives little when access is unpredictable, and can worsen tail latency when the disk is the bottleneck. "
    "The cost of a wrong prediction can be estimated from file size, disk bandwidth and think time, which tells a designer how accurate a "
    "prefetcher must be before it is worth running. The most important finding is methodological: the first evaluation reached the opposite "
    "conclusion because of an unnoticed kernel limit and a biased harness.")
d.para("Future work:")
d.bullet("measure on real slow storage (hard disk or throttled network volume) and on more than one machine;")
d.bullet("predict offsets within files, so large files are not prefetched whole;")
d.bullet("bound in-flight prefetch bytes adaptively to protect tail latency;")
d.bullet("move the model into eBPF maps; given the measured 20 to 140 microseconds per event, this is a lower priority than reducing wasted I/O;")
d.bullet("compare against other prefetchers and against application-specific hints such as `readahead(2)` calls placed by the program.")

# ------------------------------------------------------------------ references
d.heading("References", 1)
d.para("[1] J. Griffioen and R. Appleton, \"Reducing File System Latency using a Predictive Approach,\" USENIX Summer Technical Conference, 1994.")
d.para("[2] T. M. Kroeger and D. D. E. Long, \"Predicting File System Actions from Prior Events,\" USENIX Annual Technical Conference, 1996.")
d.para("[3] Linux manual pages: posix_fadvise(2), readahead(2), mincore(2); Linux kernel source, mm/readahead.c.")
d.para("[4] B. Gregg, BPF Performance Tools, Addison-Wesley, 2019; the BCC project (iovisor/bcc).")
d.para("[5] The `preload` adaptive prefetching daemon (Debian package).")
d.para("[6] Linux kernel documentation: Pressure Stall Information (Documentation/accounting/psi.rst).")

# ------------------------------------------------------------------ appendix
d.heading("Appendix A. Reproducing the results", 1)
d.para("Inside a Linux VM with the BCC package installed, as root:")
d.code("python3 -m unittest discover -s tests\n"
       "python3 eval_runner.py --baselines none,full --scale 3.5 --iterations 5 --ballast-gb 4\n"
       "python3 eval_runner.py --baselines none,full --scale 3.5 --skip-setup --noise 0.3 --ballast-gb 4\n"
       "python3 misprediction_cost.py\n"
       "python3 simulate.py")
d.para("Raw logs for every table are in `docs/raw/`.")

out = os.path.join(HERE, "Project_Report.docx")
d.save(out)
print("wrote", out)
