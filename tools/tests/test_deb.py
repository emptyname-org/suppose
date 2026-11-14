"""The install archive has public modes, complete metadata and no build paths."""

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deb


class PackageTest(unittest.TestCase):
    def setUp(self):
        (deb.ROOT / "tmp").mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(dir=deb.ROOT / "tmp")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.app = self.root / "app"
        self.app.mkdir()
        (self.app / ".complete").write_text("built\n")
        (self.app / "bin").mkdir()
        executable = self.app / "bin/suppose"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
        (self.app / "module.py").write_text("value = 1\n")
        (self.app / "module.py").chmod(0o600)
        (self.app / "linked").symlink_to("module.py")
        self.output = self.root / "suppose.deb"

    def archive(self, kind):
        data = subprocess.check_output(["dpkg-deb", f"--{kind}-tarfile", self.output])
        return tarfile.open(fileobj=io.BytesIO(data))

    def test_archive_paths_modes_ownership_and_control_under_private_umask(self):
        previous = os.umask(0o077)
        try:
            deb.package(self.app, "python3 (>= 3.13), coreutils", "0.1.0", self.output)
        finally:
            os.umask(previous)
        with self.archive("fsys") as archive:
            members = {entry.name: entry for entry in archive}
            self.assertEqual(
                members["./usr/bin/suppose"].linkname,
                "/opt/suppose/bin/suppose",
            )
            self.assertEqual(members["./opt/suppose/linked"].linkname, "module.py")
            for entry in members.values():
                self.assertEqual((entry.uid, entry.gid), (0, 0), entry.name)
                if entry.isdir() or entry.name == "./opt/suppose/bin/suppose":
                    self.assertEqual(entry.mode, 0o755, entry.name)
                elif entry.isfile():
                    self.assertEqual(entry.mode, 0o644, entry.name)
                    data = archive.extractfile(entry).read()
                    self.assertNotIn(os.fsencode(deb.ROOT), data)
            for icon in (deb.ROOT / "assets/icons/hicolor").rglob("*.png"):
                name = "./usr/share/icons/hicolor/" + str(
                    icon.relative_to(deb.ROOT / "assets/icons/hicolor")
                )
                self.assertIn(name, members)
            self.assertIn(f"./usr/share/applications/{deb.APP_ID}.desktop", members)
            copyright = archive.extractfile("./usr/share/doc/suppose/copyright").read()
            self.assertIn(b"CC0-1.0", copyright)
            self.assertIn(b"GPL-3", copyright)
        with self.archive("ctrl") as archive:
            self.assertEqual({item.name for item in archive}, {".", "./control"})
            control = archive.extractfile("./control").read().decode()
            for field in (
                "Package: suppose",
                "Version: 0.1.0",
                "Architecture: amd64",
                "Maintainer: emptyname <noreply@emptyname.org>",
                "Depends: python3 (>= 3.13), coreutils",
            ):
                self.assertIn(field + "\n", control)

    def test_dependencies_keep_external_constraints_and_drop_bundled_pins(self):
        fields = {
            "Pre-Depends": "libc6 (>= 2.38)",
            "Depends": (
                "libvte-2.91-common (= 0.80.1-1), libglib2.0-0t64 (>= 2.80), "
                "unavailable | libvte-2.91-common (= 0.80.1-1)"
            ),
        }

        def query(*args, **_kwargs):
            return subprocess.CompletedProcess(args, 0, stdout=fields[args[-1]])

        with patch.object(deb.build, "run", query):
            depends = deb.dependencies(
                [{"name": "libvte-2.91-common", "version": "0.80.1-1"}],
                [Path("vte.deb")],
            )
        self.assertIn("libc6 (>= 2.38)", depends)
        self.assertIn("libglib2.0-0t64 (>= 2.80)", depends)
        self.assertNotIn("libvte", depends)
        for name in deb.build.HOST_PACKAGES:
            self.assertIn(name, depends)

    def test_failed_packaging_preserves_previous_archive(self):
        self.output.write_bytes(b"previous")
        with (
            patch.object(
                deb.build,
                "run",
                side_effect=subprocess.CalledProcessError(1, "dpkg-deb"),
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            deb.package(self.app, "python3", "0.1.0", self.output)
        self.assertEqual(self.output.read_bytes(), b"previous")

    def test_invalid_version_cannot_choose_another_output_path(self):
        with patch.object(deb, "ROOT", self.root):
            for version in ("../outside", "1.0\nDepends: injected", "", "1/bad"):
                (self.root / "VERSION").write_text(version)
                with self.assertRaises(ValueError):
                    deb.read_version()


if __name__ == "__main__":
    unittest.main()
