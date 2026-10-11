import os
from collections import defaultdict, deque

def get_ext(filename):
    _, ext = os.path.splitext(filename)
    return ext if ext else ".none"

class ConfidenceScorerMiner:
    def __init__(self, max_window=5):
        self.max_window = max_window
        self.history = defaultdict(lambda: deque(maxlen=max_window))
        # frequency_table[context_tuple][next_file] = count
        self.frequency_table = defaultdict(lambda: defaultdict(int))
        # context_totals[context_tuple] = sum(counts)
        self.context_totals = defaultdict(int)
        
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
            self.context_totals[context] += 1
            
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

        # Find active suffixes
        active_suffixes = []
        total_weight = 0
        for i in range(len(hist)):
            context = tuple(list(hist)[i:])
            if context in self.frequency_table:
                active_suffixes.append(context)
                total_weight += len(context)
                
        if not active_suffixes:
            return []

        # Base sequence probability P_seq [0.0, 1.0]
        # P_seq = sum(L * P(F | C_L)) / sum(L)
        candidates_p_seq = defaultdict(float)
        
        for context in active_suffixes:
            L = len(context)
            total_transitions = self.context_totals[context]
            for next_file, count in self.frequency_table[context].items():
                prob = count / total_transitions
                candidates_p_seq[next_file] += (L * prob)
                
        for file in candidates_p_seq:
            candidates_p_seq[file] /= total_weight
                    
        # Context Fusion & Confidence Scoring
        ranked = []
        for file, p_seq in candidates_p_seq.items():
            # Calculate Context Probabilities
            p_comm = self.file_comm[file][current_comm] / self.file_total[file]
            p_time = self.file_time[file][current_time_bucket] / self.file_total[file]
            
            ext = get_ext(file)
            p_ext = self.comm_ext[current_comm][ext] / self.comm_total[current_comm]
            
            # Average Context Probability [0.0, 1.0]
            p_ctx = (p_comm + p_time + p_ext) / 3.0
            
            # Phase 4 Confidence Formula (0.0 to 1.0)
            # 60% weight to sequential pattern, 40% to context match
            confidence = (0.6 * p_seq) + (0.4 * p_ctx)
            
            ranked.append((file, confidence, p_seq, p_ctx))
            
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

def main():
    miner = ConfidenceScorerMiner(max_window=3)
    
    # Train: Process "builder" in morning (bucket 0) does A -> B
    miner.process_access(pid=1, comm="builder", time_bucket=0, filename="A.src")
    miner.process_access(pid=1, comm="builder", time_bucket=0, filename="B.obj")
    
    # Train: Process "logger" in evening (bucket 3) does A -> C
    miner.process_access(pid=2, comm="logger", time_bucket=3, filename="A.src")
    miner.process_access(pid=2, comm="logger", time_bucket=3, filename="C.log")
    
    # Test: A new "builder" process in the morning (bucket 0) accesses A.src
    miner.process_access(pid=3, comm="builder", time_bucket=0, filename="A.src")
    
    preds = miner.predict_next(pid=3, current_comm="builder", current_time_bucket=0)
    
    print("Predictions for 'builder' at time 0 after accessing 'A.src':")
    for p in preds:
        print(f"File: {p[0]:<6} | Confidence: {p[1]:.3f} = (0.6 * P_seq({p[2]:.3f})) + (0.4 * P_ctx({p[3]:.3f}))")

if __name__ == "__main__":
    main()
