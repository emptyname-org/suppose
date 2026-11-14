"""Folder listings for the tree pane."""

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

SKIP_NAMES = frozenset({"node_modules", "__pycache__"})
TREE_REFRESH_MS = 200
ATTRIBUTES = "standard::name,standard::display-name,standard::type,standard::is-hidden,standard::content-type,standard::size,standard::symbolic-icon"
MODIFIED = "time::modified,time::modified-usec"


def is_visible(info, *_args):
    return not info.get_is_hidden() and info.get_name() not in SKIP_NAMES


def is_directory(info):
    return info.get_file_type() == Gio.FileType.DIRECTORY


def compare_entries(first, second, *_args):
    """Folders first, then case-insensitive by name."""
    key_first = (not is_directory(first), first.get_display_name().casefold())
    key_second = (not is_directory(second), second.get_display_name().casefold())
    return (key_first > key_second) - (key_first < key_second)


def is_text(info):
    content_type = info.get_content_type() or ""
    return Gio.content_type_is_a(content_type, "text/plain")


def modified_stamp(gfile):
    """Modification time of a file or folder, None when it cannot be read."""
    try:
        info = gfile.query_info(MODIFIED, Gio.FileQueryInfoFlags.NONE, None)
    except GLib.GError:
        return None
    return (
        info.get_attribute_uint64("time::modified"),
        info.get_attribute_uint32("time::modified-usec"),
    )


def watch_folder(gfile):
    """A change monitor for the folder, None when the system has none left
    (inotify limit reached); polling covers that case."""
    try:
        return gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
    except GLib.GError:
        return None


def visible_entries(gfile):
    """Name -> info of the folder's visible entries; None when unreadable."""
    entries = {}
    try:
        children = gfile.enumerate_children(
            ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None
        )
        for info in children:
            if is_visible(info):
                info.set_attribute_object("standard::file", children.get_child(info))
                entries[info.get_name()] = info
        children.close(None)
    except GLib.GError:
        return None
    return entries


class FolderListing:
    """One folder's entries as a list model, updated in place so that rows
    which stay keep their state (expanded subfolders, selection)."""

    def __init__(self, gfile):
        self.gfile = gfile
        self.store = Gio.ListStore.new(Gio.FileInfo)
        self.stamp = None
        self.timer = 0
        self.monitor = watch_folder(gfile)
        if self.monitor is not None:
            self.monitor.connect("changed", self._on_changed)
        self.refresh()

    def _on_changed(self, *_args):
        if not self.timer:
            self.timer = GLib.timeout_add(TREE_REFRESH_MS, self._on_timer)

    def _on_timer(self):
        self.timer = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def refresh(self):
        """Re-read the folder. False when it cannot be read: the entries stay
        as they are and the next poll reads again."""
        stamp = modified_stamp(self.gfile)  # before reading: never ahead
        entries = visible_entries(self.gfile)
        if entries is None:
            self.stamp = None  # equal to no stamp of a folder
            return False
        self.stamp = stamp
        store = self.store
        index = 0
        while index < store.get_n_items():
            old = store.get_item(index)
            new = entries.get(old.get_name())
            if new is None or new.get_file_type() != old.get_file_type():
                store.remove(index)
            else:
                del entries[old.get_name()]
                index += 1
        for info in entries.values():
            store.append(info)
        return True

    def poll(self):
        """Refresh when the folder changed; empty while the folder is gone."""
        stamp = modified_stamp(self.gfile)
        if stamp is None:
            self.stamp = None
            self.store.remove_all()
        elif stamp != self.stamp:
            self.refresh()

    def close(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.monitor is not None:
            self.monitor.cancel()
            self.monitor = None
