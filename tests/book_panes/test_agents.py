"""Ending an agent with what it started, and nothing else."""

import errno
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from book_panes import agents

from .test_channel import TMP

HELPER = "(trap '' HUP; echo ready; exec sleep 300) & "
STUBBORN = "trap '' HUP; echo ready; exec cat"


def group_signals():
    """True where the kernel signals a process group through a pidfd."""
    fd = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(fd, 0, None, agents.PIDFD_SIGNAL_PROCESS_GROUP)
    except OSError as error:
        return error.errno != errno.EINVAL
    finally:
        os.close(fd)
    return True


def old_kernel(*_args):
    raise OSError(errno.EINVAL, "Invalid argument")


class AgentCase(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP, exist_ok=True)
        folder = tempfile.mkdtemp(prefix="ag.", dir=TMP)
        self.folder = os.path.realpath(folder)
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.addCleanup(self.sweep)
        patience = mock.patch.object(agents, "KILL_AFTER_SECONDS", 0.5)
        patience.start()
        self.addCleanup(patience.stop)

    def sweep(self):
        """Nothing a test started outlives it, whatever became of the test."""
        for agent in list(agents.ENDING):
            agent.close()
        agents.ENDING.clear()
        for pid in self.running():
            os.kill(pid, signal.SIGKILL)

    def start(self, script):
        """A child started the way the terminal starts an agent."""
        child = subprocess.Popen(
            ["/bin/sh", "-c", script],
            cwd=self.folder,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(self.finish, child)
        for _line in range(script.count("echo ready")):
            child.stdout.readline()
        return child

    def finish(self, child):
        child.kill()
        child.wait()
        child.stdin.close()
        child.stdout.close()

    def running(self):
        """Pids of the processes at work in the folder, exited ones left out."""
        found = []
        for name in os.listdir("/proc"):
            if name.isdigit():
                try:
                    if os.readlink(f"/proc/{name}/cwd") == self.folder:
                        found.append(int(name))
                except OSError:
                    pass  # gone meanwhile, exited, or not ours
        return found

    def gone(self):
        patience = time.monotonic() + 3
        while self.running() and time.monotonic() < patience:
            time.sleep(0.02)
        return not self.running()


class SignalTest(AgentCase):
    @unittest.skipUnless(group_signals(), "needs Linux 6.9")
    def test_what_the_agent_started_is_reached_after_the_agent_was_collected(self):
        child = self.start(HELPER + "exec cat")
        agent = agents.Agent(child.pid)
        self.assertTrue(agents.end(agent))
        child.wait()
        self.assertTrue(agent.collected())
        self.assertTrue(agent.signal(0))
        self.assertEqual(len(self.running()), 1)
        agents.kill_due(agent)
        self.assertTrue(self.gone())
        self.assertIsNone(agent.fd)
        self.assertNotIn(agent, agents.ENDING)

    def test_a_number_that_became_anothers_is_not_signalled(self):
        child = self.start("exec cat")
        other = self.start(STUBBORN)
        agent = agents.Agent(child.pid)
        self.assertTrue(agents.end(agent))
        child.wait()
        agent.pid = other.pid  # as if the number had been handed out again
        with mock.patch.object(os, "killpg") as killpg:
            agents.kill_due(agent)
        killpg.assert_not_called()
        time.sleep(0.2)
        self.assertIsNone(other.poll())
        self.assertEqual(self.running(), [other.pid])

    def test_a_kernel_without_group_signals_gets_the_number_while_it_is_ours(self):
        child = self.start(HELPER + STUBBORN)
        agent = agents.Agent(child.pid)
        with mock.patch.object(signal, "pidfd_send_signal", old_kernel):
            self.assertTrue(agent.signal(signal.SIGKILL))
            self.assertTrue(self.gone())
            child.wait()
            other = self.start(STUBBORN)
            agent.pid = other.pid  # as if the number had been handed out again
            self.assertFalse(agent.signal(signal.SIGKILL))
        time.sleep(0.2)
        self.assertIsNone(other.poll())

    def test_by_number_tells_gone_from_not_ours_to_signal(self):
        child = self.start(STUBBORN)
        agent = agents.Agent(child.pid)
        with mock.patch.object(signal, "pidfd_send_signal", old_kernel):
            with mock.patch.object(os, "killpg", side_effect=ProcessLookupError):
                self.assertFalse(agent.signal(0))
            with mock.patch.object(os, "killpg", side_effect=PermissionError):
                self.assertTrue(agent.signal(0))
        agent.close()

    def test_a_process_that_is_not_our_child_is_never_signalled(self):
        agent = agents.Agent(os.getppid())
        self.assertIsNone(agent.fd)
        agent.collect()
        with mock.patch.object(signal, "pidfd_send_signal") as send:
            self.assertFalse(agent.signal(signal.SIGHUP))
            self.assertFalse(agents.end(agent))
        send.assert_not_called()
        self.assertNotIn(agent, agents.ENDING)

    def test_what_is_there_but_not_ours_to_signal_counts_as_there(self):
        child = self.start(STUBBORN)
        agent = agents.Agent(child.pid)
        with mock.patch.object(
            signal, "pidfd_send_signal", side_effect=PermissionError
        ):
            self.assertTrue(agent.signal(0))
        agent.close()

    def test_an_unexpected_failure_is_not_swallowed(self):
        child = self.start(STUBBORN)
        agent = agents.Agent(child.pid)
        failure = OSError(errno.EBADF, "Bad file descriptor")
        with (
            mock.patch.object(signal, "pidfd_send_signal", side_effect=failure),
            self.assertRaises(OSError),
        ):
            agent.signal(0)
        agent.close()


class EndTest(AgentCase):
    def test_an_agent_with_nobody_left_is_closed_at_once(self):
        child = self.start("exec cat")
        agent = agents.Agent(child.pid)
        child.kill()
        child.wait()
        self.assertFalse(agents.end(agent))
        self.assertIsNone(agent.fd)
        self.assertNotIn(agent, agents.ENDING)

    def test_the_first_hangup_sets_the_time(self):
        child = self.start(STUBBORN)
        agent = agents.Agent(child.pid)
        self.assertTrue(agents.end(agent))
        deadline = agent.deadline
        time.sleep(0.05)
        self.assertTrue(agents.end(agent))
        self.assertEqual(agent.deadline, deadline)
        self.assertEqual(agents.ENDING, {agent})

    def test_the_kill_is_done_once(self):
        child = self.start(STUBBORN)
        agent = agents.Agent(child.pid)
        agents.end(agent)
        agents.kill_due(agent)
        self.assertEqual(child.wait(), -signal.SIGKILL)
        with mock.patch.object(signal, "pidfd_send_signal") as send:
            agents.kill_due(agent)
        send.assert_not_called()
        self.assertIsNone(agent.fd)

    def test_a_child_that_cannot_be_held_is_killed(self):
        child = self.start(HELPER + STUBBORN)
        agents.refuse(child.pid)
        self.assertTrue(self.gone())
        self.assertEqual(child.wait(), -signal.SIGKILL)

    def test_refusing_spares_what_is_not_our_child(self):
        with mock.patch.object(os, "killpg") as killpg:
            agents.refuse(os.getppid())
        killpg.assert_not_called()


class WaitTest(AgentCase):
    def ended(self, script):
        """Seconds the wait took for an agent running the script."""
        agent = agents.Agent(self.start(script).pid)
        agents.end(agent)
        started = time.monotonic()
        agents.wait_for_ending()
        taken = time.monotonic() - started
        self.assertIsNone(agent.fd)
        self.assertEqual(agents.ENDING, set())
        self.assertTrue(self.gone())
        return taken

    def test_an_agent_that_goes_is_not_waited_for(self):
        self.assertLess(self.ended("exec cat"), 0.4)

    def test_an_agent_ignoring_the_hangup_is_killed_when_its_time_is_up(self):
        self.assertGreaterEqual(self.ended(STUBBORN), 0.4)

    @unittest.skipUnless(group_signals(), "needs Linux 6.9")
    def test_what_an_agent_that_went_left_behind_is_killed(self):
        self.assertGreaterEqual(self.ended(HELPER + "exec cat"), 0.4)

    def test_nothing_to_wait_for_returns_at_once(self):
        started = time.monotonic()
        agents.wait_for_ending()
        self.assertLess(time.monotonic() - started, 0.04)


if __name__ == "__main__":
    unittest.main()
