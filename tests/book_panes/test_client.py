"""book-view and book-edit against a channel with a scripted editor."""

import contextlib
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock

from book_panes import channel, client

from .test_channel import TMP, run_with_loop

VIEW = {
    "file": "/books/novel/part/ch2.md",
    "relative": "part/ch2.md",
    "unsaved": True,
    "conflict": False,
    "lines": 3,
    "characters": 24,
    "cursor": {"line": 2, "column": 1},
    "selection": {
        "start": {"line": 1, "column": 3},
        "end": {"line": 1, "column": 6},
        "text": "Two",
    },
    "text": "# Two\n\nSecond chäpter.\n",
}


class ClientCase(unittest.TestCase):
    def setUp(self):
        os.makedirs(TMP, exist_ok=True)
        base = tempfile.mkdtemp(prefix="cl.", dir=TMP)
        self.addCleanup(shutil.rmtree, base, True)
        self.requests = []
        self.reply = dict(VIEW)
        self.refusal = None
        self.channel = channel.Channel(self.handle, base=base)
        self.addCleanup(self.channel.close)

    def handle(self, request):
        self.requests.append(request)
        if self.refusal:
            raise channel.Refused(self.refusal)
        return self.reply

    def run_tool(self, tool, *argv, stdin=""):
        """Returns (exit status, standard output, standard error)."""
        out, err = io.StringIO(), io.StringIO()
        environ = {client.SOCKET_ENV: self.channel.path}

        def call():
            with (
                mock.patch.dict(os.environ, environ),
                mock.patch("sys.stdin", io.StringIO(stdin)),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                return client.main(tool, list(argv))

        return run_with_loop(call), out.getvalue(), err.getvalue()


class ViewTest(ClientCase):
    def test_prints_the_text_exactly(self):
        self.assertEqual(self.run_tool("view"), (0, VIEW["text"], ""))
        self.assertEqual(self.requests, [{"op": "view", "text": True}])

    def test_status_names_file_state_cursor_and_selection(self):
        status, out, _err = self.run_tool("view", "--status")
        self.assertEqual(status, 0)
        self.assertEqual(
            out,
            "file: part/ch2.md\n"
            "state: unsaved edits\n"
            "size: 3 lines, 24 characters\n"
            "cursor: line 2, column 1\n"
            "selection: line 1, column 3 to line 1, column 6\n",
        )
        self.assertEqual(self.requests, [{"op": "view", "text": False}])

    def test_status_of_a_saved_text_without_selection(self):
        self.reply.update(unsaved=False, selection=None, relative=None)
        _status, out, _err = self.run_tool("view", "--status")
        self.assertIn("file: /books/novel/part/ch2.md\nstate: saved\n", out)
        self.assertIn("selection: none\n", out)

    def test_selection_prints_the_passage_or_fails_without_one(self):
        self.assertEqual(self.run_tool("view", "--selection"), (0, "Two", ""))
        self.reply["selection"] = None
        self.assertEqual(
            self.run_tool("view", "--selection"),
            (1, "", "book-view: nothing is selected\n"),
        )

    def test_json_prints_the_whole_reply(self):
        _status, out, _err = self.run_tool("view", "--json")
        self.assertEqual(json.loads(out), dict(VIEW, ok=True))


class EditTest(ClientCase):
    def setUp(self):
        super().setUp()
        self.reply = {"file": VIEW["file"], "relative": VIEW["relative"], "line": 7}

    def test_replace_sends_old_and_new(self):
        result = self.run_tool("edit", "replace", "old text", "new text")
        self.assertEqual(result, (0, "edited part/ch2.md at line 7\n", ""))
        self.assertEqual(
            self.requests,
            [{"op": "edit", "mode": "replace", "old": "old text", "new": "new text"}],
        )

    def test_new_text_comes_from_standard_input_minus_one_newline(self):
        self.run_tool("edit", "insert", stdin="line one\nline two\n\n")
        self.run_tool("edit", "selection", stdin="")
        self.run_tool("edit", "replace", "old", stdin="new")
        self.assertEqual(
            [request["new"] for request in self.requests],
            ["line one\nline two\n", "", "new"],
        )
        self.assertEqual(
            [request["mode"] for request in self.requests],
            ["insert", "selection", "replace"],
        )

    def test_file_guard_is_sent_as_an_absolute_path(self):
        self.run_tool("edit", "--file", "part/ch2.md", "insert", "x")
        self.assertEqual(self.requests[0]["file"], os.path.abspath("part/ch2.md"))

    def test_refusal_is_reported_with_exit_status_one(self):
        self.refusal = "old text not found in the editor"
        self.assertEqual(
            self.run_tool("edit", "replace", "a", "b"),
            (1, "", "book-edit: old text not found in the editor\n"),
        )


class BrokenEditorTest(unittest.TestCase):
    """The tools against an endpoint that does not answer as the editor does."""

    def setUp(self):
        os.makedirs(TMP, exist_ok=True)
        base = tempfile.mkdtemp(prefix="br.", dir=TMP)
        self.addCleanup(shutil.rmtree, base, True)
        self.path = os.path.join(base, "socket")
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.listener.close)
        self.listener.bind(self.path)
        self.listener.listen(1)
        self.stop = threading.Event()
        self.addCleanup(self.stop.set)

    def serve(self, behave):
        def work():
            connection, _address = self.listener.accept()
            with connection:
                connection.recv(65536)
                try:
                    behave(connection)
                except OSError:
                    pass  # the client gave up

        threading.Thread(target=work, daemon=True).start()

    def trickle(self, connection):
        while not self.stop.wait(0.05):
            connection.sendall(b"x")

    def test_answer_that_trickles_forever_ends_at_the_deadline(self):
        self.serve(self.trickle)
        start = time.monotonic()
        with self.assertRaises(client.Unanswered):
            client.ask({"op": "view"}, self.path, timeout=0.4)
        self.assertLess(time.monotonic() - start, 2)

    def test_silent_editor_ends_at_the_deadline(self):
        self.serve(lambda connection: self.stop.wait(5))
        with self.assertRaises(client.Unanswered):
            client.ask({"op": "view"}, self.path, timeout=0.3)

    def flood(self, connection):
        while not self.stop.is_set():
            connection.sendall(b"x" * 65536)

    def test_answer_that_never_pauses_still_ends_at_the_deadline(self):
        self.serve(self.flood)
        start = time.monotonic()
        with (
            mock.patch.object(client, "MAX_REPLY_BYTES", 1 << 40),
            self.assertRaisesRegex(client.Unanswered, "timed out"),
        ):
            client.ask({"op": "view"}, self.path, timeout=0.4)
        self.assertLess(time.monotonic() - start, 2)

    def test_answer_beyond_the_size_limit_is_refused_even_when_whole(self):
        whole = json.dumps({"ok": True, "text": "x" * 5000}).encode() + b"\n"
        for answer in (whole, b"x" * 100000):
            self.serve(lambda connection, answer=answer: connection.sendall(answer))
            with (
                mock.patch.object(client, "MAX_REPLY_BYTES", 1000),
                self.assertRaisesRegex(client.Unanswered, "too long"),
            ):
                client.ask({"op": "view"}, self.path, timeout=5)

    def test_answers_that_are_not_replies_count_as_none(self):
        for answer in (b"", b"[]\n", b"\xff\n", b'{"ok": tr'):
            self.serve(lambda connection, answer=answer: connection.sendall(answer))
            with self.assertRaises(client.Unanswered):
                client.ask({"op": "view"}, self.path, timeout=5)

    def run_tool(self, tool, *argv):
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, {client.SOCKET_ENV: self.path}),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return client.main(tool, list(argv)), err.getvalue()

    def test_edit_without_answer_says_its_outcome_is_unknown(self):
        self.serve(lambda connection: None)
        status, err = self.run_tool("edit", "insert", "x")
        self.assertEqual(status, client.UNANSWERED)
        self.assertIn("The edit may have been made; check with book-view.", err)

    def test_view_without_answer_has_the_same_status_and_no_edit_hint(self):
        self.serve(lambda connection: None)
        status, err = self.run_tool("view")
        self.assertEqual(status, client.UNANSWERED)
        self.assertNotIn("The edit may", err)


class ConnectionTest(unittest.TestCase):
    def test_outside_the_editor_the_tools_say_so(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ), contextlib.redirect_stderr(err):
            os.environ.pop(client.SOCKET_ENV, None)
            self.assertEqual(client.main("view", []), 1)
        self.assertIn("not in the book editor's terminal", err.getvalue())

    def test_a_vanished_editor_is_reported(self):
        gone = os.path.join(TMP, "no-such-socket")
        with self.assertRaisesRegex(
            client.ChannelError, "cannot reach the editor"
        ) as failed:
            client.ask({"op": "view"}, gone)
        self.assertNotIsInstance(
            failed.exception, client.Unanswered
        )  # nothing was sent

    def test_usage_errors_exit_with_status_two(self):
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as stopped,
        ):
            client.main("edit", ["replace"])
        self.assertEqual(stopped.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
