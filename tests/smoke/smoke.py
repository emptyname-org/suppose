"""Integration test: Apostrophe with book panes on a hidden display.

Run through tests/smoke/run.sh, which starts it in place of Apostrophe inside
the built editor's environment. SMOKE_SCRATCH: an empty scratch directory.
Exits 0 when every check passes.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from apostrophe import config
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

Gio.Resource._register(
    Gio.resource_load(os.path.join(config.PKGDATA_DIR, "apostrophe.gresource"))
)

from apostrophe import helpers  # after the resources: it loads widgets
from apostrophe.application import Application
from apostrophe.preview_security import PreviewSecurity, PreviewSecurityHandler

from book_panes import (
    agents,
    channel,
    engines,
    folders,
    fonts,
    marks,
    panes,
    paste,
)

WATCHING = os.environ.get("SMOKE_WATCH") != "0"
if not WATCHING:  # as with the inotify limit reached
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


def open_rows(book):
    """The names of the tree's rows that stand out as the open file."""
    return sorted(
        item.get_item().get_item().get_display_name()
        for item in book.shown_rows
        if item.get_child().has_css_class(panes.OPEN_CLASS)
    )


def terminal_text(book):
    text = book.terminal.get_text_format(Vte.Format.TEXT)
    return text[0] if isinstance(text, tuple) else text


def terminal_is_light(book):
    return book.terminal.get_color_background_for_draw().red > 0.5


def append(window, text):
    buffer = window.textview.get_buffer()
    buffer.insert(buffer.get_end_iter(), text)


CLIPBOARD_OWNER = """
import sys
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk
Gtk.init()
kinds = iter(sys.argv[1:])
offers = [
    Gdk.ContentProvider.new_for_bytes(kind, GLib.Bytes.new(text.encode()))
    for kind, text in zip(kinds, kinds)
]
Gdk.Display.get_default().get_clipboard().set_content(
    Gdk.ContentProvider.new_union(offers)
)
GLib.MainLoop().run()
"""


def own_clipboard(*offers):
    """Another program takes the clipboard: kind, text, kind, text ..."""
    return subprocess.Popen([sys.executable, "-c", CLIPBOARD_OWNER, *offers])


def paste_shortcut(view):
    """The action Ctrl+Shift+V has on the view, or None."""
    wanted = Gtk.ShortcutTrigger.parse_string(paste.ACCELERATOR)
    controllers = view.observe_controllers()
    for index in range(controllers.get_n_items()):
        controller = controllers.get_item(index)
        if isinstance(controller, Gtk.ShortcutController):
            for place in range(controller.get_n_items()):
                shortcut = controller.get_item(place)
                if wanted.equal(shortcut.get_trigger()):
                    return shortcut.get_action()
    return None


def mark_rows(button):
    """The rows of the Mark menu: what each tells its action, to the row."""
    rows = {}
    row = button.get_popover().get_child().get_first_child()
    while row is not None:
        if isinstance(row, Gtk.Button):
            target = row.get_action_target_value()
            rows[target.get_string() if target is not None else None] = row
        row = row.get_next_sibling()
    return rows


def preview_asks(window, text):
    """What the preview makes of a file with this text: ask, or show it."""
    asked = SimpleNamespace(
        settings=window.preview_security_handler.settings,
        window=SimpleNamespace(current=SimpleNamespace(security_level=None)),
    )
    PreviewSecurityHandler.assert_security_risk(asked, text)
    return asked.window.current.security_level == PreviewSecurity.ASK


