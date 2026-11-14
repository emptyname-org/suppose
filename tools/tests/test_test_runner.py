"""Unsupported socket paths fail before any test environment is created."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestRunnerTest(unittest.TestCase):
    def test_long_checkout_path_explains_the_socket_limit_before_starting(self):
        root = Path(__file__).resolve().parents[2]
        (root / "tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root / "tmp") as scratch:
            checkout = Path(scratch) / ("é" * 50)
            tools = checkout / "tools"
            tools.mkdir(parents=True)
            shutil.copyfile(root / "tools/test.sh", tools / "test.sh")
            result = subprocess.run(
                ["sh", str(tools / "test.sh")],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("checkout path is too long", result.stderr)
            self.assertIn("limit 107", result.stderr)
            self.assertFalse((checkout / "tmp").exists())
