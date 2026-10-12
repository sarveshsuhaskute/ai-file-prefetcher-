import argparse
import ctypes as ct
import datetime
import os
import resource
import signal
import sys
import time

from prefetch_actuator import Actuator, MemoryMonitor, PrecisionTracker
from prefetch_model import PrefetchModel

bpf_source = r"""
#include <linux/sched.h>
#include <uapi/linux/ptrace.h>
#include <linux/fs.h>

struct event_t {
    u32 pid;
    u32 type;
    char comm[16];
    char filename[256];
};

BPF_RINGBUF_OUTPUT(events, 8);

struct val_t {
    char filename[256];
};
BPF_HASH(infotmp, u64, struct val_t);

struct fd_key_t {
    u32 pid;
    u32 fd;
};
BPF_HASH(fdmap, struct fd_key_t, struct val_t);

static inline bool filter_noise(const char *filename) {
    if (filename[0] == 0) return true;
    if (filename[0] == '/' && filename[1] == 'p' && filename[2] == 'r' && filename[3] == 'o' && filename[4] == 'c') return true;
    if (filename[0] == '/' && filename[1] == 's' && filename[2] == 'y' && filename[3] == 's') return true;
    if (filename[0] == '/' && filename[1] == 'd' && filename[2] == 'e' && filename[3] == 'v') return true;
    if (filename[0] == '/' && filename[1] == 'e' && filename[2] == 't' && filename[3] == 'c') return true;

    for (int i = 0; i < 250 && i < 256; i++) {
        if (filename[i] == '\0') break;
        if (filename[i] == '.' && filename[i+1] == 's' && filename[i+2] == 'o') return true;
        if (filename[i] == '.' && filename[i+1] == 'p' && filename[i+2] == 'y' && filename[i+3] == 'c') return true;
        if (filename[i] == '.' && filename[i+1] == 'c' && filename[i+2] == 'a' && filename[i+3] == 'c' && filename[i+4] == 'h' && filename[i+5] == 'e') return true;
        if (filename[i] == 'l' && filename[i+1] == 'o' && filename[i+2] == 'c' && filename[i+3] == 'a' && filename[i+4] == 'l' && filename[i+5] == 'e') return true;
    }
    return false;
}

int trace_enter_openat(struct tracepoint__syscalls__sys_enter_openat *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    if (pid_tgid >> 32 == DAEMON_PID) return 0;
    struct val_t val = {};
    bpf_probe_read_user_str(&val.filename, sizeof(val.filename), ctx->filename);
    if (filter_noise(val.filename)) return 0;
    infotmp.update(&pid_tgid, &val);
    return 0;
}

int trace_exit_openat(struct tracepoint__syscalls__sys_exit_openat *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    if (pid == DAEMON_PID) return 0;
    int ret = ctx->ret;
    
    struct val_t *valp = infotmp.lookup(&pid_tgid);
    if (valp == 0) return 0;
    
    if (ret >= 0) {
        struct fd_key_t key = {.pid = pid, .fd = (u32)ret};
        fdmap.update(&key, valp);
        
        struct event_t *event = events.ringbuf_reserve(sizeof(struct event_t));
        if (event) {
            event->pid = pid;
            event->type = 1; 
            bpf_get_current_comm(&event->comm, sizeof(event->comm));
            __builtin_memcpy(&event->filename, valp->filename, sizeof(event->filename));
            events.ringbuf_submit(event, 0);
        }
    }
    infotmp.delete(&pid_tgid);
    return 0;
}

int trace_enter_read(struct tracepoint__syscalls__sys_enter_read *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    if (pid == DAEMON_PID) return 0;
    u32 fd = ctx->fd;
    struct fd_key_t key = {.pid = pid, .fd = fd};
    struct val_t *valp = fdmap.lookup(&key);
    if (valp) {
        struct event_t *event = events.ringbuf_reserve(sizeof(struct event_t));
        if (event) {
            event->pid = pid;
            event->type = 2; 
            bpf_get_current_comm(&event->comm, sizeof(event->comm));
            __builtin_memcpy(&event->filename, valp->filename, sizeof(event->filename));
            events.ringbuf_submit(event, 0);
        }
    }
    return 0;
}

int trace_enter_close(struct tracepoint__syscalls__sys_enter_close *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    if (pid == DAEMON_PID) return 0;
    u32 fd = ctx->fd;
    struct fd_key_t key = {.pid = pid, .fd = fd};
    struct val_t *valp = fdmap.lookup(&key);
    if (valp) {
        struct event_t *event = events.ringbuf_reserve(sizeof(struct event_t));
        if (event) {
            event->pid = pid;
            event->type = 3; 
            bpf_get_current_comm(&event->comm, sizeof(event->comm));
            __builtin_memcpy(&event->filename, valp->filename, sizeof(event->filename));
            events.ringbuf_submit(event, 0);
        }
        fdmap.delete(&key);
    }
    return 0;
}
"""