def runs(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def child_runs(pid):
    """True while pid is a child of ours that has not exited."""
    try:
        flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
        return os.waitid(os.P_PID, pid, flags) is None
    except ChildProcessError:
        return False


def processes_in(folder):
    """Pids of the other processes whose working directory is the folder."""
    found = []
    for name in os.listdir("/proc"):
        if name.isdigit() and int(name) != os.getpid():
            try:
                if os.readlink(f"/proc/{name}/cwd") == folder:
                    found.append(int(name))
            except OSError:
                pass  # gone meanwhile, or not ours
    return found


def shutdown_ends(script):
    """Run the script the way an agent runs, hang it up and let the shutdown
    handler see to it: the seconds that took and the processes left."""
    folder = tempfile.mkdtemp(dir=SCRATCH)
    with subprocess.Popen(
        ["/bin/sh", "-c", script],
        cwd=folder,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        start_new_session=True,
    ) as child:
        for _line in range(script.count("echo ready")):
            child.stdout.readline()
        started = time.monotonic()
        panes.end_agent(agents.Agent(child.pid))
        panes.on_shutdown()
        taken = time.monotonic() - started
        left = processes_in(folder)
        for _attempt in range(20):
            if not left:
                break
            time.sleep(0.1)
            left = processes_in(folder)
    return taken, left


def check_shutdown():
    """What the shutdown handler does with the agents hung up before it."""
    patience = agents.KILL_AFTER_SECONDS
    agents.KILL_AFTER_SECONDS = 0.5
    taken, left = shutdown_ends("exec cat")
    check("shutdown: an agent that goes is not waited for", taken < 0.4 and not left)
    taken, left = shutdown_ends("trap '' HUP; echo ready; exec cat")
    check("shutdown: an agent ignoring the hangup is killed", not left)
    check("shutdown: after the time it was given", taken >= 0.4)
    taken, left = shutdown_ends("(trap '' HUP; echo ready; exec sleep 300) & exec cat")
    check("shutdown: what an agent that went left behind is killed", not left)
    check("shutdown: no agent is held any longer", not agents.ENDING)
    agents.KILL_AFTER_SECONDS = patience


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
    check(
        "tree: the open file's row stands out, no other", open_rows(book) == ["ch1.md"]
    )
    rows = book.list_view.get_model()
    for position in range(rows.get_n_items()):  # what the pointer over a row asks
        book.list_view.activate_action(
            "list.select-item", GLib.Variant("(ubb)", (position, False, False))
        )
    check(
        "tree: the pointer over a row selects nothing",
        not any(rows.is_selected(position) for position in range(rows.get_n_items()))
        and open_rows(book) == ["ch1.md"],
    )

    append(window, "typed\n")
    check("edit: marked changed", window.did_change)
    yield 1900
    check(
        "autosave: edit on disk",
        read(CH1) == CH1_TEXT + "typed\n" and not window.did_change,
    )

    # formatted text of another program's clipboard arrives as Markdown
    view = window.textview
    buffer = view.get_buffer()
    typed = CH1_TEXT + "typed\n"
    owner = own_clipboard(
        "text/html",
        "<h2>Title</h2><ul><li>one</li><li><em>two</em></li></ul><pre>x *y*</pre>",
        "text/plain;charset=utf-8",
        "Title\none\ntwo\nx *y*",
    )
    yield 2500
    check(
        "paste: another program's clipboard offers HTML",
        view.get_clipboard().get_formats().contain_mime_type("text/html"),
    )
    buffer.place_cursor(buffer.get_end_iter())
    view.activate_action("book." + paste.ACTION, None)
    yield 1500
    check(
        "paste: HTML arrives as Markdown",
        view.get_text() == typed + "## Title\n\n- one\n- *two*\n\n```\nx *y*\n```",
    )
    buffer.undo()
    check("paste: one undo step takes it back", view.get_text() == typed)
    owner.kill()
    owner.wait()
    owner = own_clipboard("text/plain;charset=utf-8", "plain *text*")
    yield 2500
    shortcut = paste_shortcut(view)
    check("paste: Ctrl+Shift+V is the editor's", shortcut is not None)
    if shortcut is not None:
        shortcut.activate(Gtk.ShortcutActionFlags.EXCLUSIVE, view, None)
    yield 1000
    check(
        "paste: without HTML the plain text arrives",
        view.get_text() == typed + "plain *text*",
    )
    buffer.undo()
    owner.kill()
    owner.wait()
    menu = view.get_extra_menu()
    label = menu.get_item_attribute_value(0, "label", None)
    check(
        "paste: in the editor's menu, above the spelling",
        label is not None
        and label.get_string() == paste.LABEL
        and menu.get_n_items() == 2,
    )
    yield 1900
    check(
        "paste: undone, the file is as before",
        view.get_text() == typed and read(CH1) == typed and not window.did_change,
    )

    # text marked in a colour from the Mark menu beside Save
    def select(part):
        at = view.get_text().index(part)
        buffer.select_range(
            buffer.get_iter_at_offset(at), buffer.get_iter_at_offset(at + len(part))
        )

    def painted(part):
        at = view.get_text().index(part)
        tags = buffer.get_iter_at_offset(at).get_tags()
        return [tag.get_property("name") for tag in tags if tag.get_property("name")]

    rows = mark_rows(book.mark_button)
    beside = book.mark_button.get_prev_sibling()
    check(
        "mark: the menu stands beside Save, a row to a colour",
        book.mark_button.get_label() == "_Mark"
        and beside is not None
        and beside.get_label() == "_Save"
        and list(rows) == [*marks.COLORS, None]
        and all(row.get_sensitive() for row in rows.values()),
    )
    yellow = typed.replace(
        "First chapter.", marks.opening("yellow") + "First chapter.</mark>"
    )
    select("First chapter.")
    rows["yellow"].emit("clicked")
    bounds = buffer.get_selection_bounds()
    check(
        "mark: the selection is marked and stays selected",
        view.get_text() == yellow
        and len(bounds) == 2
        and buffer.get_text(*bounds, True) == "First chapter.",
    )
    check(
        "mark: painted, its tags hidden",
        painted("First") == ["book-mark-yellow"]
        and "book-mark-tag" in painted("<mark")
        and "book-mark-tag" in painted("</mark>"),
    )
    converted: list[str] = []
    window.preview_handler.preview_converter.convert(yellow, True, converted.append)
    yield 1900
    check("mark: saved with the file", read(CH1) == yellow and not window.did_change)
    check(
        "mark: its tags are no words",
        window.editor.stats_handler.words == 4,
    )
    check(
        "mark: shown by the preview that leaves HTML out",
        len(converted) == 1
        and '<span class="mark-yellow">First chapter.</span>' in converted[0],
    )
    check(
        "mark: the preview asks about other HTML only",
        not preview_asks(window, yellow) and preview_asks(window, yellow + "<b>x</b>"),
    )
    buffer.undo()
    check("mark: one undo step takes it back", view.get_text() == typed)
    buffer.redo()
    buffer.place_cursor(buffer.get_iter_at_offset(yellow.index("chapter")))
    window.activate_action("book." + marks.ACTION, GLib.Variant("s", "green"))
    check(
        "mark: at the cursor it takes another colour",
        view.get_text() == yellow.replace("yellow", "green")
        and painted("First") == ["book-mark-green"],
    )
    rows[None].emit("clicked")
    check(
        "mark: removed",
        view.get_text() == typed and painted("First") == [],
    )
    yield 1900
    check(
        "mark: removed, the file is as before",
        read(CH1) == typed and not window.did_change,
    )

    # the tags of a mark are hidden in the editor, and stay whole
    def shown():
        return Gtk.TextBuffer.get_text(buffer, *buffer.get_bounds(), False)

    def put(part):
        buffer.place_cursor(buffer.get_iter_at_offset(view.get_text().index(part)))

    def typing(text):
        """As GTK commits what is typed."""
        buffer.begin_user_action()
        buffer.delete_selection(True, True)
        taken = buffer.insert_interactive_at_cursor(text, -1, True)
        buffer.end_user_action()
        return taken

    opened, closed = marks.opening("yellow"), marks.CLOSING
    select("First chapter.")
    window.activate_action("book." + marks.ACTION, GLib.Variant("s", "yellow"))
    check(
        "hidden: the editor shows a mark without its tags, the text keeps them",
        shown() == typed and view.get_text() == yellow,
    )
    put(closed)
    view.emit("backspace")
    taken = typing("!")
    edited = yellow.replace("chapter.", "chapter!")
    check(
        "hidden: keys reach the text beside a tag",
        taken and view.get_text() == edited and shown() == typed.replace(".", "!"),
    )
    put("chapter!")
    typing("\n")  # Apostrophe inserts it, in its own time
    yield 400
    check(
        "hidden: a new line in a mark breaks the mark",
        view.get_text()
        == edited.replace("First chapter!", f"First{closed} \n{opened}chapter!")
        and shown() == typed.replace("First chapter.", "First \nchapter!"),
    )
    buffer.undo()
    check("hidden: ... in one undo step", view.get_text() == edited)
    yield 1900
    check("hidden: saved with its tags", read(CH1) == edited and not window.did_change)
    select("First chapter!")
    view.emit("copy-clipboard")
    buffer.place_cursor(buffer.get_end_iter())
    view.emit("paste-clipboard")
    yield 600
    check(
        "hidden: a mark copied in the editor is pasted whole",
        view.get_text() == edited + opened + "First chapter!" + closed
        and shown() == typed.replace(".", "!") + "First chapter!",
    )
    buffer.undo()
    buffer.place_cursor(buffer.get_end_iter())
    view.activate_action("book." + paste.ACTION, None)
    yield 600
    check(
        "paste: what was copied in the editor arrives as it shows",
        view.get_text() == edited + "First chapter!",
    )
    buffer.undo()
    select("First chapter!")
    view.emit("backspace")
    check(
        "hidden: a mark goes with the last of its text",
        view.get_text() == typed.replace("First chapter.", ""),
    )
    buffer.undo()
    put("chapter!")
    window.activate_action("book." + marks.CLEAR, None)
    select("!")
    typing(".")
    yield 1900
    check(
        "hidden: unmarked, the file is as before",
        view.get_text() == typed and read(CH1) == typed and not window.did_change,
    )

    # a file is shown again where it was left: cursor and scrolling
    long_path = os.path.join(BOOK, "long.md")
    long_text = "".join(
        f"Paragraph {number} of a long chapter, with words enough for a line.\n\n"
        for number in range(1, 401)
    )
    write(long_path, long_text)
    long_file = Gio.File.new_for_path(long_path)
    adjustment = window.editor.scrolledwindow.get_vadjustment()

    def stands(place, scroll):
        return (
            buffer.get_property("cursor-position") == place
            and abs(adjustment.get_value() - scroll) < 2
        )

    buffer.place_cursor(buffer.get_iter_at_offset(3))
    book.open_file(long_file)
    yield 1500
    check(
        "place: a file not seen before begins at its beginning",
        view.get_text() == long_text and stands(0, 0),
    )
    place = long_text.index("Paragraph 30 ")
    buffer.place_cursor(buffer.get_iter_at_offset(place))
    yield 1200  # Apostrophe scrolls to the cursor
    adjustment.set_value(4000)  # and the writer reads on, far from it
    yield 300
    check("place: the long file is scrolled", stands(place, 4000))
    book.open_file(ch1)
    yield 1200
    check(
        "place: the file before is shown where it was left",
        view.get_text() == typed and buffer.get_property("cursor-position") == 3,
    )
    book.open_file(long_file)
    yield 1500
    check(
        "place: the long file is shown where it was left",
        view.get_text() == long_text and stands(place, 4000),
    )
    yield 800  # the file monitor is back
    write(long_path, long_text + "more\n")
    yield 2600
    check(
        "place: changed on disk, it stays where it stands",
        view.get_text() == long_text + "more\n" and stands(place, 4000),
    )
    documents = [book._document()]
    window.new_document()
    documents.append(book._document())
    window.new_document()
    documents.append(book._document())
    check(
        "document: a new text is another one than the new text before",
        len(set(documents)) == 3 and window.current.gfile is None,
    )
    book.open_file(long_file)
    yield 1500
    check(
        "place: kept while a new text was open",
        view.get_text() == long_text + "more\n" and stands(place, 4000),
    )
    book.open_file(ch1)
    yield 1200
    os.remove(long_path)
    yield 2600
    check(
        "place: back in the first file",
        view.get_text() == typed
        and buffer.get_property("cursor-position") == 3
        and not window.did_change,
    )

    # the bars hide while typing, to the agent too, and the keyboard going
    # to another pane does not bring them back
    bar = window.toolbar_revealer
    window.reveal_headerbar_bottombar()
    view.grab_focus()
    yield 200
    shown = bar.get_reveal_child()
    book._on_terminal_key(None, Gdk.KEY_x, 0, Gdk.ModifierType(0))
    check("bars: typing to the agent hides them", shown and not bar.get_reveal_child())
    book.terminal.grab_focus()
    yield 300
    check(
        "bars: stay hidden when the keyboard goes to the agent",
        window.get_focus() is book.terminal and not bar.get_reveal_child(),
    )
    view.grab_focus()
    window.lookup_action("find").activate(None)
    yield 300
    check(
        "bars: shown for the search",
        window.searchbar.search_mode_enabled and bar.get_reveal_child(),
    )
    window.searchbar.search_mode_enabled = False
    yield 300

    # Preferences: a font for the editor, one for the agent
    def editor_font():
        described = view.get_pango_context().get_font_description()
        sizes = [name for name in view.get_css_classes() if name.startswith("size")]
        return described.get_family(), described.get_size(), sizes

    def agent_font():
        return book.terminal.get_font().to_string(), book.terminal.get_char_width()

    own = editor_font(), view.get_left_margin(), agent_font()
    app.activate_action("preferences", None)
    yield 800
    dialog = window.get_visible_dialog()
    rows = getattr(dialog, "font_rows", {})
    check(
        "fonts: Preferences offer one for the editor, one for the agent, no Large Text",
        sorted(rows) == [fonts.EDITOR_KEY, fonts.TERMINAL_KEY]
        and not dialog.bigger_text_row.get_visible()
        and not any(row.reset.get_sensitive() for row in rows.values())
        and own[0][2] != [],
    )
    rows[fonts.EDITOR_KEY].button.set_font_desc(
        Pango.FontDescription.from_string("Serif 20")
    )
    yield 600
    family, size, sizes = editor_font()
    check(
        "fonts: the editor takes the chosen one, the agent keeps its own",
        window.settings.get_string(fonts.EDITOR_KEY) == "Serif 20"
        and family == "Serif"
        and size > 1.5 * own[0][1]
        and sizes == []
        and view.get_left_margin() != own[1]
        and agent_font() == own[2],
    )
    rows[fonts.TERMINAL_KEY].button.set_font_desc(
        Pango.FontDescription.from_string("Monospace 16")
    )
    yield 600
    check(
        "fonts: the agent takes its own",
        agent_font()[0] == "Monospace 16"
        and agent_font()[1] > own[2][1]
        and editor_font()[0] == "Serif",
    )
    for row in rows.values():
        row.reset.emit("clicked")
    yield 600
    check(
        "fonts: the defaults come back",
        (editor_font(), view.get_left_margin(), agent_font()) == own
        and window.settings.get_string(fonts.EDITOR_KEY) == ""
        and window.settings.get_string(fonts.TERMINAL_KEY) == "",
    )
    dialog.close()
    yield 300
    check(
        "fonts: closed Preferences listen no more",
        not any(
            GObject.signal_handler_is_connected(row.settings, row.handler)
            for row in rows.values()
        ),
    )
    view.grab_focus()

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
    append(window, "late\n")  # typed while the save before the switch runs
    check("switch: waits for the save", window.saving and book.wanted is ch2)
    yield 1500
    check(
        "switch: previous file saved with the text typed meanwhile, next shown",
        read(CH1) == "from agent\nmine\nmore\nlate\n"
        and window.textview.get_text() == CH2_TEXT
        and window.loaded_uri == ch2.get_uri(),
    )
    check(
        "tree: the row that stands out follows the editor",
        open_rows(book) == ["ch2.md"],
    )
    book.tree_model.get_item(0).set_expanded(False)
    yield 400
    check(
        "tree: no row stands out with the file's folder closed", open_rows(book) == []
    )
    book.tree_model.get_item(0).set_expanded(True)
    yield 800
    check(
        "tree: opened again, the folder shows the open file",
        open_rows(book) == ["ch2.md"],
    )

    # P1-2: a save asked for while a write is in flight waits its turn
    append(window, "one\n")
    book.flush()
    append(window, "two\n")
    saved = []
    window.save_document(callback=lambda _window: saved.append(read(CH2)))
    check(
        "save: a second save waits for the write in flight",
        window.saving and len(window._queued_saves) == 1,
    )
    yield 1200
    check(
        "save: both writes done in turn, state matches the disk",
        saved == [CH2_TEXT + "one\ntwo\n"]
        and read(CH2) == CH2_TEXT + "one\ntwo\n"
        and not window.did_change
        and not window.saving
        and window.base_bytes == (CH2_TEXT + "one\ntwo\n").encode(),
    )
    write(CH2, CH2_TEXT)  # as the agent would: back to the start
    yield 2600
    check(
        "save: disk change after queued saves still reloads",
        window.textview.get_text() == CH2_TEXT and not window.did_change,
    )

    # P1-3: only the newest load counts, and nothing is typed into a text
    # that is about to be replaced
    shown = []
    set_text = window.textview.set_text
    window.textview.set_text = lambda text: (shown.append(text), set_text(text))[1]
    window.load_file(ch1)
    window.load_file(ch2)
    window.load_file(ch1)
    check(
        "load: no typing while a load is under way", not window.textview.get_editable()
    )
    # the buffer still holds ch2: a save now must not put that into ch1
    window.save_document()
    window.save_document(quiet=True)
    check("load: no save while a load is under way", not window.saving)
    yield 900
    del window.textview.set_text
    check(
        "load: of several loads only the newest is shown",
        shown == ["from agent\nmine\nmore\nlate\n"]
        and read(CH1) == "from agent\nmine\nmore\nlate\n"
        and window.loaded_uri == ch1.get_uri()
        and window.textview.get_editable(),
    )
    errors = []
    show_error = helpers.show_error
    helpers.show_error = lambda _parent, message: errors.append(message)
    before = "from agent\nmine\nmore\nlate\n"
    window.load_file(Gio.File.new_for_path(os.path.join(BOOK, "missing.md")))
    check(
        "load: a missing file changes nothing",
        len(errors) == 1
        and window.current.gfile.equal(ch1)
        and window.loaded_uri == ch1.get_uri()
        and window.textview.get_editable(),
    )
    locked = os.path.join(BOOK, "locked.md")
    write(locked, "unreadable\n")
    os.chmod(locked, 0)
    window.load_file(Gio.File.new_for_path(locked))
    yield 900
    helpers.show_error = show_error
    os.remove(locked)
    check(
        "load: after a failed load the previous file is current again",
        len(errors) == 2
        and window.current.gfile.equal(ch1)
        and window.loaded_uri == ch1.get_uri()
        and window.textview.get_editable()
        and window.textview.get_text() == before,
    )
    append(window, "kept\n")
    yield 1900
    check(
        "load: and its edits are saved to it",
        read(CH1) == "from agent\nmine\nmore\nlate\nkept\n" and not window.did_change,
    )
    book.open_file(ch2)
    yield 1200
    check(
        "load: next file shown",
        window.loaded_uri == ch2.get_uri() and window.textview.get_text() == CH2_TEXT,
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

    spawned = []
    on_spawned = book._on_spawned

    def record(terminal, pid, error, *rest):
        spawned.append(pid)
        return on_spawned(terminal, pid, error, *rest)

    book._on_spawned = record
    first_pid = book.child_pid
    book.restart()
    book.restart()  # asked twice: the second finds one on its way
    yield 1800
    check(
        "restart: asked twice, one new agent process",
        spawned == [book.child_pid]
        and book.child_pid not in (None, first_pid)
        and not runs(first_pid),
    )
    check(
        "restart: the new agent sees the open file",
        "file: part/ch2.md" in terminal_text(book),
    )

    command = book.command
    book.command = "trap '' HUP; echo stubborn; exec cat"
    book.restart()
    yield 1500
    stubborn = book.child_pid
    agents.KILL_AFTER_SECONDS = 0.5
    book.command = command
    book.restart()
    yield 300
    check(
        "restart: an agent ignoring the hangup is still there at first",
        book.child_pid == stubborn and "stubborn" in terminal_text(book),
    )
    yield 1900
    check(
        "restart: then it is killed and replaced",
        book.child_pid not in (None, stubborn)
        and not runs(stubborn)
        and len(spawned) == 3,
    )
    # an agent that goes at the hangup and leaves behind one that does not
    book.command = "(trap '' HUP; exec sleep 300) & exec cat"
    book.restart()
    yield 1500
    check("restart: agent with a helper of its own", len(processes_in(BOOK)) == 2)
    book.command = command
    book.restart()
    yield 1900
    check(
        "restart: what the old agent left behind is killed",
        processes_in(BOOK) == [book.child_pid] and len(spawned) == 5,
    )
    # an agent that cannot be held is not kept
    hold = agents.Agent

    def no_descriptor(_pid):
        raise OSError(24, "Too many open files")

    agents.Agent = no_descriptor
    book.restart()
    yield 1500
    agents.Agent = hold
    check(
        "restart: an agent that cannot be held is killed",
        book.child_pid is None
        and len(spawned) == 6
        and not processes_in(BOOK)
        and "Too many open files" in terminal_text(book),
    )
    book.restart()
    yield 1500
    check(
        "restart: the next one is held again",
        processes_in(BOOK) == [book.child_pid] and len(spawned) == 7,
    )

    # the agent engines, in place of a command of the user's own
    kept = os.path.join(BOOK, ".suppose")
    check(
        "engine: none chosen yet, claude is the one",
        book.engine == "claude" and not os.path.exists(kept),
    )
    book.command = None
    book.restart()
    yield 2500
    text = " ".join(terminal_text(book).split())
    check(
        "engine: claude opens the folder's conversation",
        "engine=claude args=--append-system-prompt" in text
        and "--session-id" in text
        and book.running_engine == "claude",
    )
    check(
        "engine: the conversation is on record in the folder",
        len(read(os.path.join(kept, "claude", "session")).strip()) == 36,
    )
    choice = Gtk.DropDown.new_from_strings(list(engines.ENGINES.values()))
    choice.set_selected(list(engines.ENGINES).index("codex"))
    book._on_engine_chosen(choice, None)
    yield 3000
    text = " ".join(terminal_text(book).split())
    check(
        "engine: codex takes over, the editor's tools with it",
        "engine=codex args=-c mcp_servers.suppose.command=" in text
        and "SUPPOSE_SOCKET=" in text
        and book.running_engine == "codex",
    )
    check(
        "engine: the choice is kept in the folder", read(kept + "/engine") == "codex\n"
    )
    check(
        "engine: the folder's record stays out of the tree",
        tree_names(book) == ["part", "ch2.md", "ch3.md", "added.md", "ch1.md"],
    )
    # another engine is chosen while one starts
    os.killpg(book.child_pid, signal.SIGKILL)
    yield 1200
    book.restart()
    check("engine: a start is under way", book.spawning is not None)
    book.set_engine("claude")
    book.set_engine("claude")
    book.set_engine("eliza")
    yield 3500
    text = " ".join(terminal_text(book).split())
    check(
        "engine: the one chosen meanwhile is the one that runs",
        "engine=claude args=" in text
        and "--session-id" in text
        and book.running_engine == "claude"
        and read(kept + "/engine") == "claude\n",
    )
    book.command = command
    book.restart()
    yield 2500
    check(
        "engine: a command of the user's own runs again",
        processes_in(BOOK) == [book.child_pid] and "agent-ok" in terminal_text(book),
    )
    STATE["pid"] = book.child_pid

    # another editor is started for another folder
    fake = os.path.join(SCRATCH, "fake-bin")
    os.makedirs(fake)
    write(
        os.path.join(fake, "suppose"),
        "#!/bin/sh\n"
        'echo "$1|${BOOK_ROOT:-}|${SUPPOSE_SOCKET:-}|${PYTHONPATH:-}" >"$OUT"\n',
    )
    os.chmod(os.path.join(fake, "suppose"), 0o755)
    tools, panes.TOOLS_DIR = panes.TOOLS_DIR, fake
    os.environ["OUT"] = os.path.join(SCRATCH, "launched")
    panes.launch_editor(os.path.join(BOOK, "part"))
    panes.TOOLS_DIR = tools
    yield 800
    check(
        "folder: another editor starts on it, free of this one's environment",
        read(os.path.join(SCRATCH, "launched")) == os.path.join(BOOK, "part") + "|||\n",
    )
    check(
        "folder: the one in use is remembered for the next start",
        read(panes.state_file("last-folder")) == BOOK + "\n",
    )

    check(
        "tree: unchanged by the editor's own saves",
        tree_names(book) == ["part", "ch2.md", "ch3.md", "added.md", "ch1.md"],
    )
    shot = os.path.join(SCRATCH, "window.png")
    subprocess.run(["import", "-window", "root", shot], check=False, timeout=30)
    check("screenshot written", os.path.exists(shot))

    if WATCHING:
        # the window closes with unsaved text and an agent ignoring the hangup
        # and has started a helper that ignores it too
        book.command = "(trap '' HUP; exec sleep 300) & trap '' HUP; exec cat"
        book.restart()
        yield 1500
        check("close: agent and helper run", len(processes_in(BOOK)) == 2)
        STATE["pid"] = book.child_pid
        append(window, "bye\n")
        # it closes because another folder is opened

        class Dialog:
            def __init__(self, folder):
                self.folder = folder

            def select_folder_finish(self, _result):
                if self.folder is None:
                    raise GLib.GError("dismissed")
                return Gio.File.new_for_path(self.folder)

        def no_editor(_folder):
            raise GLib.GError("no such file")

        launched = STATE["launched"] = []
        panes.launch_editor = no_editor
        book.open_folder(Gio.File.new_for_path(os.path.join(BOOK, "part")))
        panes.launch_editor = launched.append
        book._on_folder_chosen(Dialog(None), None)
        book._on_folder_chosen(Dialog(BOOK), None)
        book._on_folder_chosen(Dialog(os.path.join(BOOK, "missing")), None)
        check(
            "folder: no other folder, no other editor: this window stays",
            launched == [] and not book.closed,
        )
        book._on_folder_chosen(Dialog(os.path.join(BOOK, "part")), None)
    else:
        # the application quits with the window open while an agent starts
        os.kill(book.child_pid, signal.SIGKILL)
        yield 700
        check(
            "agent: its end is noticed",
            book.child_pid is None and "session ended" in terminal_text(book),
        )
        append(window, "bye\n")
        yield 1900
        book.restart()
        check("agent: a start is under way", book.spawning is not None)
        app.quit()
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

    check_shutdown()

    app = Application("org.gnome.gitlab.somas.Apostrophe")
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    app.connect_after("activate", drive)
    GLib.timeout_add_seconds(
        140, lambda: (check("finished in time", False), app.quit())
    )
    app.run([])

    if WATCHING:
        check(
            "folder: another one opens in an editor of its own",
            STATE.get("launched") == [os.path.join(BOOK, "part")],
        )
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
    left = processes_in(BOOK)
    for _attempt in range(20):
        if not left:
            break
        GLib.usleep(100000)
        left = processes_in(BOOK)
    check(
        "close: agent process ended",
        isinstance(pid, int) and not child_runs(pid),
    )
    check("close: no process left in the book folder", not left)
    check("close: no agent is held any longer", not agents.ENDING)
    print(f"{len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
