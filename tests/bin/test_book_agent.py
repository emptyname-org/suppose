"""book-agent: which conversation the pane's Claude continues."""

import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
BOOK_AGENT = os.path.join(ROOT, "src", "bin", "book-agent")
FAKE_CLAUDE = """#!/bin/sh
printf '%s\\n' "$$" "$@"
"""


class BookAgentTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
        self.scratch = tempfile.mkdtemp(prefix="agent.", dir=os.path.join(ROOT, "tmp"))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.book = os.path.join(self.scratch, "book")
        self.config = os.path.join(self.scratch, "claude")
        self.state = os.path.join(self.scratch, "state")
        os.makedirs(self.book)
        self.claude = os.path.join(self.scratch, "claude-fake")
        with open(self.claude, "w") as target:
            target.write(FAKE_CLAUDE)
        os.chmod(self.claude, 0o755)

    def start(self, *arguments, folder=None, **changes):
        """Run book-agent; returns (pid of claude, its arguments after the
        system prompt)."""
        environ = dict(
            os.environ,
            BOOK_CLAUDE=self.claude,
            CLAUDE_CONFIG_DIR=self.config,
            XDG_STATE_HOME=self.state,
        )
        environ.update(changes)
        done = subprocess.run(
            [sys.executable, BOOK_AGENT, *arguments],
            cwd=folder or self.book,
            env=environ,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        pid, option, prompt, *rest = done.stdout.splitlines()
        self.assertEqual(option, "--append-system-prompt")
        self.assertIn("book-view", prompt)
        return int(pid), rest

    def save_transcript(self, session):
        folder = os.path.join(self.config, "projects", "-some-book")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, session + ".jsonl"), "w") as target:
            target.write("{}\n")

    def test_first_start_opens_a_conversation_with_a_new_id(self):
        _pid, arguments = self.start()
        self.assertEqual(arguments[0], "--session-id")
        uuid.UUID(arguments[1])

    def test_conversation_with_a_transcript_is_resumed(self):
        _pid, first = self.start()
        self.save_transcript(first[1])
        self.assertEqual(self.start()[1], ["--resume", first[1]])
        self.assertEqual(
            self.start("--model", "x")[1], ["--resume", first[1], "--model", "x"]
        )

    def test_unused_id_is_replaced_not_reused(self):
        _pid, first = self.start()
        _pid, second = self.start()
        self.assertEqual(second[0], "--session-id")
        self.assertNotEqual(first[1], second[1])

    def test_each_folder_has_its_own_conversation(self):
        other = os.path.join(self.scratch, "other")
        os.makedirs(other)
        _pid, first = self.start()
        self.save_transcript(first[1])
        _pid, elsewhere = self.start(folder=other)
        self.assertEqual(elsewhere[0], "--session-id")
        self.assertNotEqual(elsewhere[1], first[1])

    def test_second_pane_on_a_folder_gets_a_separate_conversation(self):
        _pid, first = self.start()
        self.save_transcript(first[1])
        (folder,) = os.listdir(os.path.join(self.state, "book-editor", "sessions"))
        owner = os.path.join(self.state, "book-editor", "sessions", folder, "pid")
        with open(owner, "w") as target:
            target.write(f"{os.getpid()}\n")  # a running agent holds it
        self.assertEqual(self.start()[1], [])
        with open(owner, "w") as target:
            target.write("2147483646\n")  # that agent is gone
        self.assertEqual(self.start()[1], ["--resume", first[1]])

    def test_claude_keeps_the_claim_made_before_exec(self):
        pid, _arguments = self.start()
        (folder,) = os.listdir(os.path.join(self.state, "book-editor", "sessions"))
        owner = os.path.join(self.state, "book-editor", "sessions", folder, "pid")
        with open(owner) as source:
            self.assertEqual(int(source.read()), pid)

    def test_locked_state_gives_a_separate_conversation_without_hanging(self):
        _pid, first = self.start()
        self.save_transcript(first[1])
        (folder,) = os.listdir(os.path.join(self.state, "book-editor", "sessions"))
        lock = os.path.join(self.state, "book-editor", "sessions", folder, "lock")
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)  # a holder that never lets go
            start = time.monotonic()
            self.assertEqual(self.start()[1], [])
            self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(self.start()[1], ["--resume", first[1]])

    def test_damaged_state_starts_a_new_conversation(self):
        self.start()
        (folder,) = os.listdir(os.path.join(self.state, "book-editor", "sessions"))
        state = os.path.join(self.state, "book-editor", "sessions", folder)
        for name in ("session", "pid"):
            with open(os.path.join(state, name), "w") as target:
                target.write("garbage\n")
        self.assertEqual(self.start()[1][0], "--session-id")

    def test_without_writable_state_claude_still_starts(self):
        blocked = os.path.join(self.scratch, "blocked")
        with open(blocked, "w") as target:
            target.write("a file where the state folder should be\n")
        self.assertEqual(self.start(XDG_STATE_HOME=blocked)[1], [])

    def test_missing_claude_is_reported(self):
        done = subprocess.run(
            [sys.executable, BOOK_AGENT],
            cwd=self.book,
            env=dict(
                os.environ, BOOK_CLAUDE="/no/such/claude", XDG_STATE_HOME=self.state
            ),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(done.returncode, 127)
        self.assertIn("cannot run /no/such/claude", done.stderr)
        self.assertIn("BOOK_AGENT_CMD", done.stderr)


if __name__ == "__main__":
    unittest.main()
