"""Folder listings behind the tree pane."""

import functools
import os
import shutil
import tempfile
import unittest
from unittest import mock

from gi.repository import Gio

from book_panes import folders

from .test_channel import TMP


def names(listing):
    store = listing.store
    return sorted(
        store.get_item(index).get_name() for index in range(store.get_n_items())
    )


class FolderListingTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP, exist_ok=True)
        self.folder = tempfile.mkdtemp(prefix="fo.", dir=TMP)
        self.addCleanup(shutil.rmtree, self.folder, True)
        for name in ("b.md", ".hidden", "A.md"):
            self.touch(name)
        os.mkdir(os.path.join(self.folder, "part"))
        os.mkdir(os.path.join(self.folder, "node_modules"))
        self.listing = folders.FolderListing(Gio.File.new_for_path(self.folder))
        self.addCleanup(self.listing.close)

    def touch(self, name):
        with open(os.path.join(self.folder, name), "w") as target:
            target.write("x\n")

    def change(self):
        """Make sure the folder's modification time moves on."""
        stamp = os.stat(self.folder).st_mtime_ns + 1_000_000_000
        os.utime(self.folder, ns=(stamp, stamp))

    def test_hidden_and_skipped_entries_are_left_out(self):
        self.assertEqual(names(self.listing), ["A.md", "b.md", "part"])

    def test_folders_sort_first_then_names_without_case(self):
        store = self.listing.store
        entries = [store.get_item(index) for index in range(store.get_n_items())]
        entries.sort(key=functools.cmp_to_key(folders.compare_entries))
        self.assertEqual(
            [info.get_name() for info in entries], ["part", "A.md", "b.md"]
        )

    def test_poll_follows_added_and_removed_entries_and_keeps_the_rest(self):
        kept = self.listing.store.get_item(0)
        os.remove(
            os.path.join(self.folder, "b.md" if kept.get_name() != "b.md" else "A.md")
        )
        self.touch("c.md")
        self.change()
        self.listing.poll()
        self.assertIn("c.md", names(self.listing))
        self.assertEqual(len(names(self.listing)), 3)
        self.assertIs(self.listing.store.get_item(0), kept)  # the row survives

    def test_entry_that_changed_kind_is_replaced(self):
        os.remove(os.path.join(self.folder, "b.md"))
        os.mkdir(os.path.join(self.folder, "b.md"))
        self.change()
        self.listing.poll()
        store = self.listing.store
        kinds = {
            store.get_item(index).get_name(): folders.is_directory(
                store.get_item(index)
            )
            for index in range(store.get_n_items())
        }
        self.assertEqual(kinds, {"A.md": False, "b.md": True, "part": True})

    def test_unreadable_folder_keeps_its_entries_and_is_read_again(self):
        self.touch("c.md")
        with mock.patch.object(folders, "visible_entries", return_value=None):
            self.assertFalse(self.listing.refresh())
            self.listing.poll()  # still failing
        self.assertEqual(names(self.listing), ["A.md", "b.md", "part"])
        self.listing.poll()  # readable again, although the folder did not change
        self.assertEqual(names(self.listing), ["A.md", "b.md", "c.md", "part"])

    def test_folder_that_is_gone_empties_and_comes_back(self):
        shutil.rmtree(self.folder)
        self.listing.poll()
        self.assertEqual(names(self.listing), [])
        os.mkdir(self.folder)
        self.touch("new.md")
        self.listing.poll()
        self.assertEqual(names(self.listing), ["new.md"])

    def test_without_a_change_monitor_polling_still_works(self):
        with mock.patch.object(folders, "watch_folder", return_value=None):
            listing = folders.FolderListing(Gio.File.new_for_path(self.folder))
        self.assertIsNone(listing.monitor)
        self.touch("c.md")
        self.change()
        listing.poll()
        self.assertIn("c.md", names(listing))
        listing.close()


if __name__ == "__main__":
    unittest.main()
