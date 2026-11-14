"""The editor channel's socket server and edit planning."""

import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest

from gi.repository import GLib

from book_panes import channel, client

# relative, so that socket paths stay short wherever the checkout lives
TMP = os.path.relpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tmp")
)


def wait_until(condition, seconds=5.0):
    """Wait, off the main loop, for something the main loop will do."""
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > end:
            raise AssertionError(f"condition not met in {seconds:g} s")
        time.sleep(0.01)


def run_with_loop(call, seconds=10):
    """Run call() in a thread while the main loop serves; return its result
    or raise its exception."""
    loop = GLib.MainLoop()
    outcome = {}

    def work():
        try:
            outcome["result"] = call()
        except Exception as error:  # noqa: BLE001  raised again in the caller
            outcome["error"] = error
        GLib.idle_add(loop.quit)

    thread = threading.Thread(target=work, daemon=True)
    guard = GLib.timeout_add_seconds(seconds, loop.quit)
    thread.start()
    loop.run()
    GLib.source_remove(guard)
    thread.join(1)
    if "error" in outcome:
        raise outcome["error"]
    if "result" not in outcome:
        raise AssertionError(f"no result within {seconds} s")
    return outcome["result"]


def raw_exchange(path, payload, wait=5.0, end=False):
    """Send bytes (end: then end the sending side), return everything
    received until the server closes."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(wait)
        connection.connect(path)
        try:
            connection.sendall(payload)
            if end:
                connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass  # closed on us while sending
        received = b""
        try:
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                received += chunk
        except ConnectionResetError:
            pass
        return received


class ChannelCase(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP, exist_ok=True)
        self.base = tempfile.mkdtemp(prefix="ch.", dir=TMP)
        self.addCleanup(shutil.rmtree, self.base, True)
        self.requests = []
        self.channel = None

    def open(self, handler=None, **options):
        def echo(request):
            self.requests.append(request)
            return {"echo": request}

        self.channel = channel.Channel(handler or echo, base=self.base, **options)
        self.addCleanup(self.channel.close)
        return self.channel


class ServingTest(ChannelCase):
    def test_request_is_answered_by_the_handler(self):
        path = self.open().path
        reply = run_with_loop(lambda: client.ask({"op": "x", "text": "é\n"}, path))
        self.assertEqual(reply, {"ok": True, "echo": {"op": "x", "text": "é\n"}})

    def test_refusal_reaches_the_client_as_an_error(self):
        def refuse(_request):
            raise channel.Refused("not now")

        path = self.open(refuse).path
        with self.assertRaisesRegex(client.ChannelError, "^not now$"):
            run_with_loop(lambda: client.ask({}, path))

    def test_handler_failure_is_answered_not_left_hanging(self):
        def fail(_request):
            raise RuntimeError("boom")

        path = self.open(fail).path
        with self.assertLogs(channel.LOGGER, "ERROR"):
            reply = run_with_loop(lambda: raw_exchange(path, b"{}\n"))
        self.assertEqual(
            json.loads(reply), {"ok": False, "error": "internal error in the editor"}
        )

    def test_malformed_requests_are_answered_as_bad(self):
        path = self.open().path
        for payload in (b"not json\n", b"[1]\n", b"\xff\xfe\n", b"\n"):
            reply = run_with_loop(lambda payload=payload: raw_exchange(path, payload))
            self.assertEqual(json.loads(reply), {"ok": False, "error": "bad request"})
        self.assertEqual(self.requests, [])

    def test_only_the_first_line_of_a_connection_is_served(self):
        path = self.open().path
        reply = run_with_loop(lambda: raw_exchange(path, b'{"n":1}\n{"n":2}\n'))
        self.assertEqual(reply.count(b"\n"), 1)
        self.assertEqual(self.requests, [{"n": 1}])

    def test_large_text_passes_both_ways(self):
        text = "paragraph ő\n" * 400000  # about 5 MB
        path = self.open().path
        reply = run_with_loop(lambda: client.ask({"text": text}, path))
        self.assertEqual(reply["echo"]["text"], text)

    def test_oversized_request_is_dropped_unanswered(self):
        path = self.open().path
        payload = b"x" * (channel.MAX_REQUEST_BYTES + 2 * channel.READ_CHUNK)
        self.assertEqual(run_with_loop(lambda: raw_exchange(path, payload)), b"")
        self.assertEqual(self.requests, [])
        self.assertEqual(self.channel.exchanges, set())

    def test_connection_closed_early_is_forgotten(self):
        path = self.open().path
        for payload in (b"", b'{"op":'):
            reply = run_with_loop(
                lambda payload=payload: raw_exchange(path, payload, end=True)
            )
            self.assertEqual(reply, b"")
        self.assertTrue(run_with_loop(lambda: client.ask({"after": True}, path))["ok"])
        self.assertEqual(self.requests, [{"after": True}])
        self.assertEqual(self.channel.exchanges, set())

    def test_silent_connection_is_closed_at_the_deadline(self):
        path = self.open(deadline_seconds=0.3).path

        def idle():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(path)
                return connection.recv(1)  # returns b"" once the server closes

        self.assertEqual(run_with_loop(idle), b"")
        self.assertEqual(self.channel.exchanges, set())

    def test_connections_beyond_the_limit_are_closed(self):
        path = self.open(deadline_seconds=3).path

        def crowd():
            held = []
            try:
                for _number in range(channel.MAX_CONNECTIONS):
                    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    connection.connect(path)
                    held.append(connection)
                extra = raw_exchange(path, b"{}\n")
            finally:
                for connection in held:
                    connection.close()
            wait_until(lambda: not self.channel.exchanges)
            return extra, client.ask({"later": True}, path)

        extra, later = run_with_loop(crowd)
        self.assertEqual(extra, b"")
        self.assertTrue(later["ok"])
        self.assertEqual(self.requests, [{"later": True}])


class LifecycleTest(ChannelCase):
    def test_socket_is_private_to_the_user(self):
        opened = self.open()
        self.assertEqual(stat.S_IMODE(os.stat(opened.directory).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(opened.path).st_mode), 0o600)
        self.assertTrue(stat.S_ISSOCK(os.stat(opened.path).st_mode))

    def test_close_removes_the_socket_and_drops_connections(self):
        opened = self.open(deadline_seconds=30)

        def connect_then_close():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(opened.path)
                wait_until(lambda: opened.exchanges)
                GLib.idle_add(opened.close)
                return connection.recv(1)

        self.assertEqual(run_with_loop(connect_then_close), b"")
        self.assertEqual(os.listdir(self.base), [])
        opened.close()  # a second close is harmless
        with self.assertRaises(client.ChannelError):
            client.ask({}, opened.path)

    def test_folders_of_dead_editors_are_removed_at_start(self):
        dead = os.path.join(self.base, "book-editor-2147483646-abc")
        os.mkdir(dead)
        with socket.socket(socket.AF_UNIX) as leftover:
            leftover.bind(os.path.join(dead, "socket"))
        alive = os.path.join(self.base, f"book-editor-{os.getppid()}-abc")
        os.mkdir(alive)
        strange = os.path.join(self.base, "book-editor-2147483645-abc")
        os.mkdir(strange)
        with open(os.path.join(strange, "notes"), "w") as target:
            target.write("not ours to delete")
        opened = self.open()
        self.assertEqual(
            sorted(os.listdir(self.base)),
            sorted(map(os.path.basename, (alive, strange, opened.directory))),
        )
        self.assertEqual(os.listdir(strange), ["notes"])

    def test_failed_start_leaves_nothing_behind(self):
        deep = os.path.join(self.base, "d" * 100)
        with self.assertRaises(OSError):
            channel.Channel(lambda request: {}, base=deep)
        self.assertEqual(os.listdir(deep), [])


class PlanEditTest(unittest.TestCase):
    text = "one two\nthree two\n"

    def plan(self, request, cursor=0, selection=None):
        return channel.plan_edit(self.text, cursor, selection, request)

    def test_replace_finds_the_single_occurrence(self):
        request = {"mode": "replace", "old": "three", "new": "3"}
        self.assertEqual(self.plan(request), (8, 13, "3"))

    def test_replace_without_new_deletes(self):
        self.assertEqual(self.plan({"mode": "replace", "old": "one "}), (0, 4, ""))

    def test_replace_refuses_missing_and_repeated_text(self):
        with self.assertRaisesRegex(channel.Refused, "not found"):
            self.plan({"mode": "replace", "old": "four"})
        with self.assertRaisesRegex(channel.Refused, "more than once"):
            self.plan({"mode": "replace", "old": "two"})
        with self.assertRaisesRegex(channel.Refused, "more than once"):
            channel.plan_edit("aaa", 0, None, {"mode": "replace", "old": "aa"})
        with self.assertRaisesRegex(channel.Refused, "empty"):
            self.plan({"mode": "replace", "old": ""})

    def test_selection_and_insert_use_the_editor_state(self):
        request = {"mode": "selection", "new": "x"}
        self.assertEqual(self.plan(request, selection=(4, 7)), (4, 7, "x"))
        with self.assertRaisesRegex(channel.Refused, "nothing is selected"):
            self.plan(request)
        self.assertEqual(
            self.plan({"mode": "insert", "new": "x"}, cursor=5), (5, 5, "x")
        )

    def test_text_the_buffer_cannot_hold_is_refused(self):
        for bad in (5, None, "a\0b", "\ud800"):
            with self.assertRaises(channel.Refused):
                self.plan({"mode": "insert", "new": bad})
        with self.assertRaises(channel.Refused):
            self.plan({"mode": "replace", "old": 5})
        with self.assertRaisesRegex(channel.Refused, "unknown edit mode"):
            self.plan({"mode": "append", "new": "x"})


if __name__ == "__main__":
    unittest.main()
