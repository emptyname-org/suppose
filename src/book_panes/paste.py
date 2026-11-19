"""Formatted text of the clipboard pasted as Markdown.

Ctrl+Shift+V in the editor, or "Paste HTML as Markdown" in its menu: the
HTML a browser or a word processor puts on the clipboard beside the plain
text is converted by pandoc (paste.lua tidies it) and inserted at the
cursor, as one undo step. Without HTML on the clipboard, or with HTML
pandoc cannot convert, the plain text is pasted as by Ctrl+V. Images stay
links; nothing is fetched.

A paste ends in one of four ways: Markdown inserted, plain text pasted,
nothing (the document changed meanwhile, or the clipboard or pandoc took
too long: a beep), or cancelled with the window.
"""

import codecs
import os
import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

MAX_HTML_BYTES = (
    2 * 1024 * 1024
)  # more is pasted plain: pandoc takes seconds a megabyte
READ_CHUNK = 256 * 1024
READ_SECONDS = 5.0  # for the clipboard's owner to hand its HTML over
CONVERT_SECONDS = 15.0  # for pandoc
FILTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paste.lua")
OPTIONS = (
    "--sandbox",  # reads nothing but its input and the filter
    "--from=html",
    "--to=gfm-raw_html",  # what Markdown cannot say is left out, not kept as HTML
    "--wrap=none",
    "--lua-filter=" + FILTER,
)
ACTION = "paste-markdown"
ACCELERATOR = "<Control><Shift>v"
LABEL = "Paste HTML as Markdown"
CONVERTED, FAILED, LATE = "converted", "failed", "late"
PRE = re.compile(r"<pre\b[^<>]*>|</pre\s*>", re.IGNORECASE)
CODE = re.compile(r"(\s*)<code\b", re.IGNORECASE)
MARKS = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def decode(data):
    """The clipboard's HTML as text: UTF-8, or what its byte order mark
    says; the NULs some browsers add go."""
    for mark, name in MARKS:
        if data.startswith(mark):
            text = data[len(mark) :].decode(name, "replace")
            break
    else:
        text = data.decode("utf-8", "replace")
    return text.replace("\0", "")


def prepare(html):
    """The HTML as pandoc reads it best: it takes a pre for code only with
    a code right behind it. One pass, whatever the HTML."""
    parts, taken, inside, bare = [], 0, False, False
    for tag in PRE.finditer(html):
        if tag.group().startswith("</"):
            if bare:
                parts += [html[taken : tag.start()], "</code>"]
                taken = tag.start()
            inside = bare = False
        elif not inside:
            inside = True
            code = CODE.match(html, tag.end())
            parts.append(html[taken : tag.end()])
            taken = tag.end()
            if code is None:
                bare = True
                parts.append("<code>")
            else:
                taken = code.end(1)
    parts.append(html[taken:])
    return "".join(parts)


def pandoc_command():
    """The pandoc that converts; None without pandoc."""
    program = GLib.find_program_in_path("pandoc")
    return [program, *OPTIONS] if program else None


def shut(stream):
    """Close a stream of the clipboard, if there is one and it lets us."""
    if stream is not None:
        try:
            stream.close(None)
        except GLib.GError:
            pass  # a read is under way, or it is closed


