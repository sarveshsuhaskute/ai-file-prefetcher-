"""Real-application workload: real programs on a real codebase (PostgreSQL source).

Three concurrent actors, each repeating a real task until the run ends:
  compile - `make clean` + `make` of PostgreSQL's src/port and src/common (gcc opens
            the same headers in the same order on every build)
  search  - `grep -rn` and `git grep` over src/backend
  python  - start python3 and import a fixed set of heavy standard-library modules
The metric is time per operation (one build, one search, one start-up) and operations
completed per run. Page-cache hit rate is not measured here: the files are opened by
the real tools, not by this script.

Setup (inside the VM, once):  git clone --depth 1 https://github.com/postgres/postgres ~/real/postgres
                              cd ~/real/postgres && ./configure --without-readline --without-zlib --without-icu --without-openssl
Prints the same summary lines eval_runner.py parses (hit rate reported as n/a).
"""
import argparse
import multiprocessing
import os
import subprocess
import time

# under sudo, "~" is root's home: resolve the invoking user's home instead
PG = os.environ.get("PG_SRC") or os.path.expanduser(f"~{os.environ.get('SUDO_USER', '')}/real/postgres")
IMPORTS = ("import email.mime.multipart, json, http.client, asyncio, xml.etree.ElementTree, sqlite3, "
           "unittest, argparse, decimal, logging, csv, ssl, tarfile, zipfile, urllib.request, "
           "concurrent.futures, multiprocessing, statistics, difflib, pydoc")


def op_compile():
    for d in ("src/port", "src/common"):
        subprocess.run(["make", "-s", "-C", d, "clean"], cwd=PG, capture_output=True)
        subprocess.run(["make", "-s", "-j1", "-C", d], cwd=PG, capture_output=True, check=True)


def op_search():
    subprocess.run(["grep", "-rn", "elog(ERROR", "src/backend"], cwd=PG, capture_output=True)
    subprocess.run(["git", "-c", "safe.directory=*", "grep", "-c", "palloc", "--", "src/backend"],
                   cwd=PG, capture_output=True, check=True)


def op_python():
    subprocess.run(["python3", "-c", IMPORTS], capture_output=True, check=True)


OPS = {"compile": op_compile, "search": op_search, "python": op_python}


def actor(name, duration, out):
    end, times = time.time() + duration, []
    while time.time() < end:
        t0 = time.perf_counter()
        OPS[name]()
        times.append((time.perf_counter() - t0) * 1000.0)
    out.append((name, times))


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("duration", nargs="?", type=int, default=60)
    ap.add_argument("--actors", default="compile,search,python")
    args = ap.parse_args()
    if not os.path.isdir(PG):
        raise SystemExit(f"PostgreSQL source not found at {PG} (see the docstring for setup)")

    mgr = multiprocessing.Manager()
    out = mgr.list()
    procs = [multiprocessing.Process(target=actor, args=(n, args.duration, out)) for n in args.actors.split(",")]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    allt = []
    for name, times in sorted(out):
        allt += times
        mean = sum(times) / len(times) if times else 0.0
        print(f"[apps] {name}: ops={len(times)} mean={mean:.1f} p50={pct(times, .5):.1f} p95={pct(times, .95):.1f}")
    allt.sort()
    print(f"Overall Cache Hit Rate: n/a (Total Reads: {len(allt)})")
    if allt:
        print(f"Read Latency ms: mean={sum(allt) / len(allt):.2f} p50={pct(allt, .5):.2f} "
              f"p95={pct(allt, .95):.2f} p99={pct(allt, .99):.2f}")


if __name__ == "__main__":
    main()
