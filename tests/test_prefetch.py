import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prefetch_actuator import FADV_DONTNEED, FADV_WILLNEED, Actuator, MemoryMonitor, PrecisionTracker
from prefetch_model import PrefetchModel


def feed(model, seq, pid=1, comm="app", bucket=0):
    for f in seq:
        model.process_access(pid, comm, bucket, f)


class ModelTests(unittest.TestCase):
    def test_learns_cycle(self):
        m = PrefetchModel("seq")
        feed(m, ["a", "b", "c"] * 10)
        top = m.predict_next(1, "app", 0)[0]
        self.assertEqual(top[0], "a")           # history ends in c -> a
        self.assertGreater(top[1], 0.9)

    def test_pids_are_isolated(self):
        m = PrefetchModel("seq")
        feed(m, ["a", "b"] * 10, pid=1)
        feed(m, ["x", "y"] * 10, pid=2)
        self.assertEqual(m.predict_next(1, "app", 0)[0][0], "a")
        self.assertEqual(m.predict_next(2, "app", 0)[0][0], "x")

    def test_top_k_and_min_conf(self):
        m = PrefetchModel("seq")
        feed(m, ["a", "b", "a", "c", "a", "b", "a", "b"])
        self.assertLessEqual(len(m.predict_next(1, "app", 0, top_k=1)), 1)
        self.assertTrue(all(c >= 0.5 for _, c in m.predict_next(1, "app", 0, min_conf=0.5)))

    def test_static_mode_does_not_learn(self):
        m = PrefetchModel("static")
        feed(m, ["a", "b", "a", "b"])
        self.assertEqual(m.size, 0)

    def test_full_mode_uses_context(self):
        m = PrefetchModel("full")
        feed(m, ["a", "b"] * 5, comm="builder")
        conf = m.predict_next(1, "builder", 0)[0][1]
        self.assertGreater(conf, 0.5)
        self.assertLessEqual(conf, 1.0)

    def test_json_roundtrip(self):
        m = PrefetchModel("full")
        feed(m, ["a", "b", "c"] * 5)
        before = m.predict_next(1, "app", 0)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.json")
            m.save(p)
            m2 = PrefetchModel("static")
            m2.load(p)
            feed(m2, ["a", "b", "c"])           # rebuild history only
            self.assertEqual(before, m2.predict_next(1, "app", 0))

    def test_prune_bounds_memory(self):
        m = PrefetchModel("seq", max_contexts=50)
        for i in range(2000):
            m.process_access(1, "app", 0, f"/f{i}")
        self.assertLessEqual(m.size, 50 + m.max_window)

    def test_pid_table_bounded(self):
        m = PrefetchModel("seq", max_pids=8)
        for pid in range(100):
            m.process_access(pid, "app", 0, "/f")
        self.assertLessEqual(len(m.history), 8)


class FakeMem(MemoryMonitor):
    def __init__(self, pressure):
        super().__init__()
        self.p = pressure

    def under_pressure(self):
        return self.p


class ActuatorTests(unittest.TestCase):
    def make(self, pressure=False, **kw):
        calls = []
        act = Actuator(PrecisionTracker(), memory=FakeMem(pressure),
                       advise=lambda p, a: calls.append((p, a)), **kw)
        return act, calls

    def test_prefetches_top_k_above_threshold(self):
        act, calls = self.make(top_k=2)
        act.submit([("a", 0.9), ("b", 0.8), ("c", 0.7), ("d", 0.1)])
        act.close()
        self.assertEqual({p for p, _ in calls}, {"a", "b"})
        self.assertTrue(all(a == FADV_WILLNEED for _, a in calls))

    def test_cooldown_dedupes(self):
        act, calls = self.make(cooldown=60)
        act.submit([("a", 0.9)])
        act.submit([("a", 0.9)])
        act.close()
        self.assertEqual(len(calls), 1)
        self.assertEqual(act.stats["deduped"], 1)

    def test_pressure_throttles_and_reclaims(self):
        act, calls = self.make(pressure=True)
        act.tracker.window = 0.0
        act.tracker.record_prefetch("old")
        time.sleep(0.01)
        act.submit([("a", 0.9)])
        act.close()
        self.assertEqual(calls, [("old", FADV_DONTNEED)])
        self.assertEqual(act.stats["throttled_mem"], 1)

    def test_queue_cap(self):
        import threading
        gate = threading.Event()
        calls = []
        act = Actuator(PrecisionTracker(), memory=FakeMem(False), workers=1, max_queue=2,
                       top_k=10, advise=lambda p, a: (gate.wait(2), calls.append(p)))
        act.submit([(f"f{i}", 0.9) for i in range(6)])
        self.assertGreaterEqual(act.stats["dropped_queue"], 1)
        gate.set()
        act.close()

    def test_precision_tracker(self):
        t = PrecisionTracker(window=5)
        t.record_prefetch("a")
        t.record_read("a")
        t.record_read("zzz")
        self.assertEqual((t.useful, t.total), (1, 1))

    def test_reclaim_rate_signal(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("pgsteal_kswapd 0\npgsteal_direct 0\npgscan_kswapd 999\n")
        try:
            m = MemoryMonitor(reclaim_mb_s=100, ttl=0, vmstat=fh.name, psi="/nonexistent", meminfo="/nonexistent")
            self.assertFalse(m.under_pressure())            # first sample: no rate yet
            with open(fh.name, "w") as f2:                  # +51200 pages = 200 MB
                f2.write("pgsteal_kswapd 40000\npgsteal_direct 11200\npgscan_kswapd 999\n")
            m._vm_prev = (m._vm_prev[0] - 1.0, m._vm_prev[1])   # pretend 1 s elapsed
            self.assertTrue(m.under_pressure())
            self.assertAlmostEqual(m.last_reclaim_mb_s, 200.0, delta=5)
        finally:
            os.unlink(fh.name)

    def test_psi_parsing_and_threshold(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("some avg10=7.50 avg60=1.00 avg300=0.10 total=123\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        try:
            hot = MemoryMonitor(psi_threshold=5.0, ttl=0, psi=fh.name, meminfo="/nonexistent", vmstat="/nonexistent")
            self.assertTrue(hot.under_pressure())
            self.assertEqual(hot.last_psi, 7.5)
            cool = MemoryMonitor(psi_threshold=10.0, ttl=0, psi=fh.name, meminfo="/nonexistent", vmstat="/nonexistent")
            self.assertFalse(cool.under_pressure())
        finally:
            os.unlink(fh.name)

    def test_meminfo_parsing(self):
        with tempfile.NamedTemporaryFile("w", suffix=".mi", delete=False) as fh:
            fh.write("MemTotal: 1000 kB\nMemFree: 5 kB\nMemAvailable: 50 kB\n")
        try:
            self.assertTrue(MemoryMonitor(low_frac=0.1, ttl=0, meminfo=fh.name, psi="/nonexistent", vmstat="/nonexistent").under_pressure())
            self.assertFalse(MemoryMonitor(low_frac=0.01, ttl=0, meminfo=fh.name, psi="/nonexistent", vmstat="/nonexistent").under_pressure())
            self.assertFalse(MemoryMonitor(meminfo="/nonexistent", psi="/nonexistent", vmstat="/nonexistent").under_pressure())
        finally:
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
