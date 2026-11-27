import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from obsidian_vault import atomic
from obsidian_vault.cat import Cat


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_writes_and_leaves_no_temp_files(self):
        p = self.dir / "sub" / "state.json"
        atomic.write_text(p, "one")
        atomic.write_text(p, "two")
        self.assertEqual(p.read_text(encoding="utf-8"), "two")
        self.assertEqual(os.listdir(p.parent), ["state.json"])

    def test_mode_new_existing_and_explicit(self):
        p = self.dir / "a.json"
        atomic.write_text(p, "x")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o644)
        os.chmod(p, 0o640)
        atomic.write_text(p, "y")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o640)
        atomic.write_text(p, "z", mode=0o600)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_concurrent_writes_to_one_file_do_not_fail(self):
        p = self.dir / "cat.json"
        errors, start = [], threading.Barrier(8)

        def write(n):
            start.wait()
            try:
                for i in range(200):
                    atomic.write_text(p, f"{n}-{i}")
            except Exception as e:  # pragma: no cover - the bug this guards against
                errors.append(e)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(os.listdir(self.dir), ["cat.json"])

    def test_concurrent_cat_saves_do_not_crash(self):
        cat = Cat(self.dir)
        errors, start = [], threading.Barrier(6)

        def save(n):
            start.wait()
            try:
                for i in range(50):
                    cat.save({"x": n, "y": i, "mood": "sleepy", "asleep": False})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=save, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertIsNotNone(cat.get()["x"])


if __name__ == "__main__":
    unittest.main()
