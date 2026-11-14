"""Tree pane, agent terminal and their link to the Apostrophe editor.

BookPanes wraps the editor of an Apostrophe MainWindow. The editor keeps the
file on disk current (autosave) and follows changes the agent makes; the
agent reads and edits the open text through the editor channel.

Environment:
  BOOK_ROOT         folder shown in the tree and used as the agent's cwd
                    (default: current directory)
  BOOK_AGENT_CMD    shell command run in the terminal (default: claude)
  BOOK_AGENT_TITLE  title of the terminal pane (default: Agent)
"""

import os
import signal
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
from apostrophe import config
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte

from . import channel
from .folders import (
    ATTRIBUTES,
    FolderListing,
    compare_entries,
    is_directory,
    is_text,
)

MAX_OPEN_BYTES = 5 * 1024 * 1024
AUTOSAVE_MS = 1000
SYNC_DEBOUNCE_MS = 150
DISK_POLL_SECONDS = 1
TREE_POLL_SECONDS = 2
BUSY_RETRY_MS = 100
BUSY_RETRIES = 50
KILL_AFTER_MS = 5000  # an agent that ignores the hangup is killed
START_WAIT_SECONDS = 2.0  # shutdown: for agent starts under way to report
TREE_WIDTH = 240
EDITOR_MIN_WIDTH = 420
AGENT_MIN_WIDTH = 320
AGENT_SHARE = 0.38  # of the window width, up to TERMINAL_COLUMNS
TERMINAL_COLUMNS = 84
TERMINAL_PADDING = 26
WINDOW_SIZE = (1760, 1040)  # upper bound, reduced to fit the monitor
# terminal colours by app theme (dark or not): foreground, background, palette
TERMINAL_COLORS = {
    False: (
        "#1e1e1e",
        "#ffffff",
        (
            "#24292e", "#d73a49", "#28a745", "#b08800",
            "#0366d6", "#6f42c1", "#1b7c83", "#6a737d",
            "#959da5", "#cb2431", "#22863a", "#9a6700",
            "#005cc5", "#5a32a3", "#3192aa", "#d1d5da",
        ),
    ),
    True: (
        "#deddda",
        "#1d1d20",
        (
            "#241f31", "#c01c28", "#2ec27e", "#f5c211",
            "#1e78e4", "#9841bb", "#0ab9dc", "#c0bfbc",
            "#5e5c64", "#ed333b", "#57e389", "#f8e45c",
            "#51a1ff", "#c061cb", "#4fd2fd", "#f6f5f4",
        ),
    ),
}  # fmt: skip

CSS = """
vte-terminal.book-terminal { padding: 10px 12px; }
.book-tree row { min-height: 30px; }
.book-pane { background-color: @window_bg_color; }
"""

# Environment the launcher adds for Apostrophe itself; kept out of the agent.
APP_PREFIX = os.path.dirname(os.path.dirname(config.PKGDATA_DIR))
MODULE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(MODULE_DIR, "bin")  # book-view, book-edit
LAUNCHER_PATH_VARS = (
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "GI_TYPELIB_PATH",
    "XDG_DATA_DIRS",
    "GSETTINGS_SCHEMA_DIR",
)


def rgba(spec):
    color = Gdk.RGBA()
    color.parse(spec)
    return color


def default_window_size(display):
    width, height = WINDOW_SIZE
    monitors = display.get_monitors()
    if monitors.get_n_items():
        area = monitors.get_item(0).get_geometry()
        width = min(width, max(900, area.width - 80))
        height = min(height, max(600, area.height - 100))
    return width, height


