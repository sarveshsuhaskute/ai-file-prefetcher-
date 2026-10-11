import sys
from collections import defaultdict, deque

class PatternMiner:
    def __init__(self, max_window=5):
        self.max_window = max_window
        # history[pid] = deque of recent files
        self.history = defaultdict(lambda: deque(maxlen=max_window))
        # frequency_table[context_tuple][next_file] = count
        self.frequency_table = defaultdict(lambda: defaultdict(int))

    def process_access(self, pid, filename):
        hist = self.history[pid]
        
        # Record this transition for all suffixes of the current history
        for i in range(len(hist)):
            context = tuple(list(hist)[i:])
            self.frequency_table[context][filename] += 1
            
        # Add the new file to the history window
        hist.append(filename)

    def predict_next(self, pid):
        hist = self.history[pid]
        if not hist:
            return []

        candidates = defaultdict(int)
        
        # Aggregate scores from all matching suffixes in the current window
        # Longer contexts are given higher weight for stronger confidence
        for i in range(len(hist)):
            context = tuple(list(hist)[i:])
            if context in self.frequency_table:
                weight = len(context)
                for next_file, count in self.frequency_table[context].items():
                    candidates[next_file] += count * weight
        
        # Rank candidates by score
        ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        return ranked

def main():
    miner = PatternMiner(max_window=3)
    pid = 100
    
    # Synthetic repeating sequence: A, B, C, A, B, C...
    sequence = ["A", "B", "C", "A", "B", "C", "A", "B"]
    
    print("Feeding sequence to PatternMiner:")
    for file in sequence:
        print(f"-> Accessing: {file}")
        miner.process_access(pid, file)
        predictions = miner.predict_next(pid)
        if predictions:
            top_pred = predictions[0][0]
            print(f"   Predicted next: {top_pred} (Full candidates: {predictions})")
        else:
            print("   Predicted next: None")
            
    print("\nVerifying specific Phase 2 requirement:")
    print("After seeing the second 'A', 'B' sequence, what is the prediction?")
    # At this point, the history is [C, A, B] (with max_window=3)
    # The suffixes are:
    # - (C, A, B)
    # - (A, B)
    # - (B,)
    # Based on past data, B is always followed by C.
    preds = miner.predict_next(pid)
    print(f"History window for PID {pid}: {list(miner.history[pid])}")
    print(f"Ranked Predictions: {preds}")

if __name__ == "__main__":
    main()
