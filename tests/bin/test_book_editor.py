"""book-editor: which folder the editor is started on."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
LAUNCHER = os.path.join(ROOT, "src", "bin", "book-editor")
MAIN = """import json, os, sys
names = ("BOOK_ROOT", "BOOK_CLAUDE", "PYTHONPATH")
print(json.dumps({"cwd": os.getcwd(), "args": sys.argv[1:],
                  **{name: os.environ.get(name) for name in names}}))
"""
CHOOSE = """import os, sys
if not os.environ.get("CHOSEN"):
    sys.exit(1)
print(os.environ["CHOSEN"])
"""


class LauncherTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
        scratch = tempfile.mkdtemp(prefix="la.", dir=os.path.join(ROOT, "tmp"))
        self.scratch = os.path.realpath(scratch)
        self.addCleanup(shutil.rmtree, self.scratch, True)
        # an install with nothing in it but what the launcher looks for
        self.app = os.path.join(self.scratch, "app")
        os.makedirs(os.path.join(self.app, "bin"))
        os.makedirs(os.path.join(self.app, "usr", "lib", "x86_64-linux-gnu"))
        os.makedirs(os.path.join(self.app, "book_panes"))
        shutil.copy(LAUNCHER, os.path.join(self.app, "bin", "book-editor"))
        self.write(os.path.join(self.app, "book_panes", "__init__.py"), "")
        self.write(os.path.join(self.app, "book_panes", "choose.py"), CHOOSE)
        self.main = os.path.join(self.scratch, "main.py")
        self.write(self.main, MAIN)
        self.book = os.path.join(self.scratch, "book")
        self.other = os.path.join(self.scratch, "other")
        self.start = os.path.join(self.scratch, "start")
        for folder in (self.book, self.other, self.start):
            os.makedirs(folder)
        self.write(os.path.join(self.book, "one.md"), "# One\n")
        self.command = os.path.join(self.app, "bin", "book-editor")

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as target:
            target.write(text)

    def launch(self, *arguments, **changes):
        """Run the launcher from the start folder; what the editor would
        have been started with, None when it was not started."""
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": os.path.join(self.scratch, "home"),
            "XDG_STATE_HOME": os.path.join(self.scratch, "state"),
            "XDG_CONFIG_HOME": os.path.join(self.scratch, "config"),
            "BOOK_EDITOR_MAIN": self.main,
        }
        environ.update(changes)
        done = subprocess.run(
            [self.command, *arguments],
            cwd=self.start,
            env=environ,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.status = done.returncode
        return json.loads(done.stdout) if done.stdout.strip() else None

    def remember(self, folder):
        last = os.path.join(self.scratch, "state", "book-editor", "last-folder")
        self.write(last, folder + "\n")

    def test_a_folder_named_is_the_working_folder(self):
        started = self.launch(self.book)
        self.assertEqual(started["BOOK_ROOT"], self.book)
        self.assertEqual(started["args"], [])
        self.assertEqual(started["cwd"], self.start)  # the editor stays put

    def test_a_folder_named_from_where_the_launcher_was_started(self):
        started = self.launch("../book")
        self.assertEqual(started["BOOK_ROOT"], self.book)

    def test_a_file_named_brings_its_folder(self):
        started = self.launch("../book/one.md")
        self.assertEqual(started["BOOK_ROOT"], self.book)
        self.assertEqual(started["args"], ["../book/one.md"])

    def test_files_after_the_folder_are_handed_on(self):
        started = self.launch(self.book, "notes.md", "new.md")
        self.assertEqual(started["args"], ["notes.md", "new.md"])

    def test_the_folder_named_wins_over_the_variable(self):
        started = self.launch(self.book, BOOK_ROOT=self.other)
        self.assertEqual(started["BOOK_ROOT"], self.book)

    def test_the_variable_names_the_folder_when_no_argument_does(self):
        self.remember(self.book)
        started = self.launch(BOOK_ROOT=self.other)
        self.assertEqual(started["BOOK_ROOT"], self.other)

    def test_without_a_folder_the_one_used_last_opens(self):
        self.remember(self.book)
        started = self.launch()
        self.assertEqual(started["BOOK_ROOT"], self.book)

    def test_a_last_folder_that_is_gone_makes_the_launcher_ask(self):
        self.remember(os.path.join(self.scratch, "gone"))
        started = self.launch(CHOSEN=self.other)
        self.assertEqual(started["BOOK_ROOT"], self.other)

    def test_the_first_start_asks_for_the_folder(self):
        started = self.launch(CHOSEN=self.book)
        self.assertEqual(started["BOOK_ROOT"], self.book)

    def test_a_dismissed_dialog_starts_nothing(self):
        self.assertIsNone(self.launch())
        self.assertEqual(self.status, 0)

    def test_the_machines_settings_reach_the_editor(self):
        settings = os.path.join(self.scratch, "config", "book-editor", "environment")
        self.write(settings, "# engines\nBOOK_CLAUDE=/opt/claude/bin/claude\n")
        started = self.launch(self.book)
        self.assertEqual(started["BOOK_CLAUDE"], "/opt/claude/bin/claude")

    def test_the_editor_finds_its_own_modules(self):
        started = self.launch(self.book)
        self.assertEqual(started["PYTHONPATH"].split(os.pathsep)[0], self.app)

    def test_a_link_to_the_launcher_finds_the_install(self):
        self.command = os.path.join(self.scratch, "book-editor")
        os.symlink(os.path.join(self.app, "bin", "book-editor"), self.command)
        started = self.launch(self.book)
        self.assertEqual(started["PYTHONPATH"].split(os.pathsep)[0], self.app)

    def test_an_install_without_apostrophe_is_reported(self):
        shutil.rmtree(os.path.join(self.app, "usr"))
        self.assertIsNone(self.launch(self.book))
        self.assertEqual(self.status, 1)


if __name__ == "__main__":
    unittest.main()
