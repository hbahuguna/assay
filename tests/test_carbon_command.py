"""Carbon command alias tests."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CarbonCommandTests(unittest.TestCase):
    def test_carbon_alias_runs_the_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "harness",
                    "carbon",
                    "--target",
                    str(ROOT / "examples" / "sample-repo"),
                    "--no-browser",
                    "--personas-dir",
                    str(ROOT / "personas"),
                    "--root",
                    str(root),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "completed")
            self.assertIn("confidence", report)


if __name__ == "__main__":
    unittest.main()
