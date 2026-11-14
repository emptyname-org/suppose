"""Integration test: Apostrophe with book panes on a hidden display.

Run through tests/smoke/run.sh, which starts it in place of Apostrophe inside
the built editor's environment. SMOKE_SCRATCH: an empty scratch directory.
Exits 0 when every check passes.
"""

import json
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from apostrophe import config
from gi.repository import Adw, Gio, GLib, Gtk, Vte

Gio.Resource._register(
    Gio.resource_load(os.path.join(config.PKGDATA_DIR, "apostrophe.gresource"))
)

from apostrophe.application import Application

from book_panes import channel, folders, panes

if os.environ.get("SMOKE_WATCH") == "0":  # as with the inotify limit reached
    folders.watch_folder = lambda gfile: None

SCRATCH = os.path.abspath(os.environ["SMOKE_SCRATCH"])
BOOK = os.environ["BOOK_ROOT"]
CH1 = os.path.join(BOOK, "ch1.md")
CH2 = os.path.join(BOOK, "part", "ch2.md")
CH1_TEXT = "# One\n\nFirst chapter.\n"
CH2_TEXT = "# Two\n\nSecond chapter.\n"
FAILED: list[str] = []
STATE: dict[str, object] = {}


def check(name, condition):
    print(("PASS  " if condition else "FAIL  ") + name, flush=True)
    if not condition:
        FAILED.append(name)


def read(path):
    with open(path, encoding="utf-8") as source:
        return source.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as target:
        target.write(text)


def tree_names(book):
    model = book.tree_model
    return [
        model.get_item(index).get_item().get_display_name()
        for index in range(model.get_n_items())
    ]


def terminal_text(book):
    text = book.terminal.get_text_format(Vte.Format.TEXT)
    return text[0] if isinstance(text, tuple) else text


def terminal_is_light(book):
    return book.terminal.get_color_background_for_draw().red > 0.5


def append(window, text):
    buffer = window.textview.get_buffer()
    buffer.insert(buffer.get_end_iter(), text)


def start_tool(book, *argv, stdin=None):
    """Start book-view or book-edit as the agent would; the editor answers
    while the scenario waits, finish_tool() collects the result."""
    changed, removed = panes.agent_environment(os.environ, book.channel.path)
    environ = {name: value for name, value in os.environ.items() if name not in removed}
    environ.update(changed)
    write(os.path.join(SCRATCH, "stdin"), stdin or "")
    with open(os.path.join(SCRATCH, "stdin"), encoding="utf-8") as source:
        return subprocess.Popen(
            argv,
            cwd=BOOK,
            env=environ,
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )


def finish_tool(process):
    """(exit status, standard output, standard error)"""
    try:
        out, err = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
        return None, out, err
    return process.returncode, out, err


