import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prefetch_policies import MB, DeadFileEvictor, IOPressureController


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def write_psi(path, total_us):
    with open(path, "w") as fh:
        fh.write(f"some avg10=0.00 avg60=0.00 avg300=0.00 total={total_us}\n"
                 f"full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")


class IOPressureTests(unittest.TestCase):
    def setUp(self):
        self.fh = tempfile.NamedTemporaryFile("w", delete=False)
        self.fh.close()
        self.clock = FakeClock()
        self.c = IOPressureController(target=0.3, start_mb=32, min_mb=1, max_mb=64, step_mb=8,
                                      interval=0.25, psi=self.fh.name, clock=self.clock)

    def tearDown(self):
        os.unlink(self.fh.name)

    def step(self, stall_fraction, total=[0]):
        total[0] += int(stall_fraction * 0.25 * 1e6)
        write_psi(self.fh.name, total[0])
        self.clock.t += 0.25
        return self.c.limit()

    def test_halves_under_stall_and_grows_back(self):
        write_psi(self.fh.name, 0)
        self.assertEqual(self.c.limit(), 32 * MB)            # first read only primes the counters
        self.assertEqual(self.step(0.8), 16 * MB)             # stalled -> halve
        self.assertEqual(self.step(0.8), 8 * MB)
        self.assertEqual(self.step(0.0), 16 * MB)             # calm -> +8 MB
        for _ in range(10):
            self.step(0.0)
        self.assertEqual(self.c.limit(), 64 * MB)             # capped at max

    def test_never_below_minimum(self):
        write_psi(self.fh.name, 0)
        self.c.limit()
        for _ in range(20):
            self.step(1.0)
        self.assertEqual(self.c.limit(), 1 * MB)
        self.assertGreater(self.c.summary()["cuts"], 0)

    def test_missing_psi_keeps_start_value(self):
        c = IOPressureController(psi="/nonexistent", start_mb=32)
        self.assertEqual(c.limit(), 32 * MB)


class DeadFileTests(unittest.TestCase):
    def make(self, cache_bytes):
        mi = tempfile.NamedTemporaryFile("w", delete=False)
        mi.write(f"MemTotal: 1000000 kB\nCached: {cache_bytes // 1024} kB\n")
        mi.close()
        self.addCleanup(os.unlink, mi.name)
        return DeadFileEvictor(meminfo=mi.name, cgroup_root="/nonexistent", sizeof=lambda f: 10 * MB)

    def cycle(self, ev, n, rounds):
        for _ in range(rounds):
            for i in range(n):
                ev.on_open(f"/f{i}")

    def test_loop_larger_than_cache_is_dead(self):
        ev = self.make(cache_bytes=500 * MB)                 # 50 files fit
        self.cycle(ev, 100, 3)                                # reuse distance 100 files = 1000 MB
        self.assertTrue(ev.is_dead("/f3"))

    def test_loop_that_fits_is_kept(self):
        ev = self.make(cache_bytes=2000 * MB)                # 200 files fit
        self.cycle(ev, 100, 3)
        self.assertFalse(ev.is_dead("/f3"))

    def test_unseen_or_once_seen_file_is_kept(self):
        ev = self.make(cache_bytes=10 * MB)
        ev.on_open("/once")
        self.assertFalse(ev.is_dead("/once"))
        self.assertFalse(ev.is_dead("/never"))

    def test_tables_stay_bounded(self):
        ev = self.make(cache_bytes=500 * MB)
        ev.max_files = 1000
        for i in range(10_000):                              # many distinct files, as in a real program start-up
            ev.on_open(f"/lib/{i}.pyc")
        self.assertLessEqual(len(ev.last), 1001)
        self.assertLessEqual(len(ev.sizes), 1001)


if __name__ == "__main__":
    unittest.main()
