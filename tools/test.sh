#!/bin/sh
# Cover unit tests and both hidden-display scenarios, including subprocesses.
set -eu
root=$(CDPATH='' cd -- "$(dirname "$0")/.." && pwd)
cd "$root"
/usr/bin/python3 - "$root/tmp/test_runtime" <<'PY'
import os
import sys
# Linux sun_path, allowing the largest PID and tempfile's eight-character suffix.
size = len(os.fsencode(sys.argv[1] + "/book-editor-2147483647-xxxxxxxx/socket"))
if size > 107:
    sys.exit(
        f"test: checkout path is too long for the editor socket ({size} bytes; "
        "limit 107). Move the checkout to a shorter path."
    )
PY
for command in xvfb-run xauth dbus-run-session import timeout flock; do
    if ! command -v "$command" >/dev/null; then
        echo "test: requires xvfb xauth dbus-daemon imagemagick coreutils ($command)" >&2
        exit 1
    fi
done
mkdir -p tmp/coverage tmp/test_runtime
exec 9>tmp/test.lock
flock -n 9 || { echo 'test: another run is active' >&2; exit 1; }
chmod 700 tmp/test_runtime
export TMPDIR="$root/tmp"
export XDG_RUNTIME_DIR="$root/tmp/test_runtime"
export PYTHONUSERBASE="$root/tmp/coverage_user"
export COVERAGE_PROCESS_START="$root/pyproject.toml"
export COVERAGE_FILE="$root/tmp/coverage/.coverage"
export BOOK_EDITOR_DIST="$root/dist/book-editor"
.venv/bin/python -m coverage erase
/usr/bin/python3 - <<'PY'
import pathlib
import site
import sys
root = pathlib.Path.cwd()
target = pathlib.Path(site.getusersitepackages())
target.mkdir(parents=True, exist_ok=True)
version = f"python{sys.version_info.major}.{sys.version_info.minor}"
packages = root / ".venv/lib" / version / "site-packages"
(target / "coverage_start.pth").write_text(
    f"import sys; sys.path.insert(0, {str(packages)!r}); import coverage; coverage.process_startup()\n"
)
PY
PYTHONPATH=src /usr/bin/python3 -m unittest discover -s tests -t .
SMOKE_WATCH=1 timeout --kill-after=10 150 sh tests/smoke/run.sh
SMOKE_WATCH=0 timeout --kill-after=10 150 sh tests/smoke/run.sh
# Avoid measuring the reporting process itself.
unset COVERAGE_PROCESS_START PYTHONUSERBASE
.venv/bin/python -m coverage combine
.venv/bin/python -m coverage report
