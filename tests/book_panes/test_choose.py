"""The dialog that asks for the working folder."""

import contextlib
import io
import unittest
from unittest import mock

from gi.repository import Gio, GLib

from book_panes import choose


class Loop:
    def __init__(self):
        self.stopped = False

    def run(self):
        pass  # the dialog below answers at once

    def quit(self):
        self.stopped = True


class Dialog:
    """Answers at once: with the folder given, or as a dismissed dialog."""

    def __init__(self, folder=None):
        self.folder = folder

    def select_folder(self, parent, cancellable, done):
        self.asked = (parent, cancellable)
        done(self, "result")

    def select_folder_finish(self, result):
        if self.folder is None:
            raise GLib.GError("dismissed")
        return Gio.File.new_for_path(self.folder)


class ChooseTest(unittest.TestCase):
    def test_the_folder_chosen_is_returned(self):
        loop, dialog = Loop(), Dialog("/books/one")
        self.assertEqual(choose.choose(dialog, loop), "/books/one")
        self.assertTrue(loop.stopped)
        self.assertEqual(dialog.asked, (None, None))

    def test_a_dismissed_dialog_gives_nothing(self):
        loop = Loop()
        self.assertIsNone(choose.choose(Dialog(), loop))
        self.assertTrue(loop.stopped)

    def run_main(self, dialog):
        printed = io.StringIO()
        with (
            mock.patch.object(choose.Gtk, "init"),
            mock.patch.object(choose.Gtk, "FileDialog", return_value=dialog),
            mock.patch.object(choose.GLib, "MainLoop", Loop),
            mock.patch.object(choose.GLib, "set_prgname"),
            contextlib.redirect_stdout(printed),
        ):
            return choose.main(), printed.getvalue()

    def test_the_program_prints_the_folder(self):
        self.assertEqual(self.run_main(Dialog("/books/one")), (0, "/books/one\n"))

    def test_the_program_fails_without_a_folder(self):
        self.assertEqual(self.run_main(Dialog()), (1, ""))


if __name__ == "__main__":
    unittest.main()
