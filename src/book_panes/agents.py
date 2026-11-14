"""Ending the agent of a terminal pane, and nothing but it.

The terminal starts the agent as the leader of a session and process group of
its own; what the agent starts stays in that group unless it takes pains to
leave. Once the agent's exit is collected its pid, which is the group's
number too, may become another's. So every signal goes through a pidfd, which
keeps meaning this process and this group whatever happens to the number.
"""

import errno
import os
import signal
import time

PIDFD_SIGNAL_PROCESS_GROUP = 4  # <linux/pidfd.h> of Linux 6.9
KILL_AFTER_SECONDS = 5.0  # hung up and still there: killed


class Agent:
    def __init__(self, pid):
        """Of a child whose exit is not collected yet: its pid is ours."""
        self.pid = pid
        self.fd: int | None = os.pidfd_open(pid)
        self.deadline = 0.0  # time.monotonic() to kill at, once hung up
        if self.collected():
            self.close()  # not a child of ours, never to be signalled

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def collected(self):
        """True when the exit was collected, by the terminal as a rule."""
        if self.fd is None:
            return True
        try:
            flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
            os.waitid(os.P_PIDFD, self.fd, flags)
        except ChildProcessError:
            return True
        return False

    def collect(self):
        """Collect the exit, if there is one, where no main loop does it."""
        if self.fd is not None:
            try:
                os.waitid(os.P_PIDFD, self.fd, os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                pass

    def signal(self, signum):
        """Signal the agent and what it started. False when none is left."""
        if self.fd is None:
            return False
        try:
            signal.pidfd_send_signal(self.fd, signum, None, PIDFD_SIGNAL_PROCESS_GROUP)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # there, though no longer ours to signal
        except OSError as error:
            if error.errno != errno.EINVAL:
                raise
            return self._signal_by_number(signum)
        return True

    def _signal_by_number(self, signum):
        """For kernels that take no group through a pidfd. The number is ours
        until the agent's exit is collected; what is left after that is not
        signalled."""
        if self.collected():
            return False
        try:
            os.killpg(self.pid, signum)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True


def refuse(pid):
    """Kill a child just started that cannot be held as an Agent. By number,
    which is its own for as long as its exit is not collected."""
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        os.killpg(pid, signal.SIGKILL)
    except (ChildProcessError, ProcessLookupError, PermissionError):
        pass


ENDING: set[Agent] = set()  # hung up, to be killed when their time is up


def end(agent):
    """Hang up the agent and what it started, and take the agent over: it is
    closed here. False when none was left; else kill_due() has to follow."""
    if not agent.signal(signal.SIGHUP):
        ENDING.discard(agent)
        agent.close()
        return False
    if agent not in ENDING:  # the first hangup counts
        agent.deadline = time.monotonic() + KILL_AFTER_SECONDS
    ENDING.add(agent)
    return True


def kill_due(agent):
    """The time of an agent hung up is over: kill what is left of it."""
    if agent in ENDING:
        ENDING.discard(agent)
        agent.signal(signal.SIGKILL)
        agent.close()


def wait_for_ending():
    """Give the agents hung up their time and kill what stays; for when no
    main loop is left to do it."""
    while ENDING:
        now = time.monotonic()
        for agent in list(ENDING):
            agent.collect()
            if not agent.signal(0):
                ENDING.discard(agent)
                agent.close()
            elif now >= agent.deadline:
                kill_due(agent)
        if ENDING:
            time.sleep(0.05)
