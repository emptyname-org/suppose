"""The editor channel as MCP tools."""

import io
import json
import os
import unittest
from unittest import mock

from book_panes import client, mcp

VIEW = {
    "ok": True,
    "file": "/book/one.md",
    "relative": "one.md",
    "unsaved": True,
    "conflict": False,
    "lines": 2,
    "characters": 9,
    "cursor": {"line": 1, "column": 3},
    "selection": None,
    "text": "# One\nxy\n",
}


def request(method, params=None, identity=1):
    message = {"jsonrpc": "2.0", "id": identity, "method": method}
    if params is not None:
        message["params"] = params
    return message


def tool(name, arguments=None):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return mcp.answer(request("tools/call", params))


def said(reply):
    (content,) = reply["result"]["content"]
    return content["text"], reply["result"]["isError"]


class ProtocolTest(unittest.TestCase):
    def test_initialize_agrees_to_the_clients_version(self):
        reply = mcp.answer(request("initialize", {"protocolVersion": "2025-06-18"}))
        result = reply["result"]
        self.assertEqual(reply["id"], 1)
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertIn("book editor", result["instructions"])

    def test_initialize_without_a_version_names_ours(self):
        result = mcp.answer(request("initialize"))["result"]
        self.assertEqual(result["protocolVersion"], mcp.PROTOCOL)

    def test_the_tools_are_listed_with_their_arguments(self):
        tools = mcp.answer(request("tools/list"))["result"]["tools"]
        self.assertEqual([entry["name"] for entry in tools], ["view", "edit"])
        for entry in tools:
            self.assertEqual(entry["inputSchema"]["type"], "object")
            self.assertTrue(entry["description"])

    def test_ping_is_answered(self):
        self.assertEqual(mcp.answer(request("ping"))["result"], {})

    def test_a_notification_gets_no_reply(self):
        message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.assertIsNone(mcp.answer(message))

    def test_an_unknown_method_is_refused(self):
        reply = mcp.answer(request("resources/list"))
        self.assertEqual(reply["error"]["code"], -32601)

    def test_what_is_no_request_is_refused(self):
        self.assertEqual(mcp.answer([1])["error"]["code"], -32600)

    def test_an_unknown_tool_is_refused(self):
        self.assertEqual(tool("erase")["error"]["code"], -32602)
        reply = mcp.answer(request("tools/call", {"name": "view", "arguments": 1}))
        self.assertEqual(reply["error"]["code"], -32602)

    def test_messages_come_and_go_a_line_each(self):
        lines = [
            json.dumps(request("ping", identity=7)),
            "",
            "not json",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps(request("tools/list", identity="b")),
        ]
        target = io.StringIO()
        mcp.serve(io.StringIO("\n".join(lines) + "\n"), target)
        replies = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual([reply["id"] for reply in replies], [7, None, "b"])
        self.assertEqual(replies[1]["error"]["code"], -32700)

    def test_main_serves_the_standard_streams(self):
        source = io.StringIO(json.dumps(request("ping")) + "\n")
        target = io.StringIO()
        with mock.patch("sys.stdin", source), mock.patch("sys.stdout", target):
            self.assertEqual(mcp.main(), 0)
        self.assertEqual(json.loads(target.getvalue())["result"], {})


class ViewTest(unittest.TestCase):
    def test_the_text_comes_after_its_state(self):
        with mock.patch.object(client, "ask", return_value=VIEW) as ask:
            text, failed = said(tool("view"))
        ask.assert_called_once_with({"op": "view", "text": True})
        self.assertFalse(failed)
        self.assertTrue(text.startswith("file: one.md\nstate: unsaved edits\n"))
        self.assertTrue(text.endswith("\n\n# One\nxy\n"))

    def test_the_state_alone_leaves_the_text_out(self):
        with mock.patch.object(client, "ask", return_value=VIEW) as ask:
            text, failed = said(tool("view", {"part": "status"}))
        ask.assert_called_once_with({"op": "view", "text": False})
        self.assertFalse(failed)
        self.assertNotIn("# One", text)

    def test_the_selection_alone(self):
        chosen = dict(VIEW, selection={"start": {}, "end": {}, "text": "xy"})
        with mock.patch.object(client, "ask", return_value=chosen):
            self.assertEqual(said(tool("view", {"part": "selection"})), ("xy", False))

    def test_no_selection_is_an_error_the_agent_can_read(self):
        with mock.patch.object(client, "ask", return_value=VIEW):
            text, failed = said(tool("view", {"part": "selection"}))
        self.assertEqual((text, failed), ("nothing is selected", True))

    def test_an_unknown_part_is_an_error_the_agent_can_read(self):
        with mock.patch.object(client, "ask") as ask:
            for part in ("everything", 3):
                _text, failed = said(tool("view", {"part": part}))
                self.assertTrue(failed)
        ask.assert_not_called()

    def test_an_editor_out_of_reach_is_an_error_the_agent_can_read(self):
        failure = client.ChannelError("cannot reach the editor: gone")
        with mock.patch.object(client, "ask", side_effect=failure):
            text, failed = said(tool("view"))
        self.assertEqual((text, failed), ("cannot reach the editor: gone", True))


DONE = {
    "ok": True,
    "line": 2,
    "characters": 9,
    "file": "/b/one.md",
    "relative": "one.md",
}


class EditTest(unittest.TestCase):
    def test_replace_names_the_old_and_the_new_text(self):
        with mock.patch.object(client, "ask", return_value=DONE) as ask:
            text, failed = said(
                tool("edit", {"mode": "replace", "old": "a", "new": "b"})
            )
        ask.assert_called_once_with(
            {"op": "edit", "mode": "replace", "new": "b", "old": "a"}
        )
        self.assertEqual((text, failed), ("edited one.md at line 2", False))

    def test_insert_and_selection_take_the_new_text_alone(self):
        for mode in ("insert", "selection"):
            with mock.patch.object(client, "ask", return_value=DONE) as ask:
                tool("edit", {"mode": mode, "new": "b", "old": "ignored"})
            ask.assert_called_once_with({"op": "edit", "mode": mode, "new": "b"})

    def test_the_file_meant_is_named_in_full(self):
        with mock.patch.object(client, "ask", return_value=DONE) as ask:
            tool("edit", {"mode": "insert", "new": "b", "file": "one.md"})
        self.assertEqual(ask.call_args.args[0]["file"], os.path.abspath("one.md"))

    def test_arguments_that_do_not_fit_are_errors_the_agent_can_read(self):
        with mock.patch.object(client, "ask") as ask:
            for arguments in (
                {"new": "b"},
                {"mode": "erase", "new": "b"},
                {"mode": "insert"},
                {"mode": "replace", "new": "b"},
                {"mode": "insert", "new": "b", "file": 3},
            ):
                _text, failed = said(tool("edit", arguments))
                self.assertTrue(failed, arguments)
        ask.assert_not_called()

    def test_a_refusal_is_passed_on(self):
        failure = client.ChannelError("the old text occurs 2 times")
        with mock.patch.object(client, "ask", side_effect=failure):
            text, failed = said(
                tool("edit", {"mode": "replace", "old": "a", "new": "b"})
            )
        self.assertEqual((text, failed), ("the old text occurs 2 times", True))

    def test_an_edit_left_unanswered_may_have_been_done(self):
        failure = client.Unanswered("no answer from the editor: timed out")
        with mock.patch.object(client, "ask", side_effect=failure):
            text, failed = said(tool("edit", {"mode": "insert", "new": "b"}))
        self.assertTrue(failed)
        self.assertIn("unknown", text)


if __name__ == "__main__":
    unittest.main()