def scenario(app):
    window = app.get_main_windows()[0]
    book = window.book
    STATE["book"] = book
    STATE["socket"] = book.channel.path if book.channel is not None else None
    yield 2000

    check(
        "channel: socket in a folder private to the user",
        book.channel is not None
        and os.stat(book.channel.directory).st_mode & 0o777 == 0o700
        and os.environ.get("XDG_RUNTIME_DIR")
        == os.path.dirname(book.channel.directory),
    )
    check(
        "agent: book-view answers in the agent's environment",
        "file: (unsaved new text)" in terminal_text(book)
        and "state: saved" in terminal_text(book),
    )

    check(
        "tree: folders first, hidden files skipped",
        tree_names(book) == ["part", "ch1.md", "notes.txt"],
    )
    book.tree_model.get_item(0).set_expanded(True)
    yield 800
    check(
        "tree: folder expands",
        tree_names(book) == ["part", "ch2.md", "ch1.md", "notes.txt"],
    )
    print(
        "folder watching: %s"
        % (book.listings[book.root.get_uri()].monitor is not None),
        flush=True,
    )
    write(os.path.join(BOOK, "added.md"), "new\n")
    write(os.path.join(BOOK, "part", "ch3.md"), "new\n")
    os.remove(os.path.join(BOOK, "notes.txt"))
    yield 2600
    check(
        "tree: follows the folder, expanded folder stays open",
        tree_names(book) == ["part", "ch2.md", "ch3.md", "added.md", "ch1.md"],
    )
    check("agent: runs in the book folder", "agent-ok " + BOOK in terminal_text(book))
    check(
        "agent: environment without the editor's library paths",
        "env=/usr/local/share:/usr/share." in terminal_text(book),
    )
    check(
        "layout: three panes",
        book.tree_pane.get_width() >= 160
        and book.agent_pane.get_width() >= 480
        and book.terminal.get_column_count() >= 50
        and window.editor.get_width() >= 420,
    )

    style = Adw.StyleManager.get_default()
    light = not style.get_dark() and terminal_is_light(book)
    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    yield 400
    dark = style.get_dark() and not terminal_is_light(book)
    style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    yield 400
    check(
        "terminal: follows the light or dark theme",
        light and dark and terminal_is_light(book),
    )

    ch1 = Gio.File.new_for_path(CH1)
    ch2 = Gio.File.new_for_path(CH2)
    mtime = os.stat(CH1).st_mtime_ns
    book.open_file(ch1)
    yield 1700
    check(
        "open: text shown, not marked changed",
        window.textview.get_text() == CH1_TEXT
        and window.loaded_uri == ch1.get_uri()
        and not window.did_change,
    )
    check("open: file not rewritten", os.stat(CH1).st_mtime_ns == mtime)

    append(window, "typed\n")
    check("edit: marked changed", window.did_change)
    yield 1900
    check(
        "autosave: edit on disk",
        read(CH1) == CH1_TEXT + "typed\n" and not window.did_change,
    )

    yield 800  # the file monitor comes back 500 ms after a save
    write(CH1, "from agent\n")
    yield 2600
    check(
        "agent edit: shown in the editor",
        window.textview.get_text() == "from agent\n" and not window.did_change,
    )

    append(window, "mine\n")
    write(CH1, "theirs\n")
    yield 2800
    check(
        "conflict: autosave keeps the disk version",
        read(CH1) == "theirs\n" and window.did_change and window.disk_conflict,
    )
    check("conflict: info bar shown", window.discard_infobar.get_revealed())
    tool = start_tool(book, "book-view", "--json")
    yield 700
    status, out, _err = finish_tool(tool)
    seen = json.loads(out) if status == 0 else {}
    check(
        "view: the unsaved text, not the file on disk",
        seen.get("text") == "from agent\nmine\n"
        and seen.get("unsaved")
        and seen.get("conflict")
        and seen.get("relative") == "ch1.md"
        and seen.get("file") == CH1
        and read(CH1) == "theirs\n",
    )

    window.dismiss_discard_infobar(None, Gtk.ResponseType.CLOSE)
    book.flush()
    yield 900
    check(
        "conflict: keeping mine saves the buffer",
        read(CH1) == "from agent\nmine\n" and not window.did_change,
    )

    yield 800
    write(CH1, "from agent\nmine\n")  # same bytes, new mtime
    yield 2400
    check(
        "touch: identical content is not a conflict",
        not window.disk_conflict and not window.discard_infobar.get_revealed(),
    )

    append(window, "more\n")
    book.open_file(ch2)
    yield 1200
    check(
        "switch: previous file saved, next one shown",
        read(CH1) == "from agent\nmine\nmore\n"
        and window.textview.get_text() == CH2_TEXT
        and window.loaded_uri == ch2.get_uri(),
    )

    buffer = window.textview.get_buffer()
    buffer.select_range(buffer.get_start_iter(), buffer.get_iter_at_line(1)[1])
    book.send_context()
    yield 700
    sent = terminal_text(book)
    check(
        "send: file reference and selection reach the agent",
        "@part/ch2.md" in sent and "# Two" in sent,
    )

    buffer.place_cursor(buffer.get_start_iter())
    tool = start_tool(book, "book-edit", "replace", "Second chapter.", "Chapter 2.")
    yield 700
    status, out, _err = finish_tool(tool)
    check(
        "edit: replace changes the text in the editor",
        status == 0
        and out == "edited part/ch2.md at line 3\n"
        and window.textview.get_text() == "# Two\n\nChapter 2.\n"
        and buffer.get_property("cursor-position") == 0,
    )
    buffer.undo()
    check("edit: one undo step takes it back", window.textview.get_text() == CH2_TEXT)
    yield 1900
    check("edit: autosave follows", read(CH2) == CH2_TEXT and not window.did_change)

    tool = start_tool(book, "book-edit", "replace", "o", "0")
    yield 700
    status, _out, err = finish_tool(tool)
    check(
        "edit: text found more than once is refused",
        status == 1
        and "more than once" in err
        and window.textview.get_text() == CH2_TEXT,
    )

    tool = start_tool(book, "book-edit", "--file", "ch1.md", "insert", "x")
    yield 700
    status, _out, err = finish_tool(tool)
    check(
        "edit: refused for a file the editor does not show",
        status == 1
        and "the editor shows" in err
        and window.textview.get_text() == CH2_TEXT,
    )

    buffer.select_range(buffer.get_iter_at_offset(2), buffer.get_iter_at_offset(5))
    tool = start_tool(book, "book-view", "--selection")
    yield 700
    selected = finish_tool(tool)
    tool = start_tool(book, "book-edit", "--file", "part/ch2.md", "selection", "Deux")
    yield 700
    status, _out, _err = finish_tool(tool)
    check(
        "edit: the selection is shown and replaced",
        selected == (0, "Two", "")
        and status == 0
        and window.textview.get_text() == "# Deux\n\nSecond chapter.\n",
    )

    buffer.place_cursor(buffer.get_end_iter())
    tool = start_tool(book, "book-edit", "insert", stdin="Fin.\n\n")
    yield 700
    status, _out, _err = finish_tool(tool)
    check(
        "edit: insert at the cursor, text from standard input",
        status == 0
        and window.textview.get_text() == "# Deux\n\nSecond chapter.\nFin.\n",
    )

    yield 1900  # autosave, so that the load below discards nothing
    window.load_file(ch1)
    try:
        book._on_request({"op": "view"})
        refused = ""
    except channel.Refused as refusal:
        refused = str(refusal)
    check("channel: refuses while a file loads", "busy" in refused)
    yield 900
    book.open_file(ch2)
    yield 1200
    check(
        "channel: back after the load",
        book._on_request({"op": "view"})["text"] == "# Deux\n\nSecond chapter.\nFin.\n",
    )

    names = ("win.close", "win.save", "win.save_as", "app.new_window")
    window.textview.grab_focus()
    yield 400
    before = [app.get_accels_for_action(name) for name in names]
    book.terminal.grab_focus()
    yield 400
    suspended = [app.get_accels_for_action(name) for name in names]
    window.textview.grab_focus()
    yield 400
    check(
        "shortcuts: off while the terminal has focus, back afterwards",
        all(before)
        and not any(suspended)
        and [app.get_accels_for_action(name) for name in names] == before,
    )

    first_pid = book.child_pid
    book.restart()
    yield 1800
    check(
        "restart: new agent process",
        book.child_pid is not None and book.child_pid != first_pid,
    )
    check(
        "restart: the new agent sees the open file",
        "file: part/ch2.md" in terminal_text(book),
    )
    STATE["pid"] = book.child_pid

    check(
        "tree: unchanged by the editor's own saves",
        tree_names(book) == ["part", "ch2.md", "ch3.md", "added.md", "ch1.md"],
    )
    shot = os.path.join(SCRATCH, "window.png")
    subprocess.run(["import", "-window", "root", shot], check=False, timeout=30)
    check("screenshot written", os.path.exists(shot))

    append(window, "bye\n")
    window.close()
    yield 3000
    check("close: window gone", False)  # reached only if the app is still up
    app.quit()


