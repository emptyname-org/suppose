#!/bin/sh
# Runs smoke.py inside the built editor on a hidden X display, with
# throwaway settings. BOOK_EDITOR_DIST: the build to test (default: dist).
set -eu
root=$(cd "$(dirname "$0")/../.." && pwd)
app=${BOOK_EDITOR_DIST:-$root/dist/book-editor}
mkdir -p "$root/tmp/smoke"
scratch=$(mktemp -d "$root/tmp/smoke/run.XXXXXX")
mkdir -m 700 "$scratch/home" "$scratch/runtime"
echo "scratch: $scratch"
# shellcheck disable=SC2016  # the agent's shell expands these
agent='echo agent-ok "$PWD"
echo "env=${PYTHONPATH:-}${LD_LIBRARY_PATH:-}${GI_TYPELIB_PATH:-}${GSETTINGS_SCHEMA_DIR:-}${XDG_DATA_DIRS:-}."
book-view --status
exec cat'
exec env -u WAYLAND_DISPLAY -u PYTHONPATH -u LD_LIBRARY_PATH -u GI_TYPELIB_PATH \
    -u GSETTINGS_SCHEMA_DIR -u XDG_DATA_DIRS -u BOOK_EDITOR_SOCKET \
    GDK_BACKEND=x11 GSK_RENDERER=cairo GSETTINGS_BACKEND=memory \
    HOME="$scratch/home" XDG_CONFIG_HOME="$scratch/home/.config" \
    XDG_DATA_HOME="$scratch/home/.local/share" \
    XDG_CACHE_HOME="$scratch/home/.cache" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$scratch/runtime}" \
    SMOKE_SCRATCH="$scratch" BOOK_ROOT="$scratch/book" BOOK_AGENT_CMD="$agent" \
    BOOK_EDITOR_MAIN="$root/tests/smoke/smoke.py" \
    dbus-run-session -- \
    xvfb-run -a -s "-screen 0 1680x1000x24" \
    "$app/bin/book-editor"
