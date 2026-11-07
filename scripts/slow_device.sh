#!/bin/bash
# Creates the slow block device used in docs/evaluation_v3.md section F and mounts it at /mnt/slow.
#
# null_blk is a real blk-mq block device whose driver limits its bandwidth. Unlike a cgroup cap
# (blk-throttle queues requests above the I/O scheduler), its requests queue in the scheduler, as
# on slow storage, so I/O priority classes apply. Memory-backed: the 5 GB it holds is taken from RAM.
#
#   sudo SCHED=bfq bash scripts/slow_device.sh            # as in the experiments
#   sudo SCHED=bfq MBPS=150 QD=4 bash scripts/slow_device.sh
#   sudo umount /mnt/slow && sudo modprobe -r null_blk    # remove it (frees the memory)
#
# Needs the null_blk and bfq modules: sudo apt-get install linux-modules-extra-$(uname -r)
# Then run the harness with PREFETCH_DATA_DIR=/mnt/slow PREFETCH_IO_DEV=nullb0 (see scripts/reproduce.sh).
set -e
MBPS=${MBPS:-150}      # bandwidth limit, MiB/s
QD=${QD:-4}            # hardware queue depth: shallow, so the backlog waits in the scheduler, not the driver
GB=${GB:-5}
SCHED=${SCHED:-bfq}    # bfq honours the idle I/O class; mq-deadline did not in our test (slow_device_check.sh)

if [ ! -b /dev/nullb0 ]; then
  modprobe -r null_blk 2>/dev/null || true
  modprobe null_blk nr_devices=1 gb="$GB" bs=4096 memory_backed=1 mbps="$MBPS" queue_mode=2 hw_queue_depth="$QD"
  mkfs.ext4 -q -F /dev/nullb0
fi
modprobe bfq 2>/dev/null || true
mkdir -p /mnt/slow
mountpoint -q /mnt/slow || mount /dev/nullb0 /mnt/slow
chmod 1777 /mnt/slow
echo "$SCHED" > /sys/block/nullb0/queue/scheduler
echo "nullb0: ${MBPS} MiB/s, queue depth ${QD}, scheduler: $(cat /sys/block/nullb0/queue/scheduler)"