class Event(ct.Structure):
    _fields_ = [
        ("pid", ct.c_uint32),
        ("type", ct.c_uint32),
        ("comm", ct.c_char * 16),
        ("filename", ct.c_char * 256),
    ]

EV_OPEN, EV_READ, EV_CLOSE = 1, 2, 3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=PrefetchModel.MODES, default="full")
    parser.add_argument("--model-path", default=os.path.expanduser("~/.cache/prefetch_model.json"))
    parser.add_argument("--top-k", type=int, default=2, help="max prefetches per open event")
    parser.add_argument("--threshold", type=float, default=0.5, help="min confidence to prefetch")
    parser.add_argument("--low-mem-frac", type=float, default=0.10,
                        help="throttle prefetching when MemAvailable < this fraction of MemTotal")
    parser.add_argument("--no-mem-aware", action="store_true",
                        help="disable memory-pressure throttling")
    args = parser.parse_args()

    from bcc import BPF  # imported late so the model/actuator stay testable without bcc

    os.makedirs(os.path.dirname(args.model_path), exist_ok=True)
    model = PrefetchModel(mode=args.mode)
    tracker = PrecisionTracker()
    mem = MemoryMonitor(low_frac=0.0 if args.no_mem_aware else args.low_mem_frac)
    actuator = Actuator(tracker, memory=mem, hi=args.threshold, top_k=args.top_k)

    if args.mode == "static":
        if not os.path.exists(args.model_path):
            sys.exit(f"Error: static mode needs a trained model at {args.model_path} (run --mode train first).")
        model.load(args.model_path)

    my_pid = os.getpid()
    print(f"Daemon PID: {my_pid}")
    b = BPF(text=bpf_source.replace("DAEMON_PID", str(my_pid)))
    b.attach_tracepoint(tp="syscalls:sys_enter_openat", fn_name="trace_enter_openat")
    b.attach_tracepoint(tp="syscalls:sys_exit_openat", fn_name="trace_exit_openat")
    b.attach_tracepoint(tp="syscalls:sys_enter_read", fn_name="trace_enter_read")
    b.attach_tracepoint(tp="syscalls:sys_enter_close", fn_name="trace_enter_close")

    counters = {"events": 0, "predict_s": 0.0}

    def handle_event(ctx, data, size):
        ev = ct.cast(data, ct.POINTER(Event)).contents
        filename = ev.filename.decode("utf-8", "replace")
        if ev.type == EV_OPEN:
            t0 = time.perf_counter()
            comm = ev.comm.decode("utf-8", "replace")
            bucket = datetime.datetime.now().hour // 6
            model.process_access(ev.pid, comm, bucket, filename)
            if args.mode != "train":
                actuator.submit(model.predict_next(ev.pid, comm, bucket, top_k=args.top_k * 2))
            counters["events"] += 1
            counters["predict_s"] += time.perf_counter() - t0
        elif ev.type == EV_READ:
            tracker.record_read(filename)

    b["events"].open_ring_buffer(handle_event)

    running = True

    def stop(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("[Daemon] Listening for file opens... (Ctrl+C to stop)", flush=True)

    t_start = last = time.time()
    while running:
        b.ring_buffer_poll(timeout=100)        # blocks in-kernel; no busy sleep
        if time.time() - last >= 60:
            ru = resource.getrusage(resource.RUSAGE_SELF)
            print(f"[Interval Stats at t={int(time.time() - t_start)}s] Max RSS: {ru.ru_maxrss} KB, "
                  f"Tree Size/Active Sequences: {model.size}, Actuator: {actuator.stats}", flush=True)
            last = time.time()

    actuator.close()
    if args.mode in ("train", "full", "seq"):
        model.save(args.model_path)
        print(f"\nModel saved to {args.model_path}")
    if args.mode != "train":
        tracker.print_stats()
    print(f"[Actuator] {actuator.stats}")
    ev_n = max(counters["events"], 1)
    print(f"[Overhead] Avg model+dispatch time per open event: {1e6 * counters['predict_s'] / ev_n:.1f} us "
          f"({counters['events']} events)")
    ru = resource.getrusage(resource.RUSAGE_SELF)
    print(f"[Overhead] Max RSS (RAM): {ru.ru_maxrss} KB")
    print(f"[Overhead] CPU Time: User {ru.ru_utime:.2f}s, Sys {ru.ru_stime:.2f}s")


if __name__ == "__main__":
    main()