class Conversion:
    """One run of pandoc on some HTML. done(outcome, markdown) is called
    once, from the main loop, never from within the constructor: CONVERTED
    with the Markdown, FAILED, or LATE when pandoc took too long. Not at
    all after cancel()."""

    def __init__(self, html, done, command=None, seconds=CONVERT_SECONDS):
        self.done = done
        self.late = False
        self.timer = 0
        self.process = None
        self.cancellable = Gio.Cancellable()
        argv = pandoc_command() if command is None else command
        try:
            if argv is None:
                raise GLib.GError("no pandoc")
            self.process = Gio.Subprocess.new(
                argv,
                Gio.SubprocessFlags.STDIN_PIPE
                | Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.GError:
            self.timer = GLib.idle_add(self._on_no_start)
            return
        self.timer = GLib.timeout_add(int(seconds * 1000), self._on_late)
        self.process.communicate_async(
            GLib.Bytes.new(html.encode("utf-8", "replace")),
            self.cancellable,
            self._on_end,
        )

    def _on_no_start(self):
        self.timer = 0
        self._tell(FAILED, None)
        return GLib.SOURCE_REMOVE

    def _on_late(self):
        self.timer = 0
        self.late = True
        if self.process is not None:
            self.process.force_exit()  # its end is told by _on_end
        return GLib.SOURCE_REMOVE

    def _on_end(self, process, result):
        try:
            _ok, output, _errors = process.communicate_finish(result)
        except GLib.GError:
            output = None  # cancelled, or its pipes broke
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.late:
            self._tell(LATE, None)
            return
        markdown = ""
        if output is not None and process.get_successful():
            markdown = output.get_data().decode("utf-8", "replace").strip()
        if markdown:
            self._tell(CONVERTED, markdown)
        else:
            self._tell(FAILED, None)

    def _tell(self, outcome, markdown):
        done, self.done = self.done, None
        if done is not None:
            done(outcome, markdown)

    def cancel(self):
        self.done = None
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.cancellable.cancel()
        if self.process is not None:
            self.process.force_exit()


class MarkdownPaste:
    """The paste of one editor. view: its text view; document(): what tells
    the open document from another."""

    def __init__(self, view, document, convert=Conversion):
        self.view = view
        self.document = document
        self.convert = convert
        self.reading = None  # cancellable of the paste under way
        self.stream = None  # the clipboard's HTML, while it is read
        self.conversion = None
        self.timer = 0
        self.started_in = None
        self.chunks = []
        self.size = 0

    def paste(self):
        """Paste the clipboard's HTML as Markdown, or its plain text."""
        view = self.view
        if self.reading is not None or not view.get_editable():
            return  # one at a time; the one under way arrives
        clipboard = view.get_clipboard()
        if not clipboard.get_formats().contain_mime_type("text/html"):
            self._paste_plain()
            return
        self.reading = reading = Gio.Cancellable()
        self.started_in = self.document()
        self.chunks, self.size = [], 0
        self.timer = GLib.timeout_add(int(READ_SECONDS * 1000), self._on_read_late)
        clipboard.read_async(
            ["text/html"], GLib.PRIORITY_DEFAULT, reading, self._on_offer, reading
        )

    def close(self):
        """The window goes: nothing is pasted any more."""
        self._end()

    # ---- the clipboard's HTML ----------------------------------------------

    def _on_offer(self, clipboard, result, reading):
        try:
            stream, _kind = clipboard.read_finish(result)
        except GLib.GError:
            stream = None
        if reading is not self.reading:
            shut(stream)  # given up meanwhile
        elif stream is None:
            self._fall_back()
        else:
            self.stream = stream
            self._read(stream, reading)

    def _read(self, stream, reading):
        stream.read_bytes_async(
            READ_CHUNK, GLib.PRIORITY_DEFAULT, reading, self._on_bytes, reading
        )

    def _on_bytes(self, stream, result, reading):
        try:
            data = stream.read_bytes_finish(result).get_data()
        except GLib.GError:
            data = None
        if reading is not self.reading:
            shut(stream)  # no read is left on it now
        elif data is None:
            self._fall_back()
        elif data:
            self.chunks.append(data)
            self.size += len(data)
            if self.size > MAX_HTML_BYTES:
                self._fall_back()
            else:
                self._read(stream, reading)
        else:  # all of it
            self._stop_timer()
            shut(stream)
            self.stream = None
            html = prepare(decode(b"".join(self.chunks)))
            self.chunks = []
            self.conversion = self.convert(html, self._on_converted)

    def _on_read_late(self):
        self.timer = 0
        self._end()
        self._refuse()
        return GLib.SOURCE_REMOVE

    # ---- its Markdown --------------------------------------------------------

    def _on_converted(self, outcome, markdown):
        self.conversion = None
        if outcome == FAILED:
            self._fall_back()
            return
        same = self._same_document()
        self._end()
        if outcome == CONVERTED and same:
            self._insert(markdown)
        else:
            self._refuse()

    def _insert(self, markdown):
        view = self.view
        buffer = view.get_buffer()
        buffer.paste_ongoing = True  # Apostrophe: pasted text is not completed
        buffer.begin_user_action()
        try:
            buffer.delete_selection(False, True)
            buffer.insert_at_cursor(markdown, -1)
        finally:
            buffer.end_user_action()
            buffer.paste_ongoing = False
        view.scroll_mark_onscreen(buffer.get_insert())

    # ---- the ends ------------------------------------------------------------

    def _same_document(self):
        return self.view.get_editable() and self.document() == self.started_in

    def _fall_back(self):
        """No Markdown to be had: the plain text, where it was asked for."""
        same = self._same_document()
        self._end()
        if same:
            self._paste_plain()
        else:
            self._refuse()

    def _paste_plain(self):
        self.view.emit("paste-clipboard")

    def _refuse(self):
        self.view.get_display().beep()

    def _stop_timer(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0

    def _end(self):
        """Leave the paste under way, if any; what it still tells is ignored."""
        self._stop_timer()
        if self.reading is not None:
            self.reading.cancel()
            self.reading = None
        if self.stream is not None:
            shut(self.stream)  # with a read under way: by that read's end
            self.stream = None
        if self.conversion is not None:
            self.conversion.cancel()
            self.conversion = None
        self.chunks = []
        self.started_in = None


def install(view, group, paste):
    """Ctrl+Shift+V on the view and an entry in its menu, for paste; the
    action goes into the group, which the view finds as "book"."""
    action = Gio.SimpleAction.new(ACTION, None)
    action.connect("activate", lambda *_args: paste.paste())
    group.add_action(action)
    shortcuts = Gtk.ShortcutController()
    shortcuts.add_shortcut(
        Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string(ACCELERATOR),
            Gtk.NamedAction.new("book." + ACTION),
        )
    )
    view.add_controller(shortcuts)
    menu = Gio.Menu()
    menu.append(LABEL, "book." + ACTION)
    extra = view.get_extra_menu()
    if extra is not None:  # Apostrophe's: spelling
        menu.append_section(None, extra)
    view.set_extra_menu(menu)