def drive(app):
    steps = scenario(app)

    def advance():
        try:
            delay = next(steps)
        except StopIteration:
            return GLib.SOURCE_REMOVE
        except Exception as error:  # noqa: BLE001  a broken step must fail the run
            check(f"scenario error: {error!r}", False)
            app.quit()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(delay, advance)
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(500, advance)


def main():
    os.makedirs(os.path.join(BOOK, "part"))
    write(CH1, CH1_TEXT)
    write(CH2, CH2_TEXT)
    write(os.path.join(BOOK, "notes.txt"), "notes\n")
    write(os.path.join(BOOK, ".hidden"), "x\n")

    app = Application("org.gnome.gitlab.somas.Apostrophe")
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    app.connect_after("activate", drive)
    GLib.timeout_add_seconds(
        120, lambda: (check("finished in time", False), app.quit())
    )
    app.run([])

    check(
        "close: unsaved edit saved",
        read(CH2) == "# Deux\n\nSecond chapter.\nFin.\nbye\n",
    )
    check(
        "close: socket removed",
        STATE.get("socket") is not None
        and not os.path.exists(os.path.dirname(STATE["socket"])),
    )
    pid = STATE.get("pid")
    alive = False
    if pid:
        for _attempt in range(20):
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
                break
            GLib.usleep(100000)
    check("close: agent process ended", pid is not None and not alive)
    print(f"{len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
