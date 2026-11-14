"""The editor channel as MCP tools.

For agents that keep their own commands from the editor's socket, as Codex's
sandbox does: the agent starts this server outside that sandbox. JSON-RPC 2.0,
one message a line, on stdin and stdout.
"""

import json
import os
import sys

from . import client

PROTOCOL = "2024-11-05"
INSTRUCTIONS = (
    "You run in the terminal pane of a book editor; the writer works in the "
    "editor pane beside you, on a file of this folder. For that file use the "
    "view and edit tools rather than the file on disk; they see edits not "
    "saved yet. Edit other files on disk as usual."
)
TOOLS = [
    {
        "name": "view",
        "description": (
            "The text open in the book editor beside this terminal, including "
            "edits not saved yet, after its name, saved state, cursor and "
            "selection. part=status leaves the text out, part=selection gives "
            "the selected passage alone."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "part": {"type": "string", "enum": ["all", "status", "selection"]}
            },
        },
    },
    {
        "name": "edit",
        "description": (
            "Change the text open in the book editor in place, as one undo "
            "step there. mode=replace puts new in place of old, which must "
            "occur exactly once; mode=selection replaces the selected "
            "passage; mode=insert inserts new at the cursor. file, when "
            "given, must be the file open in the editor."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["replace", "selection", "insert"]},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "file": {"type": "string"},
            },
            "required": ["mode", "new"],
        },
    },
]


class BadCall(Exception):
    """The arguments of a tool call do not fit the tool."""


def text_argument(arguments, name, default=None):
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise BadCall(f"{name} must be a string")
    return value


def view(arguments):
    part = text_argument(arguments, "part", "all")
    if part not in ("all", "status", "selection"):
        raise BadCall("part must be all, status or selection")
    reply = client.ask({"op": "view", "text": part == "all"})
    if part == "status":
        return client.status_text(reply)
    if part == "selection":
        if reply["selection"] is None:
            raise client.ChannelError("nothing is selected")
        return reply["selection"]["text"]
    return client.status_text(reply) + "\n\n" + reply["text"]


def edit(arguments):
    mode = text_argument(arguments, "mode")
    if mode not in ("replace", "selection", "insert"):
        raise BadCall("mode must be replace, selection or insert")
    request = {"op": "edit", "mode": mode, "new": text_argument(arguments, "new")}
    if mode == "replace":
        request["old"] = text_argument(arguments, "old")
    if arguments.get("file") is not None:
        request["file"] = os.path.abspath(text_argument(arguments, "file"))
    reply = client.ask(request)
    return f"edited {client.shown_name(reply)} at line {reply['line']}"


CALLS = {"view": view, "edit": edit}


def call(params):
    """Result of a tools/call: the tool's text, or what went wrong as text
    the agent can act on."""
    name = params.get("name")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(name, str) or name not in CALLS:
        raise BadCall("no such tool")
    if not isinstance(arguments, dict):
        raise BadCall("arguments must be an object")
    try:
        text, failed = CALLS[name](arguments), False
    except client.Unanswered as error:
        text = f"{error}. Whether it was done is unknown; view before trying again."
        failed = True
    except (client.ChannelError, BadCall) as error:
        text, failed = str(error), True
    return {"content": [{"type": "text", "text": text}], "isError": failed}


def answer(message):
    """The reply to one message, None for what takes none."""
    if not isinstance(message, dict):
        return failure(None, -32600, "not a request")
    identity = message.get("id")
    method = message.get("method")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}
    if identity is None or not isinstance(method, str):
        return None  # a notification, or a reply to something never asked
    result: dict[str, object]
    if method == "initialize":
        wanted = params.get("protocolVersion")
        result = {
            "protocolVersion": wanted if isinstance(wanted, str) else PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "suppose", "version": "1"},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            result = call(params)
        except BadCall as error:
            return failure(identity, -32602, str(error))
    else:
        return failure(identity, -32601, f"no such method: {method}")
    return {"jsonrpc": "2.0", "id": identity, "result": result}


def failure(identity, code, text):
    return {"jsonrpc": "2.0", "id": identity, "error": {"code": code, "message": text}}


def serve(source, target):
    for line in source:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            message, reply = None, failure(None, -32700, "not JSON")
        else:
            try:
                reply = answer(message)
            except Exception as error:  # noqa: BLE001 - one message must not end the server
                identity = message.get("id") if isinstance(message, dict) else None
                reply = failure(identity, -32603, f"internal error: {error}")
        if reply is not None:
            target.write(json.dumps(reply, ensure_ascii=False) + "\n")
            target.flush()


def main():
    serve(sys.stdin, sys.stdout)
    return 0
