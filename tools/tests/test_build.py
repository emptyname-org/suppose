"""Downloads and failed builds must preserve verified, working artifacts."""

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "build", Path(__file__).parents[1] / "build.py"
)
assert SPEC is not None and SPEC.loader is not None
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class BuildTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        (root / "tmp").mkdir(exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=root / "tmp")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)

    def test_bundled_vte_common_needs_no_matching_host_version(self):
        lock = json.loads((build.ROOT / "packages.lock").read_text())
        library = next(p for p in lock["packages"] if p["name"] == "libvte-2.91-gtk4-0")
        common = next(p for p in lock["packages"] if p["name"] == "libvte-2.91-common")
        self.assertEqual(common["version"], library["version"])
        dependency = f"libvte-2.91-common (= {library['version']})"
        result = subprocess.CompletedProcess([], 0, stdout=dependency)
        for installed in ({}, {"libvte-2.91-common": "99.0-1"}):
            with (
                patch.object(build, "installed", return_value=installed),
                patch.object(build, "HOST_PACKAGES", ()),
                patch.object(build, "run", return_value=result),
            ):
                build.check_dependencies([library, common], [Path("library.deb")])

    def test_bad_download_and_corrupt_cache_never_become_verified(self):
        package = {
            "name": "example",
            "path": "pool/example.deb",
            "sha256": hashlib.sha256(b"verified").hexdigest(),
        }
        target = self.root / "example.deb"
        target.write_bytes(b"corrupt")
        attempts = []

        def download(*args):
            attempts.append(args[-1])
            Path(args[args.index("--output") + 1]).write_bytes(
                b"bad mirror content" if len(attempts) == 1 else b"verified"
            )

        with (
            patch.object(build, "CACHE", self.root),
            patch.object(build, "run", download),
        ):
            self.assertEqual(build.fetch(package, "20000101T000000Z"), target)
        self.assertEqual(target.read_bytes(), b"verified")
        self.assertEqual(len(attempts), 2)
        self.assertIn("snapshot.debian.org", attempts[1])
        self.assertFalse(target.with_suffix(".partial").exists())

    def test_failed_patch_preserves_previous_build_and_removes_staging(self):
        dist = self.root / "dist"
        app = dist / "book-editor"
        app.mkdir(parents=True)
        (app / ".complete").write_text("previous")
        (self.root / "patches").mkdir()
        (self.root / "patches/apostrophe-3.2-book.patch").write_text("invalid patch")
        (self.root / "packages.lock").write_text(
            json.dumps(
                {
                    "packages": [],
                    "architecture": "amd64",
                    "snapshot": "20000101T000000Z",
                }
            )
        )
        with (
            patch.object(build, "ROOT", self.root),
            patch.object(build, "CACHE", self.root / "cache"),
            patch.object(build, "DIST", dist),
            patch.object(build, "APP", app),
            patch.object(build, "check_dependencies"),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            build.build()
        self.assertEqual((app / ".complete").read_text(), "previous")
        self.assertEqual(list(dist.iterdir()), [app])


if __name__ == "__main__":
    unittest.main()
