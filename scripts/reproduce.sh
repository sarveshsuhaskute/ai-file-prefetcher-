#!/bin/bash
# The command behind every result log in docs/raw/. Run inside the VM (ebpf.yaml: 4 vCPU, 12 GB RAM;
# ebpf-small.yaml for the vm2_* logs), from the project folder, as root:
#
#   sudo bash scripts/reproduce.sh <name>          e.g.  sudo bash scripts/reproduce.sh v9_pressure
#   sudo bash scripts/reproduce.sh list
#
# Each run writes docs/raw/<name>.log (or .json); then `python3 stats.py docs/raw/<name>.log`.
# Times are approximate. Results vary between sessions; the write-ups report each session's numbers.
# The v3 logs predate the current defaults: their configuration 4 ("throttling on") is today's
# `--daemon-args="--reclaim-mb-s 200"`, and their configuration 5 is today's default `full`.
# Not listed: logs from before the WILLNEED fix (synthetic_*, realdata_*: superseded by v3) and the two
# v8_apps_mem300_crashed* runs, which the out-of-memory kill stopped (the fix is in v10_apps_mem300_evict).
set -e
cd "$(dirname "$0")/.."
R=docs/raw
E="python3 -u eval_runner.py"
cap() { systemd-run --scope -q -p "IOReadBandwidthMax=/dev/vda $1" -- "${@:2}"; }      # disk bandwidth cap (cgroup)
mem() { systemd-run --scope -q -p "MemoryMax=$1" -p MemorySwapMax=0 -- "${@:2}"; }      # memory limit (cgroup)
data() { python3 -u workload.py --setup --scale "$1" > /dev/null; }                     # test files in /tmp (or PREFETCH_DATA_DIR)

# slow device runs: 150 MB/s null_blk with BFQ, files on it, ~2.2 GB of page cache left
slowdev_up() {
  SCHED=bfq bash scripts/slow_device.sh
  bash scripts/slow_device_check.sh > /dev/null   # the harmful sessions (v12, v13) ran this check first
  export PREFETCH_DATA_DIR=/mnt/slow PREFETCH_IO_DEV=nullb0
  data 1.0
  sync; echo 3 > /proc/sys/vm/drop_caches
  BALLAST=$(awk '/MemAvailable/{printf "%.1f", $2/1048576 - 2.2}' /proc/meminfo)
}
slowdev_down() { unset PREFETCH_DATA_DIR PREFETCH_IO_DEV; umount /mnt/slow; modprobe -r null_blk; }

