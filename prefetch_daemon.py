import sys
import os
import signal
import ctypes as ct
import datetime
import time
import argparse
import pickle
import resource
from collections import defaultdict, deque
# pyrefly: ignore [missing-import]
from bcc import BPF

def get_ext(filename):
    _, ext = os.path.splitext(filename)
    return ext if ext else ".none"

def make_deque():
    return deque(maxlen=5)

def make_int_dict():
    return defaultdict(int)

class PrefetchAI:
    def __init__(self, mode="full", max_window=5):
        self.mode = mode
        self.max_window = max_window
        self.history = defaultdict(make_deque)
        
        self.frequency_table = defaultdict(make_int_dict)
        self.context_totals = defaultdict(int)
        
        self.file_total = defaultdict(int)
        self.file_comm = defaultdict(make_int_dict)
        self.file_time = defaultdict(make_int_dict)
        self.comm_total = defaultdict(int)
        self.comm_ext = defaultdict(make_int_dict)

    def process_access(self, pid, comm, time_bucket, filename):
        if self.mode == "static":
            # True offline static model: does not learn during the live run
            self.history[pid].append(filename) 
            return
            
        hist = self.history[pid]
        for i in range(len(hist)):
            context = tuple(list(hist)[i:])
            self.frequency_table[context][filename] += 1
            self.context_totals[context] += 1
            
        hist.append(filename)
        
        self.file_total[filename] += 1
        self.file_comm[filename][comm] += 1
        self.file_time[filename][time_bucket] += 1
        
        ext = get_ext(filename)
        self.comm_total[comm] += 1
        self.comm_ext[comm][ext] += 1

    def predict_next(self, pid, current_comm, current_time_bucket):
        hist = self.history[pid]
        if not hist:
            return []

        active_suffixes = []
        total_weight = 0
        for i in range(len(hist)):
            context = tuple(list(hist)[i:])
            if context in self.frequency_table:
                active_suffixes.append(context)
                total_weight += len(context)
                
        if not active_suffixes:
            return []

        candidates_p_seq = defaultdict(float)
        for context in active_suffixes:
            L = len(context)
            total_transitions = self.context_totals[context]
            for next_file, count in self.frequency_table[context].items():
                prob = count / total_transitions
                candidates_p_seq[next_file] += (L * prob)
                
        for file in candidates_p_seq:
            candidates_p_seq[file] /= total_weight
                    
        ranked = []
        for file, p_seq in candidates_p_seq.items():
            if self.mode == "full":
                p_comm = self.file_comm[file][current_comm] / self.file_total[file] if self.file_total[file] else 0
                p_time = self.file_time[file][current_time_bucket] / self.file_total[file] if self.file_total[file] else 0
                ext = get_ext(file)
                p_ext = self.comm_ext[current_comm][ext] / self.comm_total[current_comm] if self.comm_total[current_comm] else 0
                
                p_ctx = (p_comm + p_time + p_ext) / 3.0
                confidence = (0.6 * p_seq) + (0.4 * p_ctx)
            else:
                confidence = p_seq
                
            ranked.append((file, confidence))
            
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked


