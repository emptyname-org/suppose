#!/usr/bin/python3
"""Package the built application without installing it."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import build

ROOT = build.ROOT
APP_ID = "io.github.emptyname_org.BookEditor"


def read_version() -> str:
    version = (ROOT / "VERSION").read_text().strip()
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~\-]*", version):
        raise ValueError("VERSION must be a Debian version without an epoch")
    build.run("dpkg", "--validate-version", version)
    return version


def dependencies(packages: list[dict], archives: list[Path]) -> str:
    """Preserve upstream constraints, omitting dependencies satisfied in /opt."""
    bundled = {item["name"]: item["version"] for item in packages}
    required = {*build.HOST_PACKAGES, "python3 (>= 3.13)", "coreutils"}
    for archive in archives:
        for field in ("Pre-Depends", "Depends"):
            value = build.run(
                "dpkg-deb", "-f", str(archive), field, capture_output=True, text=True
            ).stdout
            for group in value.strip().split(","):
                if group and not any(
                    build.satisfies(term, bundled) for term in group.split("|")
                ):
                    required.add(group.strip())
    return ", ".join(sorted(required))


def normalize(tree: Path) -> None:
    for path in (tree, *tree.rglob("*")):
        if path.is_symlink():
            continue
        if not (path.is_file() or path.is_dir()):
            raise ValueError(f"unsupported package entry: {path.relative_to(tree)}")
        mode = 0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644
        path.chmod(mode)


def copyright_text() -> str:
    return (
        "book-editor: original files CC0-1.0; Apostrophe and its patch GPL-3.\n"
        "Bundled packages retain their copyright and licence notices under\n"
        "/opt/book-editor/usr/share/doc/.\n\n"
        + (ROOT / "LICENSE").read_text()
        + "\nApostrophe and its patch: GNU General Public License version 3\n\n"
        + (ROOT / "patches/COPYING").read_text()
    )


def package(app: Path, depends: str, version: str, output: Path) -> None:
    """Stage privately; replace the previous archive only after a complete build."""
    if not (app / ".complete").is_file():
        raise RuntimeError("application is not built; run make build")
    (ROOT / "tmp").mkdir(exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deb-", dir=ROOT / "tmp") as scratch:
        tree = Path(scratch) / "root"
        shutil.copytree(app, tree / "opt/book-editor", symlinks=True)
        command = tree / "usr/bin/book-editor"
        command.parent.mkdir(parents=True)
        command.symlink_to("/opt/book-editor/bin/book-editor")
        desktop = tree / "usr/share/applications"
        desktop.mkdir(parents=True)
        shutil.copyfile(
            ROOT / "assets" / f"{APP_ID}.desktop", desktop / f"{APP_ID}.desktop"
        )
        shutil.copytree(ROOT / "assets/icons/hicolor", tree / "usr/share/icons/hicolor")
        doc = tree / "usr/share/doc/book-editor"
        doc.mkdir(parents=True)
        (doc / "copyright").write_text(copyright_text())
        size = sum(
            path.stat().st_size
            for path in tree.rglob("*")
            if not path.is_symlink() and path.is_file()
        )
        control = tree / "DEBIAN"
        control.mkdir()
        (control / "control").write_text(
            f"Package: book-editor\nVersion: {version}\nArchitecture: amd64\n"
            "Section: editors\nPriority: optional\n"
            "Maintainer: emptyname <noreply@emptyname.org>\n"
            f"Depends: {depends}\nInstalled-Size: {(size + 1023) // 1024}\n"
            "Description: Book editor with an AI terminal\n"
            " Apostrophe with a folder pane and Claude Code or Codex conversations.\n"
        )
        normalize(tree)
        archive = Path(scratch) / "book-editor.deb"
        build.run("dpkg-deb", "--root-owner-group", "--build", str(tree), str(archive))
        os.replace(archive, output)


def main() -> int:
    try:
        with build.build_lock():
            version = read_version()
            lock = json.loads((ROOT / "packages.lock").read_text())
            archives = [
                build.CACHE / Path(item["path"]).name for item in lock["packages"]
            ]
            for item, archive in zip(lock["packages"], archives, strict=True):
                if build.checksum(archive) != item["sha256"]:
                    raise ValueError(
                        f"unverified archive: {archive.name}; run make build"
                    )
            output = build.DIST / f"book-editor_{version}_amd64.deb"
            package(
                build.APP, dependencies(lock["packages"], archives), version, output
            )
        print(f"Packaged {output.relative_to(ROOT)}")
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"deb: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