def agent_environment(environ, socket_path):
    """The agent's environment as changes to ours: (variables to set, names
    to unset). The launcher's additions for Apostrophe are undone; the
    editor's commands and its socket are added."""
    changed = {}
    removed = []
    for name in LAUNCHER_PATH_VARS:
        value = environ.get(name)
        if value is None:
            continue
        kept = [
            part
            for part in value.split(os.pathsep)
            if part and part != MODULE_DIR and not part.startswith(APP_PREFIX)
        ]
        if not kept:
            removed.append(name)
        elif os.pathsep.join(kept) != value:
            changed[name] = os.pathsep.join(kept)
    path = environ.get("PATH") or os.defpath
    if TOOLS_DIR not in path.split(os.pathsep):
        changed["PATH"] = TOOLS_DIR + os.pathsep + path
    if socket_path:
        changed[channel.SOCKET_ENV] = socket_path
    elif channel.SOCKET_ENV in environ:
        removed.append(channel.SOCKET_ENV)  # an outer editor's, not ours
    return changed, removed


def signal_agent(pid, signum):
    """Signal the agent's process group, or the agent alone when it has left
    the group. False when there is nothing to signal."""
    for send in (os.killpg, os.kill):
        try:
            send(pid, signum)
        except (ProcessLookupError, PermissionError):
            continue
        return True
    return False


def agent_runs(pid):
    """True while pid is a child of ours that has not exited. It is not
    reaped here, so a pid that says True is never a reused one."""
    try:
        flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
        return os.waitid(os.P_PID, pid, flags) is None
    except ChildProcessError:
        return False


OPEN_PANES = set()  # panes of windows not closed yet
STARTING = set()  # panes with an agent start under way
ENDING = {}  # agents hung up by closed windows: pid -> time.monotonic() to kill


def end_agent(pid):
    """Hang up an agent whose window is gone; it is killed when it stays."""
    if signal_agent(pid, signal.SIGHUP):
        ENDING[pid] = time.monotonic() + KILL_AFTER_MS / 1000
        GLib.timeout_add(KILL_AFTER_MS, kill_ending, pid)


def kill_ending(pid):
    if ENDING.pop(pid, None) is not None and agent_runs(pid):
        signal_agent(pid, signal.SIGKILL)
    return GLib.SOURCE_REMOVE


def on_shutdown(*_args):
    """The application ends and the main loop with it: close what quitting
    left open, give the agents hung up their time, kill those that stay."""
    for panes in list(OPEN_PANES):
        panes.close()
    # a start under way reports its child through the main context
    context = GLib.MainContext.default()
    patience = time.monotonic() + START_WAIT_SECONDS
    while STARTING and time.monotonic() < patience:
        if not context.iteration(False):
            time.sleep(0.01)
    while ENDING:
        now = time.monotonic()
        for pid, deadline in list(ENDING.items()):
            if not agent_runs(pid):
                del ENDING[pid]
            elif now >= deadline:
                signal_agent(pid, signal.SIGKILL)
                del ENDING[pid]
        if ENDING:
            time.sleep(0.05)


def position(text_iter):
    return {"line": text_iter.get_line() + 1, "column": text_iter.get_line_offset() + 1}


def context_text(root, gfile, selection):
    """Text handed to the agent: the open file and the selected passage."""
    parts = []
    if gfile is not None:
        relative = root.get_relative_path(gfile)
        parts.append("@" + (relative or gfile.get_path() or gfile.get_uri()))
    if selection:
        parts.append('"""\n' + selection + '\n"""')
    return " ".join(parts) + " " if parts else ""