class PrecisionTracker:
    def __init__(self):
        self.prefetches = {}
        self.useful_count = 0
        self.total_count = 0
        self.evictions = 0
        # Calibration: The longest full cycle of any actor in workload.py is 0.6s.
        # A 2.0s window strictly bounds a "useful" read to roughly the next 3 cycles.
        self.precision_window = 2.0 
        
    def record_prefetch(self, filename):
        self.prefetches[filename] = time.time()
        self.total_count += 1
        
    def record_evict(self):
        self.evictions += 1
        
    def record_read(self, filename):
        if filename in self.prefetches:
            if time.time() - self.prefetches[filename] <= self.precision_window:
                self.useful_count += 1
            del self.prefetches[filename]

    def print_stats(self):
        prec = (self.useful_count / self.total_count) * 100 if self.total_count > 0 else 0
        print(f"\n[Precision Stats] Useful Prefetches: {self.useful_count} / {self.total_count} ({prec:.1f}%)")
        print(f"[Precision Stats] Total DONTNEED Evictions: {self.evictions}")

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["static", "seq", "full", "train"], default="full")
    parser.add_argument("--model-path", default="/tmp/prefetch_model.pkl")
    args = parser.parse_args()
    
    ai = PrefetchAI(mode=args.mode, max_window=5)
    tracker = PrecisionTracker()
    
    if args.mode == "static":
        if os.path.exists(args.model_path):
            with open(args.model_path, "rb") as f:
                state = pickle.load(f)
                ai.frequency_table, ai.context_totals, ai.file_total, ai.file_comm, ai.file_time, ai.comm_total, ai.comm_ext = state
        else:
            print(f"Error: Static mode requested but model file {args.model_path} not found.")
            sys.exit(1)
            
    my_pid = os.getpid()
    print(f"Daemon PID: {my_pid}")
    bpf_text = bpf_source.replace("DAEMON_PID", str(my_pid))
    idx = bpf_text.find("if (pid_tgid >> 32 == ")
    if idx != -1:
        print(f"Compiled BPF snippet: {bpf_text[idx:idx+40]}")
    b = BPF(text=bpf_text)
    b.attach_tracepoint(tp="syscalls:sys_enter_openat", fn_name="trace_enter_openat")
    b.attach_tracepoint(tp="syscalls:sys_exit_openat", fn_name="trace_exit_openat")
    b.attach_tracepoint(tp="syscalls:sys_enter_read", fn_name="trace_enter_read")
    b.attach_tracepoint(tp="syscalls:sys_enter_close", fn_name="trace_enter_close")

    def apply_actuation(predictions, trigger_pid):
        if args.mode == "train":
            return # No actuation during pure training
            
        for filepath, confidence in predictions:
            if confidence > 0.5:
                advice = os.POSIX_FADV_WILLNEED
            elif confidence < 0.2:
                advice = os.POSIX_FADV_DONTNEED
                with open("/tmp/evictions.log", "a") as f:
                    f.write(f"[DONTNEED] Trigger-PID: {trigger_pid} | Evicting: {filepath} | Confidence: {confidence:.4f}\n")
            else:
                continue
                
            try:
                fd = os.open(filepath, os.O_RDONLY | os.O_NONBLOCK)
                os.posix_fadvise(fd, 0, 0, advice)
                os.close(fd)
                if advice == os.POSIX_FADV_WILLNEED:
                    tracker.record_prefetch(filepath)
                elif advice == os.POSIX_FADV_DONTNEED:
                    tracker.record_evict()
            except OSError:
                pass

    def handle_event(ctx, data, size):
        event = ct.cast(data, ct.POINTER(Event)).contents
        filename = event.filename.decode('utf-8', 'replace')
        comm = event.comm.decode('utf-8', 'replace')
        
        hour = datetime.datetime.now().hour
        time_bucket = hour // 6 
        
        if event.type == 1: # OPEN
            ai.process_access(event.pid, comm, time_bucket, filename)
            predictions = ai.predict_next(event.pid, comm, time_bucket)
            if predictions:
                apply_actuation(predictions, event.pid)
                
        elif event.type == 2: # READ
            tracker.record_read(filename)

    b["events"].open_ring_buffer(handle_event)
    sys.stdout.flush()

    global running
    running = True
    def sig_handler(sig, frame):
        global running
        running = False
        print("\n[Daemon] Received SIGINT. Dumping model and shutting down gracefully...")
        try:
            with open(args.model_path, "wb") as f:
                state = (ai.frequency_table, ai.context_totals, ai.file_total, ai.file_comm, ai.file_time, ai.comm_total, ai.comm_ext)
                pickle.dump(state, f)
            print(f"Model dumped to {args.model_path} from sig_handler")
        except Exception as e:
            print(f"Failed to dump model: {e}")

    signal.signal(signal.SIGINT, sig_handler)

    print("[Daemon] Listening for file opens... (Press Ctrl+C to stop)")
    
    start_time_log = time.time()
    last_print_time = start_time_log
    while running:
        b.ring_buffer_consume()
        if time.time() - last_print_time >= 60:
            ru = resource.getrusage(resource.RUSAGE_SELF)
            print(f"[Interval Stats at t={int(time.time()-start_time_log)}s] Max RSS: {ru.ru_maxrss} KB, Tree Size/Active Sequences: {len(ai.frequency_table)}")
            last_print_time = time.time()
            sys.stdout.flush()
        time.sleep(0.1)

    with open(args.model_path, "wb") as f:
        state = (ai.frequency_table, ai.context_totals, ai.file_total, ai.file_comm, ai.file_time, ai.comm_total, ai.comm_ext)
        pickle.dump(state, f)
    print(f"\nModel dumped to {args.model_path}")

    if args.mode != "train":
        tracker.print_stats()
        
    ru = resource.getrusage(resource.RUSAGE_SELF)
    print(f"[Overhead] Max RSS (RAM): {ru.ru_maxrss} KB")
    print(f"[Overhead] CPU Time: User {ru.ru_utime:.2f}s, Sys {ru.ru_stime:.2f}s")

if __name__ == "__main__":
    main()
