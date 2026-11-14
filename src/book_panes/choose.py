"""Ask for the working folder before there is a window: prints the folder
chosen, exits 1 when none was."""

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from . import APP_ID


def choose(dialog, loop):
    """Run the dialog; the path chosen, None when it was dismissed."""
    chosen = []

    def on_done(source, result):
        try:
            chosen.append(source.select_folder_finish(result).get_path())
        except GLib.GError:
            pass  # dismissed
        loop.quit()

    dialog.select_folder(None, None, on_done)
    loop.run()
    return chosen[0] if chosen else None


def main():
    GLib.set_prgname(APP_ID)
    Gtk.init()
    dialog = Gtk.FileDialog(title="Choose the working folder", modal=True)
    path = choose(dialog, GLib.MainLoop())
    if not path:
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