class BookPanes:
    def __init__(self, window):
        self.window = window
        self.root = Gio.File.new_for_path(
            os.path.abspath(os.environ.get("BOOK_ROOT") or os.getcwd())
        )
        self.command = os.environ.get("BOOK_AGENT_CMD") or "claude"
        self.child_pid = None  # the running agent
        self.spawning = None  # cancellable of a start under way
        self.restart_pending = False  # the agent was told to go, start anew
        self.kill_timer = 0
        self.closed = False
        self.wanted = None  # file asked for, shown once edits are saved
        self.autosave_timer = 0
        self.sync_timer = 0
        self.disk_poll_timer = 0
        self.tree_poll_timer = 0
        self.saved_accels = None
        self.listings = {}  # folder uri -> FolderListing, for every folder shown

        self._install_css()
        self.tree_pane = self._build_tree()
        self.agent_pane = self._build_agent()
        self._wrap_window()
        self._connect_editor()
        self.channel = self._open_channel()
        self._spawn()
        OPEN_PANES.add(self)
        application = window.get_application()
        if application is not None and not getattr(application, "book_hooked", 0):
            application.book_hooked = True
            application.connect("shutdown", on_shutdown)

    # ---- layout -----------------------------------------------------------

    def _install_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.window.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _pane(self, title, child, start_buttons, end_buttons):
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))
        header.set_show_start_title_buttons(start_buttons)
        header.set_show_end_title_buttons(end_buttons)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(child)
        view.add_css_class("book-pane")
        return view, header

    def _build_tree(self):
        self.tree_model = Gtk.TreeListModel.new(
            self._folder_model(self.root), False, False, self._child_model
        )
        self.selection = Gtk.SingleSelection(
            model=self.tree_model, autoselect=False, can_unselect=True
        )
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        self.list_view = Gtk.ListView(
            model=self.selection, factory=factory, single_click_activate=True
        )
        self.list_view.add_css_class("navigation-sidebar")
        self.list_view.add_css_class("book-tree")
        self.list_view.connect("activate", self._on_row_activated)
        scroller = Gtk.ScrolledWindow(
            child=self.list_view, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        title = self.root.get_basename() or self.root.get_path()
        pane, _header = self._pane(title, scroller, True, False)
        pane.set_size_request(160, -1)
        return pane

    def _build_agent(self):
        terminal = Vte.Terminal()
        terminal.add_css_class("book-terminal")
        terminal.set_scrollback_lines(20000)
        terminal.set_scroll_on_keystroke(True)
        terminal.set_mouse_autohide(True)
        terminal.set_size(TERMINAL_COLUMNS, 40)
        terminal.connect("child-exited", self._on_child_exited)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_terminal_key)
        terminal.add_controller(keys)

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self._suspend_accels)
        focus.connect("leave", self._restore_accels)
        terminal.add_controller(focus)
        self.terminal = terminal
        self.style = Adw.StyleManager.get_default()
        self.style_handler = self.style.connect("notify::dark", self._color_terminal)
        self._color_terminal()

        title = os.environ.get("BOOK_AGENT_TITLE") or "Agent"
        pane, header = self._pane(title, terminal, False, True)
        pane.set_size_request(AGENT_MIN_WIDTH, -1)

        send = Gtk.Button(icon_name="mail-send-symbolic")
        send.set_tooltip_text("Send the open file and selection to the agent")
        send.connect("clicked", lambda *_: self.send_context())
        header.pack_start(send)

        restart = Gtk.Button(icon_name="view-refresh-symbolic")
        restart.set_tooltip_text("Restart the agent")
        restart.connect("clicked", lambda *_: self.restart())
        header.pack_start(restart)
        return pane

    def _color_terminal(self, *_args):
        """The terminal follows the app's light or dark theme."""
        foreground, background, palette = TERMINAL_COLORS[self.style.get_dark()]
        self.terminal.set_colors(
            rgba(foreground), rgba(background), [rgba(spec) for spec in palette]
        )

    def _wrap_window(self):
        window = self.window
        editor = window.get_content()
        # Apostrophe's panels drop their children when unrooted; this move
        # is the one unroot that is not a teardown.
        window.panels.keep_children_on_unroot = True
        window.set_content(None)

        inner = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        inner.set_start_child(editor)
        inner.set_end_child(self.agent_pane)
        inner.set_resize_start_child(True)
        inner.set_resize_end_child(False)
        inner.set_shrink_start_child(False)
        inner.set_shrink_end_child(False)

        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        outer.set_start_child(self.tree_pane)
        outer.set_end_child(inner)
        outer.set_resize_start_child(False)
        outer.set_resize_end_child(True)
        outer.set_shrink_start_child(False)
        outer.set_shrink_end_child(False)
        outer.set_position(TREE_WIDTH)

        editor.set_size_request(EDITOR_MIN_WIDTH, -1)
        window.set_content(outer)
        window.panels.keep_children_on_unroot = False
        window.set_default_size(*default_window_size(window.get_display()))
        inner.add_tick_callback(self._place_agent)

        # Window buttons live in the outer panes, not in the editor's bar.
        bar = window.headerbar.headerbar

        def hide_buttons(*_args):
            if bar.get_show_start_title_buttons():
                bar.set_show_start_title_buttons(False)
            if bar.get_show_end_title_buttons():
                bar.set_show_end_title_buttons(False)

        bar.connect("notify::show-start-title-buttons", hide_buttons)
        bar.connect("notify::show-end-title-buttons", hide_buttons)
        hide_buttons()

    def _place_agent(self, paned, _clock):
        """Give the agent its share of the window once the size is known."""
        width = paned.get_width()
        if width <= 0:
            return GLib.SOURCE_CONTINUE
        wanted = self.terminal.get_char_width() * TERMINAL_COLUMNS + TERMINAL_PADDING
        share = int(self.window.get_width() * AGENT_SHARE)
        agent = max(AGENT_MIN_WIDTH, min(wanted, share))
        paned.set_position(max(EDITOR_MIN_WIDTH, width - agent))
        return GLib.SOURCE_REMOVE

    # ---- tree -------------------------------------------------------------

    def _folder_model(self, gfile):
        uri = gfile.get_uri()
        listing = self.listings.get(uri)
        if listing is None:
            listing = self.listings[uri] = FolderListing(gfile)
        else:
            listing.refresh()  # shown again after a collapse
        return Gtk.SortListModel.new(
            listing.store, Gtk.CustomSorter.new(compare_entries)
        )

    def _child_model(self, info, *_args):
        if not is_directory(info):
            return None
        return self._folder_model(info.get_attribute_object("standard::file"))

    def _poll_tree(self):
        """Catches what folder watching misses, or all of it without watching.
        Listings stay for the life of the window: one per folder ever shown,
        so that a folder which comes back is followed again."""
        for listing in self.listings.values():
            listing.poll()
        return GLib.SOURCE_CONTINUE

    def _setup_row(self, _factory, item):
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image())
        box.append(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE))
        item.set_child(Gtk.TreeExpander(child=box))

    def _bind_row(self, _factory, item):
        row = item.get_item()
        info = row.get_item()
        expander = item.get_child()
        expander.set_list_row(row)
        box = expander.get_child()
        icon = info.get_symbolic_icon()
        if icon is not None:
            box.get_first_child().set_from_gicon(icon)
        else:
            box.get_first_child().set_from_icon_name(
                "folder-symbolic" if is_directory(info) else "text-x-generic-symbolic"
            )
        box.get_last_child().set_text(info.get_display_name())

    def _on_row_activated(self, _view, position):
        row = self.selection.get_item(position)
        if row is None:
            return
        info = row.get_item()
        if is_directory(info):
            row.set_expanded(not row.get_expanded())
            return
        gfile = info.get_attribute_object("standard::file")
        try:  # the row's info may be old
            info = gfile.query_info(ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
        except GLib.GError:
            info = None
        if info is None or not is_text(info) or info.get_size() > MAX_OPEN_BYTES:
            self.window.get_display().beep()
            return
        self.open_file(gfile)

    # ---- documents --------------------------------------------------------

    def _connect_editor(self):
        window = self.window
        window.textview.get_buffer().connect("changed", self._on_text_changed)
        window.connect("destroy", self.close)
        # the file monitor can be late or unavailable; a stat a second is cheap
        self.disk_poll_timer = GLib.timeout_add_seconds(
            DISK_POLL_SECONDS, self._poll_disk
        )
        self.tree_poll_timer = GLib.timeout_add_seconds(
            TREE_POLL_SECONDS, self._poll_tree
        )

    def close(self, *_args):
        """The window is gone, or the application quits with it open."""
        if self.closed:
            return
        self.closed = True
        OPEN_PANES.discard(self)
        self.wanted = None
        for name in (
            "autosave_timer",
            "sync_timer",
            "disk_poll_timer",
            "tree_poll_timer",
            "kill_timer",
        ):
            timer = getattr(self, name)
            if timer:
                GLib.source_remove(timer)
                setattr(self, name, 0)
        for listing in self.listings.values():
            listing.close()
        self.listings.clear()
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        self._restore_accels()
        if self.style_handler:
            self.style.disconnect(self.style_handler)
            self.style_handler = 0
        self.restart_pending = False
        if self.spawning is not None:
            self.spawning.cancel()  # _on_spawned ends one that still starts
        if self.child_pid is not None:
            end_agent(self.child_pid)
            self.child_pid = None

    def _buffer_matches_file(self):
        window = self.window
        gfile = window.current.gfile
        return gfile is not None and window.loaded_uri == gfile.get_uri()

    def open_file(self, gfile):
        """Show gfile in the editor; edits of the current file are saved
        first. The latest request wins."""
        self.wanted = gfile
        self._open(gfile, 0)

    def _open(self, gfile, attempt):
        if self.wanted is not gfile:
            return GLib.SOURCE_REMOVE  # a later request took over
        window = self.window
        current = window.current.gfile
        if current is not None and current.equal(gfile):
            self.wanted = None
            window.textview.grab_focus()
        elif window.saving:
            # let the save in flight finish, give up rather than wait forever
            if attempt < BUSY_RETRIES:
                GLib.timeout_add(BUSY_RETRY_MS, self._open, gfile, attempt + 1)
            else:
                self.wanted = None
        elif (
            window.did_change
            and self._buffer_matches_file()
            and not window.disk_conflict
        ):
            # then look again: text typed during the save is saved as well
            window.save_document(
                callback=lambda _window: self._open(gfile, 0), quiet=True
            )
        else:
            window.check_change(lambda _window: self._load(gfile))
        return GLib.SOURCE_REMOVE

    def _load(self, gfile):
        if self.wanted is gfile:
            self.wanted = None
            self.window.load_file(gfile)
            self.window.textview.grab_focus()

    def _on_text_changed(self, *_args):
        if self.autosave_timer:
            GLib.source_remove(self.autosave_timer)
        self.autosave_timer = GLib.timeout_add(AUTOSAVE_MS, self._on_autosave_timer)

    def _on_autosave_timer(self):
        self.autosave_timer = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def flush(self):
        """Write unsaved edits now. Returns True when a save was started."""
        window = self.window
        if not window.did_change or not self._buffer_matches_file():
            return False
        if window.disk_conflict:
            return False  # the user decides in the info bar
        if window.saving:
            self._on_text_changed()  # look again after the save in flight
            return False
        window.save_document(quiet=True)
        return True

    def _poll_disk(self):
        self.check_disk()
        return GLib.SOURCE_CONTINUE

    def check_disk(self):
        """Compare the file's version on disk with the one the buffer knows."""
        window = self.window
        gfile = window.current.gfile
        if (
            gfile is None
            or window.disk_etag is None
            or window.saving
            or self.sync_timer
            or not self._buffer_matches_file()
        ):
            return
        try:
            info = gfile.query_info(
                Gio.FILE_ATTRIBUTE_ETAG_VALUE, Gio.FileQueryInfoFlags.NONE, None
            )
            etag = info.get_etag()
        except GLib.GError:
            etag = None  # deleted or unreadable
        if etag != window.disk_etag:
            self.sync_with_disk()

    def sync_with_disk(self):
        """The file on disk may have changed: reload it, or ask when the
        buffer has unsaved edits."""
        if not self.sync_timer:
            self.sync_timer = GLib.timeout_add(SYNC_DEBOUNCE_MS, self._sync)

    def _sync(self):
        self.sync_timer = 0
        window = self.window
        gfile = window.current.gfile
        if gfile is None or window.saving or not self._buffer_matches_file():
            return GLib.SOURCE_REMOVE  # a finished save or load checks again
        try:
            _ok, data, etag = gfile.load_contents(None)
        except GLib.GError:
            # deleted or unreadable: keep the text, the next save recreates it
            window.disk_etag = None
            window.discard_infobar.set_revealed(True)
            return GLib.SOURCE_REMOVE

        window.disk_etag = etag
        if data == window.base_bytes:
            # touched or changed back: nothing to take over
            window.disk_conflict = False
            window.discard_infobar.set_revealed(False)
            if window.did_change:
                self._on_text_changed()
            return GLib.SOURCE_REMOVE
        if window.did_change:
            window.disk_conflict = True
            window.discard_infobar.set_revealed(True)
            return GLib.SOURCE_REMOVE

        buffer = window.textview.get_buffer()
        offset = buffer.get_property("cursor-position")
        adjustment = window.editor.scrolledwindow.get_vadjustment()
        scroll = adjustment.get_value()

        def place():
            end = buffer.get_char_count()
            buffer.place_cursor(buffer.get_iter_at_offset(min(offset, end)))
            limit = adjustment.get_upper() - adjustment.get_page_size()
            adjustment.set_value(max(0, min(scroll, limit)))
            return GLib.SOURCE_REMOVE

        # at once, with the bytes just read: no moment in which typed text
        # could be lost to a load still under way
        if window.show_contents(gfile, data, etag):
            window.discard_infobar.set_revealed(False)
            GLib.idle_add(place)
        else:
            # no longer text: keep the editor's version, the writer decides
            window.disk_conflict = True
            window.discard_infobar.set_revealed(True)
        return GLib.SOURCE_REMOVE

    def save_before_close(self):
        """Closing saves instead of asking. True: the window closes once the
        save is done. False: nothing to save here, Apostrophe decides."""
        window = self.window
        if not window.did_change or not self._buffer_matches_file():
            return False
        if window.disk_conflict or window.saving:
            return False
        window.save_document(callback=lambda win: win.close(), quiet=True)
        return True

    # ---- editor channel ---------------------------------------------------

    def _open_channel(self):
        try:
            return channel.Channel(self._on_request)
        except (OSError, GLib.GError) as error:
            self._notice(f"book-view and book-edit are unavailable: {error}")
            return None

    def _on_request(self, request):
        """Answer a request from book-view or book-edit."""
        operations = {"view": self._view, "edit": self._edit}
        operation = operations.get(request.get("op"))
        if operation is None:
            raise channel.Refused("unknown request")
        window = self.window
        gfile = window.current.gfile
        if gfile is not None and not self._buffer_matches_file():
            raise channel.Refused("the editor is busy with a file, try again")
        path = gfile.get_path() if gfile is not None else None
        wanted = request.get("file")
        if wanted is not None:
            wanted = channel.text_field(request, "file")
            if path is None or os.path.realpath(wanted) != os.path.realpath(path):
                raise channel.Refused(
                    "the editor shows {}, not {}".format(path or "a new text", wanted)
                )
        buffer = window.textview.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        if len(text) != buffer.get_char_count():
            raise channel.Refused("the text holds embedded objects")
        reply = operation(request, buffer, text)
        reply["file"] = path or (gfile.get_uri() if gfile is not None else None)
        reply["relative"] = (
            self.root.get_relative_path(gfile) if gfile is not None else None
        )
        return reply

    def _view(self, request, buffer, text):
        window = self.window
        selection = None
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            selection = {
                "start": position(start),
                "end": position(end),
                "text": buffer.get_text(start, end, True),
            }
        reply = {
            "unsaved": bool(window.did_change),
            "conflict": bool(window.disk_conflict),
            "lines": buffer.get_line_count(),
            "characters": len(text),
            "cursor": position(buffer.get_iter_at_mark(buffer.get_insert())),
            "selection": selection,
        }
        if request.get("text", True):
            reply["text"] = text
        return reply

    def _edit(self, request, buffer, text):
        selection = None
        if buffer.get_has_selection():
            selection = tuple(
                bound.get_offset() for bound in buffer.get_selection_bounds()
            )
        cursor = buffer.get_iter_at_mark(buffer.get_insert()).get_offset()
        start, end, new = channel.plan_edit(text, cursor, selection, request)
        buffer.begin_user_action()  # one undo step
        try:
            if end > start:
                buffer.delete(
                    buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end)
                )
            if new:
                buffer.insert(buffer.get_iter_at_offset(start), new)
        finally:
            buffer.end_user_action()
        return {
            "line": buffer.get_iter_at_offset(start).get_line() + 1,
            "characters": buffer.get_char_count(),
        }

    # ---- agent ------------------------------------------------------------

    def _spawn(self):
        if self.spawning is not None or self.closed:
            return
        # the terminal adds its variables to ours; unsetting takes env -u
        socket_path = self.channel.path if self.channel is not None else None
        changed, removed = agent_environment(os.environ, socket_path)
        argv = ["/usr/bin/env"]
        for name in removed:
            argv += ["-u", name]
        self.spawning = Gio.Cancellable()
        STARTING.add(self)
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            self.root.get_path(),
            argv + ["/bin/sh", "-c", self.command],
            [name + "=" + value for name, value in changed.items()],
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            self.spawning,
            self._on_spawned,
        )

    def _on_spawned(self, _terminal, pid, error, *_args):
        self.spawning = None
        STARTING.discard(self)
        if error is not None or pid == -1:
            if not self.closed:
                message = error.message if error is not None else "spawn failed"
                self._notice("could not start the agent: " + message)
        elif self.closed:
            end_agent(pid)  # the window went away meanwhile
        else:
            self.child_pid = pid

    def _on_child_exited(self, _terminal, _status):
        self.child_pid = None
        if self.kill_timer:
            GLib.source_remove(self.kill_timer)
            self.kill_timer = 0
        if self.closed:
            return
        if self.restart_pending:
            self.restart_pending = False
            self.terminal.reset(True, True)
            self._spawn()
        else:
            self._notice("session ended, press Restart to start it again")

    def _notice(self, text):
        self.terminal.feed(("\r\n\x1b[2m[" + text + "]\x1b[0m\r\n").encode())

    def restart(self):
        if self.spawning is not None or self.restart_pending:
            return  # a new agent is on its way
        if self.child_pid is not None:
            if signal_agent(self.child_pid, signal.SIGHUP):
                self.restart_pending = True  # started when this one has exited
                self.kill_timer = GLib.timeout_add(
                    KILL_AFTER_MS, self._on_kill_timer, self.child_pid
                )
                return
            self.child_pid = None  # gone already, no exit will be reported
        self.terminal.reset(True, True)
        self._spawn()

    def _on_kill_timer(self, pid):
        self.kill_timer = 0
        if self.child_pid == pid and agent_runs(pid):
            signal_agent(pid, signal.SIGKILL)
        return GLib.SOURCE_REMOVE

    def send_context(self):
        """Paste a reference to the open file and the selection to the agent."""
        window = self.window
        self.flush()
        buffer = window.textview.get_buffer()
        selection = ""
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            selection = buffer.get_text(start, end, False)
        gfile = window.current.gfile if self._buffer_matches_file() else None
        text = context_text(self.root, gfile, selection)
        if text and self.child_pid is not None:
            self.terminal.paste_text(text)  # bracketed, control chars filtered
        self.terminal.grab_focus()

    def _on_terminal_key(self, _controller, keyval, _keycode, state):
        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if control and shift and keyval in (Gdk.KEY_C, Gdk.KEY_c):
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if control and shift and keyval in (Gdk.KEY_V, Gdk.KEY_v):
            self.terminal.paste_clipboard()
            return True
        if shift and not control and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.terminal.feed_child(b"\x1b\r")  # newline in the agent's prompt
            return True
        return False

    def _suspend_accels(self, *_args):
        """Ctrl+S, Ctrl+W ... belong to the terminal while it has the focus."""
        if self.saved_accels is not None:
            return
        self.flush()
        app = self.window.get_application()
        if app is None:
            return
        names = ["app." + name for name in app.list_actions()]
        names += ["win." + name for name in self.window.list_actions()]
        self.saved_accels = {}
        for name in names:
            accels = app.get_accels_for_action(name)
            if accels:
                self.saved_accels[name] = accels
                app.set_accels_for_action(name, [])

    def _restore_accels(self, *_args):
        if self.saved_accels is None:
            return
        app = self.window.get_application()
        if app is not None:
            for action, accels in self.saved_accels.items():
                app.set_accels_for_action(action, accels)
        self.saved_accels = None
