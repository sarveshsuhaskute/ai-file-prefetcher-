#!/bin/bash
# Checks the slow device: its read bandwidth alone, then whether the scheduler lets a normal reader
# go first while an idle-class (ionice -c3) reader runs beside it. With BFQ the normal reader kept the
# full ~154 MB/s; with mq-deadline the two split it about evenly.
#
#   sudo bash scripts/slow_device_check.sh        (after scripts/slow_device.sh)
set -e
cd /mnt/slow
[ -f a.dat ] || { dd if=/dev/urandom of=a.dat bs=1M count=600 status=none; dd if=/dev/urandom of=b.dat bs=1M count=600 status=none; }
sync; echo 3 > /proc/sys/vm/drop_caches
echo "alone: $(dd if=a.dat of=/dev/null bs=1M 2>&1 | tail -1 | awk '{print $(NF-1), $NF}')"
sync; echo 3 > /proc/sys/vm/drop_caches
( ionice -c3 dd if=b.dat of=/dev/null bs=1M 2>&1 | tail -1 | awk '{print "idle-class reader: "$(NF-1), $NF}' ) &
sleep 0.5
echo "normal reader beside it: $(dd if=a.dat of=/dev/null bs=1M 2>&1 | tail -1 | awk '{print $(NF-1), $NF}')"
wait
rm -f a.dat b.dat
