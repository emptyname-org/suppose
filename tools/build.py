#!/usr/bin/python3
"""Build the pinned Debian application without installing system packages."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "tmp" / "debs"
DIST = ROOT / "dist"
APP = DIST / "book-editor"
HOST_PACKAGES = (
    "python3-gi",
    "python3-cairo",
    "python3-gi-cairo",
    "python3-chardet",
    "gir1.2-adw-1",
    "gir1.2-gtk-4.0",
    "gir1.2-webkit-6.0",
    "gsettings-desktop-schemas",
    "dconf-gsettings-backend",
    "pandoc",
)
COMMANDS = {
    "dpkg-deb": "dpkg",
    "dpkg-query": "dpkg",
    "patch": "patch",
    "glib-compile-schemas": "libglib2.0-bin",
}


def run(*args: str, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(package: dict, snapshot: str) -> Path:
    target = CACHE / Path(package["path"]).name
    if target.exists() and checksum(target) == package["sha256"]:
        return target
    urls = (
        "https://deb.debian.org/debian/" + package["path"],
        f"https://snapshot.debian.org/archive/debian/{snapshot}/" + package["path"],
    )
    errors = []
    for url in urls:
        partial = target.with_suffix(".partial")
        try:
            # curl provides a total deadline, including trickling responses.
            run(
                "curl",
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--connect-timeout",
                "15",
                "--max-time",
                "180",
                "--retry",
                "2",
                "--retry-max-time",
                "360",
                "--output",
                str(partial),
                url,
            )
            if checksum(partial) != package["sha256"]:
                raise ValueError("SHA256 mismatch")
            partial.replace(target)
            return target
        except (subprocess.CalledProcessError, ValueError) as error:
            errors.append(str(error))
        finally:
            partial.unlink(missing_ok=True)
    raise RuntimeError(f"download failed for {package['name']}: {'; '.join(errors)}")


def installed() -> dict[str, str]:
    result = run(
        "dpkg-query",
        "-W",
        "-f=${Package}\t${Version}\t${Status}\t${Provides}\n",
        capture_output=True,
        text=True,
    )
    versions = {}
    for line in result.stdout.splitlines():
        name, version, status, provides = line.split("\t", 3)
        if status != "install ok installed":
            continue
        versions[name] = version
        for item in provides.split(","):
            match = re.fullmatch(r"\s*([a-z0-9+.-]+)(?:\s*\(=\s*([^()]+)\))?\s*", item)
            if match:
                provided, pinned = match.groups()
                versions.setdefault(provided, pinned or "0")
    return versions


def satisfies(term: str, versions: dict[str, str]) -> bool:
    match = re.fullmatch(
        r"\s*([a-z0-9+.-]+)(?::[a-z0-9-]+)?(?:\s*\((<<|<=|=|>=|>>)\s*([^()]+)\))?\s*",
        term,
    )
    if match is None:
        raise ValueError(f"unsupported dependency: {term}")
    name, operator, wanted = match.groups()
    if name not in versions:
        return False
    return (
        operator is None
        or subprocess.run(
            ["dpkg", "--compare-versions", versions[name], operator, wanted],
            check=False,
        ).returncode
        == 0
    )


def check_dependencies(packages: list[dict], archives: list[Path]) -> None:
    versions = installed()
    missing = set(HOST_PACKAGES) - versions.keys()
    versions.update({item["name"]: item["version"] for item in packages})
    for archive in archives:
        result = run(
            "dpkg-deb",
            "--field",
            str(archive),
            "Depends",
            capture_output=True,
            text=True,
        )
        for group in result.stdout.strip().split(","):
            if group and not any(
                satisfies(term, versions) for term in group.split("|")
            ):
                missing.add(group.strip())
    if missing:
        raise RuntimeError(
            "missing Debian prerequisites: " + "; ".join(sorted(missing))
        )


def check_imports(stage: Path) -> None:
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(stage / "usr/lib/python3/dist-packages"),
        GI_TYPELIB_PATH=str(stage / "usr/lib/x86_64-linux-gnu/girepository-1.0"),
        LD_LIBRARY_PATH=str(stage / "usr/lib/x86_64-linux-gnu"),
    )
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        env.pop(name, None)
    code = """
import gi, cairo, chardet, pypandoc, telnetlib
gi.require_foreign('cairo')
for name, version in [('Gtk','4.0'), ('Adw','1'), ('WebKit','6.0'),
                      ('GtkSource','5'), ('Spelling','1'), ('Vte','3.91')]:
    gi.require_version(name, version)
    __import__('gi.repository.' + name)
"""
    run("/usr/bin/python3", "-c", code, env=env)


@contextlib.contextmanager
def build_lock():
    (ROOT / "tmp").mkdir(exist_ok=True)
    with (ROOT / "tmp/build.lock").open("a") as guard:
        try:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another build or clean is running") from error
        yield


def build(compare: Path | None = None) -> None:
    missing = [
        package
        for command, package in dict(COMMANDS, curl="curl").items()
        if shutil.which(command) is None
    ]
    if missing:
        raise RuntimeError("missing Debian build prerequisites: " + ", ".join(missing))
    lock = json.loads((ROOT / "packages.lock").read_text())
    if (
        run(
            "dpkg", "--print-architecture", capture_output=True, text=True
        ).stdout.strip()
        != lock["architecture"]
    ):
        raise RuntimeError("this build requires Debian 13 amd64")
    CACHE.mkdir(parents=True, exist_ok=True)
    archives = [fetch(item, lock["snapshot"]) for item in lock["packages"]]
    check_dependencies(lock["packages"], archives)
    DIST.mkdir(exist_ok=True)
    backup = DIST / ".book-editor-previous"
    # Recover a promotion interrupted between the two renames.
    if backup.exists() and not APP.exists():
        backup.rename(APP)
    stage = Path(tempfile.mkdtemp(prefix=".book-editor-", dir=DIST))
    try:
        for archive in archives:
            run("dpkg-deb", "-x", str(archive), str(stage))
        with (ROOT / "patches/apostrophe-3.2-book.patch").open("rb") as patch:
            run(
                "patch",
                "--batch",
                "--fuzz=0",
                "-p1",
                "-d",
                str(stage / "usr"),
                stdin=patch,
            )
        run("glib-compile-schemas", str(stage / "usr/share/glib-2.0/schemas"))
        shutil.copytree(
            ROOT / "src",
            stage,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        check_imports(stage)
        if compare is not None:
            run(
                "diff",
                "-r",
                "--exclude=__pycache__",
                str(compare / "usr"),
                str(stage / "usr"),
            )
            print("Reference usr/ matches (excluding generated Python bytecode).")
        (stage / ".complete").write_text(checksum(ROOT / "packages.lock") + "\n")
        if backup.exists():
            shutil.rmtree(backup)
        if APP.exists():
            APP.rename(backup)
        try:
            stage.rename(APP)
        except BaseException:
            if backup.exists():
                backup.rename(APP)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print("Built dist/book-editor")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    try:
        with build_lock():
            if args.clean:
                for path in (APP, *DIST.glob(".book-editor-*")):
                    if path.exists():
                        shutil.rmtree(path)
            else:
                build(args.compare)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"build: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
