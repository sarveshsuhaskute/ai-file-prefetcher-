import subprocess
import time
import os
import sys

def drop_caches():
    print("    [System] Dropping caches...")
    subprocess.run(["sync"])
    with open("/proc/sys/vm/drop_caches", "w") as f:
        f.write("3\n")
    time.sleep(1)

def run_workload_iteration(daemon_mode):
    drop_caches()
    
    daemon_proc = None
    if daemon_mode:
        daemon_proc = subprocess.Popen(
            ["python3", "-u", "prefetch_daemon.py", "--mode", daemon_mode],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        time.sleep(2)
        
    start_time = time.time()
    workload = subprocess.run(["python3", "workload.py", "300"], capture_output=True, text=True)
    elapsed = time.time() - start_time
    
    hit_rate = 0.0
    precision = None
    overhead = None
    time_series = []
    
    for line in workload.stdout.split('\n'):
        if "Overall Cache Hit Rate:" in line:
            hit_rate = float(line.split(':')[1].split('%')[0].strip())
        elif line.startswith("RAW DATA ->"):
            print(f"      {line.strip()}")
        elif line.startswith("["):
            time_series.append(line)
            
    if hit_rate == 0.0:
        print(f"WORKLOAD CRASHED! STDERR: {workload.stderr}")
        
    print("    Time Series:")
    for ts in time_series:
        print(f"      {ts}")
            
    if daemon_proc:
        daemon_proc.send_signal(2)
        try:
            d_out, _ = daemon_proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()
            d_out, _ = daemon_proc.communicate()
            
        print("    Daemon Stats:")
        for line in d_out.split('\n'):
            if "[Precision Stats]" in line or "Total Prefetched:" in line or "Total Evicted:" in line or "[Interval Stats]" in line:
                print(f"      {line.strip()}")
            elif "[Overhead]" in line:
                if overhead is None: overhead = []
                overhead.append(line.strip())
                
    return hit_rate, precision, overhead

def run_baseline(name, daemon_mode=None, iterations=3):
    print(f"\n{'='*60}")
    print(f"Running Baseline: {name}")
    print(f"{'='*60}")
    
    hit_rates = []
    last_overhead = None
    
    for i in range(iterations):
        print(f"  -> Iteration {i+1}/{iterations}...")
        hr, prec, oh = run_workload_iteration(daemon_mode)
        hit_rates.append(hr)
        if oh: last_overhead = oh
        
    mean_hr = sum(hit_rates) / iterations
    variance = sum((x - mean_hr) ** 2 for x in hit_rates) / (iterations - 1 if iterations > 1 else 1)
    std_dev = variance ** 0.5
    
    print(f"\nResult: {name}")
    print(f"Mean Cache Hit Rate: {mean_hr:.2f}% ± {std_dev:.2f}% (over {iterations} runs)")
    print(f"Runs: {[f'{x:.2f}%' for x in hit_rates]}")
    
    if last_overhead:
        for o in last_overhead:
            print(o)

def main():
    pass
    print("Starting Phase 6 Evaluation Harness (v2 - Averaged Runs & Proper Ablation)")
    
    print("\n[Init] Writing dummy test data to disk...")
    subprocess.run(["python3", "workload.py", "--setup"], check=True)
    
    files = [f for f in os.listdir("/tmp") if f.endswith(".dat")]
    total_files = len(files)
    total_size_gb = total_files * 10 / 1024
    print(f"    [Verify] Found {total_files} .dat files in /tmp ({total_size_gb:.1f} GB total)")
    if total_files != 400:
        print(f"Error: Expected 400 files, but found {total_files}. Aborting.")
        sys.exit(1)
        
    # print("[Init] Training offline model for Static Baseline...")
    # drop_caches()
    # train_proc = subprocess.Popen(["python3", "prefetch_daemon.py", "--mode", "train"])
    # time.sleep(2)
    # subprocess.run(["python3", "workload.py", "40"]) 
    # train_proc.send_signal(2)
    # train_proc.communicate()
    # print("[Init] Offline model saved successfully.")
    
    # run_baseline("1. No Daemon (Default Linux Readahead)", daemon_mode=None, iterations=1)
    # run_baseline("2. Static Miner (Strict Offline Training)", daemon_mode="static", iterations=3)
    # run_baseline("3. Sequence-Only Online Miner (Ablation)", daemon_mode="seq", iterations=1)
    run_baseline("4. Full Context-Aware AI (Phase 5)", daemon_mode="full", iterations=1)
    
    print("\nEvaluation Complete.")

if __name__ == "__main__":
    main()
