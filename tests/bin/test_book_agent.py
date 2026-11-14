"""book-agent: the program between the terminal pane and the engine."""

import os
import signal
import subprocess
import sys
import time
import uuid

from book_panes import engines

from ..book_panes.test_engines import EngineCase, read

BOOK_AGENT = os.path.join(engines.TOOLS_DIR, "book-agent")


class ProgramTest(EngineCase):
    def test_the_program_runs_the_engine_on_the_folders_conversation(self):
        environ = dict(os.environ, BOOK_ENGINE="claude", FAKE_STATUS="4")
        done = subprocess.run(
            [sys.executable, BOOK_AGENT, "--model", "opus"],
            env=environ,
            timeout=30,
            check=False,
        )
        self.assertEqual(done.returncode, 4)
        option, _prompt, report, _hook, started, session, *rest = read(
            self.out
        ).splitlines()
        self.assertEqual(
            (option, report, started),
            ("--append-system-prompt", "--settings", "--session-id"),
        )
        self.assertEqual(str(uuid.UUID(session)), session)
        self.assertEqual(rest, ["--model", "opus"])
        self.assertTrue(os.path.isfile(self.kept("claude", session + ".jsonl")))


class SignalTest(EngineCase):
    def launch(self, **changes):
        environ = dict(os.environ, BOOK_ENGINE="claude", **changes)
        agent = subprocess.Popen([sys.executable, BOOK_AGENT], env=environ)
        self.addCleanup(agent.wait)
        self.addCleanup(agent.kill)
        patience = time.monotonic() + 10
        while not os.path.exists(self.out) and time.monotonic() < patience:
            time.sleep(0.02)
        time.sleep(0.2)  # the engine has written its transcript by now
        return agent

    def test_a_hangup_ends_the_engine_and_the_transcript_still_moves_in(self):
        agent = self.launch(FAKE_SLEEP="30")
        agent.send_signal(signal.SIGHUP)
        self.assertEqual(agent.wait(10), 128 + signal.SIGHUP)
        session = read(self.kept("claude", "session")).strip()
        self.assertEqual(read(self.kept("claude", session + ".jsonl")), "first\n")

    def test_an_interrupt_is_the_engines_business(self):
        agent = self.launch(FAKE_SLEEP="30")
        agent.send_signal(signal.SIGINT)
        time.sleep(0.3)
        self.assertIsNone(agent.poll())
        agent.send_signal(signal.SIGTERM)
        self.assertEqual(agent.wait(10), 128 + signal.SIGTERM)


if __name__ == "__main__":
    import unittest

    unittest.main()
