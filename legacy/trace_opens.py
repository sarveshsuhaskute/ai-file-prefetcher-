# pyrefly: ignore [missing-import]
from bcc import BPF
import ctypes as ct
import sys

bpf_source = r"""
#include <linux/sched.h>
#include <uapi/linux/ptrace.h>
#include <linux/fs.h>

struct event_t {
    u32 pid;
    u32 type; // 1=OPEN, 2=READ, 3=CLOSE
    char comm[16];
    char filename[256];
};

BPF_RINGBUF_OUTPUT(events, 8);

// Temporary map to store filename from sys_enter_openat to sys_exit_openat
struct val_t {
    char filename[256];
};
BPF_HASH(infotmp, u64, struct val_t);

// Persistent map to store fd -> filename mapping
struct fd_key_t {
    u32 pid;
    u32 fd;
};
BPF_HASH(fdmap, struct fd_key_t, struct val_t);

static inline bool filter_noise(const char *filename) {
    if (filename[0] == 0) return true;
    
    // Filter /proc, /sys, /dev
    if (filename[0] == '/' && filename[1] == 'p' && filename[2] == 'r' && filename[3] == 'o' && filename[4] == 'c') return true;
    if (filename[0] == '/' && filename[1] == 's' && filename[2] == 'y' && filename[3] == 's') return true;
    if (filename[0] == '/' && filename[1] == 'd' && filename[2] == 'e' && filename[3] == 'v') return true;

    // Filter .so library loads and locale
    for (int i = 0; i < 250 && i < 256; i++) {
        if (filename[i] == '\0') break;
        if (filename[i] == '.' && filename[i+1] == 's' && filename[i+2] == 'o') return true;
        if (filename[i] == 'l' && filename[i+1] == 'o' && filename[i+2] == 'c' && filename[i+3] == 'a' && filename[i+4] == 'l' && filename[i+5] == 'e') return true;
    }
    
    return false;
}

int trace_enter_openat(struct tracepoint__syscalls__sys_enter_openat *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    struct val_t val = {};
    
    bpf_probe_read_user_str(&val.filename, sizeof(val.filename), ctx->filename);
    
    if (filter_noise(val.filename)) {
        return 0;
    }

    infotmp.update(&pid_tgid, &val);
    return 0;
}

int trace_exit_openat(struct tracepoint__syscalls__sys_exit_openat *ctx) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    int ret = ctx->ret;
    
    struct val_t *valp = infotmp.lookup(&pid_tgid);
    if (valp == 0) {
        return 0; // missed enter or filtered
    }
    
    if (ret >= 0) {
        // Success open
        struct fd_key_t key = {.pid = pid, .fd = (u32)ret};
        fdmap.update(&key, valp);
        
        // Emit event
        struct event_t *event = events.ringbuf_reserve(sizeof(struct event_t));
        if (event) {
            event->pid = pid;
            event->type = 1; // OPEN
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
    u32 fd = ctx->fd;
    
    struct fd_key_t key = {.pid = pid, .fd = fd};
    struct val_t *valp = fdmap.lookup(&key);
    if (valp) {
        struct event_t *event = events.ringbuf_reserve(sizeof(struct event_t));
        if (event) {
            event->pid = pid;
            event->type = 2; // READ
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
    u32 fd = ctx->fd;
    
    struct fd_key_t key = {.pid = pid, .fd = fd};
    struct val_t *valp = fdmap.lookup(&key);
    if (valp) {
        struct event_t *event = events.ringbuf_reserve(sizeof(struct event_t));
        if (event) {
            event->pid = pid;
            event->type = 3; // CLOSE
            bpf_get_current_comm(&event->comm, sizeof(event->comm));
            __builtin_memcpy(&event->filename, valp->filename, sizeof(event->filename));
            events.ringbuf_submit(event, 0);
        }
        fdmap.delete(&key);
    }
    return 0;
}
"""

b = BPF(text=bpf_source)
b.attach_tracepoint(tp="syscalls:sys_enter_openat", fn_name="trace_enter_openat")
b.attach_tracepoint(tp="syscalls:sys_exit_openat", fn_name="trace_exit_openat")
b.attach_tracepoint(tp="syscalls:sys_enter_read", fn_name="trace_enter_read")
b.attach_tracepoint(tp="syscalls:sys_enter_close", fn_name="trace_enter_close")

class Event(ct.Structure):
    _fields_ = [
        ("pid", ct.c_uint32),
        ("type", ct.c_uint32),
        ("comm", ct.c_char * 16),
        ("filename", ct.c_char * 256),
    ]

type_names = {1: "OPEN", 2: "READ", 3: "CLOSE"}

def handle_event(ctx, data, size):
    event = ct.cast(data, ct.POINTER(Event)).contents
    op = type_names.get(event.type, "UNKNOWN")
    print(f"{event.comm.decode('utf-8', 'replace'):<16} (pid {event.pid}) {op:<5} {event.filename.decode('utf-8', 'replace')}")
    sys.stdout.flush()

b["events"].open_ring_buffer(handle_event)
print("Tracing openat, read, close... Ctrl+C to stop.")
sys.stdout.flush()

while True:
    try:
        b.ring_buffer_poll()
    except KeyboardInterrupt:
        break