case "$1" in
  # --- kernel probes (minutes)
  willneed_probe) data 1.0; python3 willneed_probe.py ;;                                  # -> willneed_probe.json
  misprediction_uncapped) data 1.0; python3 misprediction_cost.py > $R/misprediction_uncapped.json ;;
  misprediction_cap400) data 1.0; cap 400M python3 misprediction_cost.py > $R/misprediction_cap400.json ;;
  misprediction_cap100) data 1.0; cap 100M python3 misprediction_cost.py > $R/misprediction_cap100.json ;;

  # --- memory pressure: 14 GB set, 4 GB ballast (~20 min each)
  v3_synthetic_14GB_ballast4GB_chunked_willneed) $E --scale 3.5 --ballast-gb 4 --iterations 4 --duration 40 --baselines none,full,full-nomem > $R/$1.log 2>&1 ;;
  v3_synthetic_noise30_ballast4GB_chunked_willneed) $E --scale 3.5 --noise 0.3 --ballast-gb 4 --iterations 3 --duration 40 --baselines none,full,full-nomem > $R/$1.log 2>&1 ;;
  v6_pressure) $E --scale 3.5 --ballast-gb 4 --iterations 5 --duration 40 --baselines none,ra-tuned,full,full-gate > $R/$1.log 2>&1 ;;
  v6_noise) $E --skip-setup --scale 3.5 --noise 0.3 --ballast-gb 4 --iterations 5 --duration 40 --baselines none,ra-tuned,full,full-gate > $R/$1.log 2>&1 ;;
  v8_pressure) $E --scale 3.5 --ballast-gb 4 --iterations 5 --warmup 1 --duration 40 --baselines none,full,full-evict,full-adapt > $R/$1.log 2>&1 ;;
  v9_pressure) data 3.5; $E --skip-setup --scale 3.5 --ballast-gb 4 --iterations 5 --warmup 1 --duration 40 --baselines none,full,full-evict > $R/$1.log 2>&1 ;;
  v11_pressure_ab) data 3.5; $E --skip-setup --scale 3.5 --ballast-gb 4 --iterations 5 --warmup 1 --duration 40 --baselines none,full,full-ab > $R/$1.log 2>&1 ;;

  # --- bandwidth-capped disk (cgroup, 400 MB/s), 9 GB ballast (~15 min each)
  v3_slowdisk400MBs_think10ms_ballast9GB_chunked_willneed) cap 400M $E --scale 1.0 --ballast-gb 9 --iterations 4 --duration 60 --baselines none,full,full-nomem > $R/$1.log 2>&1 ;;
  v8_slowdisk_think100) data 3.5; cap 400M $E --skip-setup --scale 1.0 --iterations 4 --warmup 1 --duration 60 --ballast-gb 9 --think-ms 100 --baselines none,full,full-adapt > $R/$1.log 2>&1 ;;
  v8_slowdisk_think10) data 3.5; cap 400M $E --skip-setup --scale 1.0 --iterations 4 --warmup 1 --duration 60 --ballast-gb 9 --think-ms 10 --baselines none,full,full-adapt > $R/$1.log 2>&1 ;;
  v9_slowdisk_think10) data 1.0; cap 400M $E --skip-setup --scale 1.0 --iterations 4 --warmup 1 --duration 60 --ballast-gb 9 --think-ms 10 --baselines none,full,full-adapt-auto > $R/$1.log 2>&1 ;;
  v10_cgroupcap_idle) echo bfq > /sys/block/vda/queue/scheduler; data 1.0
     cap 400M $E --skip-setup --scale 1.0 --iterations 4 --warmup 1 --duration 60 --ballast-gb 9 --think-ms 10 --baselines none,full,full-idle > $R/$1.log 2>&1
     echo mq-deadline > /sys/block/vda/queue/scheduler ;;

  # --- real programs on the PostgreSQL source (one-time set-up in realapps.py) (~20 min each)
  v7_apps_unlimited) $E --workload apps --baselines none,full --daemon-args=--include-libs --iterations 5 --warmup 1 --duration 60 > $R/$1.log 2>&1 ;;
  v7_apps_mem400) mem 400M $E --workload apps --baselines none,full --daemon-args=--include-libs --iterations 5 --warmup 1 --duration 60 > $R/$1.log 2>&1 ;;
  v7_apps_mem300) mem 300M $E --workload apps --baselines none,full --daemon-args=--include-libs --iterations 5 --warmup 1 --duration 60 > $R/$1.log 2>&1 ;;
  v8_apps_mem400) mem 400M $E --workload apps --baselines none,full,full-evict --daemon-args=--include-libs --iterations 5 --warmup 1 --duration 60 > $R/$1.log 2>&1 ;;
  v9_apps_mem300) mem 300M $E --workload apps --baselines none,full-libs,full,full-only-build --iterations 5 --warmup 1 --duration 60 > $R/$1.log 2>&1 ;;
  v9_apps_unlimited) $E --workload apps --baselines none,full-libs,full,full-only-build --iterations 5 --warmup 1 --duration 60 > $R/$1.log 2>&1 ;;
  v10_apps_mem300_evict) mem 300M $E --workload apps --baselines none,full,full-evict --daemon-args=--include-libs --iterations 5 --warmup 1 --duration 60 > $R/$1.log 2>&1 ;;

  # --- slow null_blk device (~20-25 min each)
  v10_slowdev_think10) slowdev_up; $E --skip-setup --scale 1.0 --iterations 4 --warmup 1 --duration 60 --ballast-gb "$BALLAST" --think-ms 10 --baselines none,full,full-idle,full-adapt-lat > $R/$1.log 2>&1; slowdev_down ;;
  v11_slowdev_ab) slowdev_up; $E --skip-setup --scale 1.0 --iterations 4 --warmup 1 --duration 60 --ballast-gb "$BALLAST" --think-ms 10 --baselines none,full,full-ab > $R/$1.log 2>&1; slowdev_down ;;
  v12_slowdev_ab) slowdev_up; $E --skip-setup --scale 1.0 --iterations 4 --warmup 1 --duration 60 --ballast-gb "$BALLAST" --think-ms 10 --baselines none,full,full-ab > $R/$1.log 2>&1; slowdev_down ;;
  v13_slowdev_gates) slowdev_up; $E --skip-setup --scale 1.0 --iterations 3 --warmup 1 --duration 90 --ballast-gb "$BALLAST" --think-ms 10 --baselines none,full,full-ab,full-tgate > $R/$1.log 2>&1; slowdev_down ;;

  # --- second machine (VM from ebpf-small.yaml: 2 vCPU, 4 GB) (~35 min)
  vm2_pressure) $E --scale 1.5 --iterations 5 --warmup 1 --duration 40 --baselines none,full,full-gate,full-evict,full-adapt > $R/$1.log 2>&1 ;;
  vm2_noise) $E --skip-setup --scale 1.5 --noise 0.3 --iterations 5 --warmup 1 --duration 40 --baselines none,full,full-gate > $R/$1.log 2>&1 ;;

  list|"") grep -oE '^  [A-Za-z0-9_]+\)' "$0" | tr -d ' )' ;;
  *) echo "unknown run: $1 (try: list)"; exit 1 ;;
esac
