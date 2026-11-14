"""A resolved advisory must still reject a vulnerable pinned version."""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "audit", Path(__file__).parents[1] / "audit.py"
)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class AuditTest(unittest.TestCase):
    def test_fixed_distribution_does_not_hide_old_vulnerable_pin(self):
        packages = [{"source": "example", "name": "libexample", "version": "1.0-1"}]
        tracker = {
            "example": {
                "CVE-example": {
                    "releases": {
                        "trixie": {
                            "status": "resolved",
                            "fixed_version": "1.0-2",
                        }
                    }
                }
            }
        }
        self.assertEqual(len(audit.debian_findings(packages, tracker, "trixie")), 1)
        packages[0]["version"] = "1.0-2"
        self.assertEqual(audit.debian_findings(packages, tracker, "trixie"), [])

    def test_no_fix_and_undetermined_findings_fail_closed(self):
        packages = [{"source": "example", "name": "example", "version": "1"}]
        for status in ("open", "undetermined"):
            tracker = {
                "example": {"CVE-example": {"releases": {"trixie": {"status": status}}}}
            }
            self.assertEqual(len(audit.debian_findings(packages, tracker, "trixie")), 1)

    def test_review_covers_only_the_exact_pin_and_advisory(self):
        finding = {
            "ecosystem": "debian",
            "name": "example",
            "version": "1",
            "id": "CVE-a",
        }
        exception = dict(
            finding, reason="No upstream fix", reviewer="fixture", expires="2999-01-01"
        )
        new = dict(finding, id="CVE-b")
        self.assertEqual(audit.review_findings([finding, new], [exception]), [new])
        with self.assertRaisesRegex(ValueError, "expired"):
            audit.review_findings([finding], [dict(exception, expires="2000-01-01")])


if __name__ == "__main__":
    unittest.main()
