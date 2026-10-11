import os
from collections import defaultdict, deque

def get_ext(filename):
    _, ext = os.path.splitext(filename)
    return ext if ext else ".none"

class ContextMiner:
    def __init__(self, max_window=5):
        self.max_window = max_window
        self.history = defaultdict(lambda: deque(maxlen=max_window))
        self.frequency_table = defaultdict(lambda: defaultdict(int))
        
        # Context stats
        self.file_total = defaultdict(int)
        self.file_comm = defaultdict(lambda: defaultdict(int))
        self.file_time = defaultdict(lambda: defaultdict(int))
        
        self.comm_total = defaultdict(int)
        self.comm_ext = defaultdict(lambda: defaultdict(int))

    def process_access(self, pid, comm, time_bucket, filename):
        # 1. Update Sequence Table
        hist = self.history[pid]
        for i in range(len(hist)):
            context = tuple(list(hist)[i:])
            self.frequency_table[context][filename] += 1
            
        hist.append(filename)
        
        # 2. Update Context Stats
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

        # Base sequence score
        candidates = defaultdict(int)
        for i in range(len(hist)):
            context = tuple(list(hist)[i:])
            if context in self.frequency_table:
                weight = len(context)
                for next_file, count in self.frequency_table[context].items():
                    candidates[next_file] += count * weight
                    
        # Context Fusion Re-ranking
        ranked = []
        for file, seq_score in candidates.items():
            # Calculate Context Multipliers
            p_comm = self.file_comm[file][current_comm] / self.file_total[file]
            p_time = self.file_time[file][current_time_bucket] / self.file_total[file]
            
            ext = get_ext(file)
            p_ext = self.comm_ext[current_comm][ext] / self.comm_total[current_comm]
            
            # Final formula: S_seq * (1 + P_comm + P_time + P_ext)
            multiplier = 1.0 + p_comm + p_time + p_ext
            final_score = seq_score * multiplier
            
            ranked.append((file, final_score, seq_score, p_comm, p_time, p_ext))
            
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

def main():
    miner = ContextMiner(max_window=3)
    
    # Train: Process "builder" in morning (bucket 0) does A -> B
    miner.process_access(pid=1, comm="builder", time_bucket=0, filename="A.src")
    miner.process_access(pid=1, comm="builder", time_bucket=0, filename="B.obj")
    
    # Train: Process "logger" in evening (bucket 3) does A -> C
    miner.process_access(pid=2, comm="logger", time_bucket=3, filename="A.src")
    miner.process_access(pid=2, comm="logger", time_bucket=3, filename="C.log")
    
    # Test: A new "builder" process in the morning (bucket 0) accesses A.src
    miner.process_access(pid=3, comm="builder", time_bucket=0, filename="A.src")
    
    # What does it predict next? (Base seq scores for B.obj and C.log are identical: 1.0)
    preds = miner.predict_next(pid=3, current_comm="builder", current_time_bucket=0)
    
    print("Predictions for 'builder' at time 0 after accessing 'A.src':")
    for p in preds:
        print(f"File: {p[0]:<6} | Final Score: {p[1]:.2f} = Base({p[2]}) * (1 + P_comm({p[3]:.1f}) + P_time({p[4]:.1f}) + P_ext({p[5]:.2f}))")

if __name__ == "__main__":
    main()
