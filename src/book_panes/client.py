"""book-view and book-edit: the agent's side of the editor channel.

Standard library only, so the commands run in the agent's own environment.
"""

import argparse
import json
import os
import socket
import sys
import time

SOCKET_ENV = "BOOK_EDITOR_SOCKET"
TIMEOUT_SECONDS = 15.0  # whole exchange; the editor's own deadline is shorter
MAX_REPLY_BYTES = 64 * 1024 * 1024
REFUSED = 1
UNANSWERED = 3


class ChannelError(Exception):
    """The editor did not carry out the request."""


class Unanswered(ChannelError):
    """The request was sent, no whole answer came: its outcome is unknown."""


def ask(request, path=None, timeout=TIMEOUT_SECONDS):
    """Send one request to the editor and return its reply. timeout bounds
    the whole exchange."""
    path = path or os.environ.get(SOCKET_ENV)
    if not path:
        raise ChannelError(f"not in the book editor's terminal ({SOCKET_ENV} unset)")
    end = time.monotonic() + timeout
    received = bytearray()

    def wait(connection):
        """Let the next step take what is left of the time, no more."""
        left = end - time.monotonic()
        if left <= 0:
            raise TimeoutError("timed out")
        connection.settimeout(left)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        try:
            wait(connection)
            connection.connect(path)
        except OSError as error:
            raise ChannelError(f"cannot reach the editor: {error}") from error
        try:
            wait(connection)
            connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
            while not received.endswith(b"\n"):
                wait(connection)
                chunk = connection.recv(65536)
                if not chunk:
                    break
                received += chunk
                if len(received) > MAX_REPLY_BYTES:
                    raise OverflowError("answer too long")
        except (OSError, OverflowError) as error:
            raise Unanswered(f"no answer from the editor: {error}") from error
    try:
        reply = json.loads(received.decode("utf-8"))
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        raise Unanswered("no answer from the editor")
    if not reply.get("ok"):
        raise ChannelError(str(reply.get("error") or "refused"))
    return reply


def shown_name(reply):
    return reply.get("relative") or reply.get("file") or "(unsaved new text)"


def place(position):
    return f"line {position['line']}, column {position['column']}"


def status_text(reply):
    if reply["conflict"]:
        state = "unsaved edits, file changed on disk"
    elif reply["unsaved"]:
        state = "unsaved edits"
    else:
        state = "saved"
    selection = reply["selection"]
    selected = (
        "{} to {}".format(place(selection["start"]), place(selection["end"]))
        if selection
        else "none"
    )
    return "\n".join(
        (
            "file: " + shown_name(reply),
            "state: " + state,
            f"size: {reply['lines']} lines, {reply['characters']} characters",
            "cursor: " + place(reply["cursor"]),
            "selection: " + selected,
        )
    )


def view(arguments):
    request = {"op": "view", "text": not (arguments.status or arguments.selection)}
    if arguments.json:
        request["text"] = True
    reply = ask(request)
    if arguments.json:
        text = json.dumps(reply, ensure_ascii=False, indent=1) + "\n"
    elif arguments.status:
        text = status_text(reply) + "\n"
    elif arguments.selection:
        if reply["selection"] is None:
            raise ChannelError("nothing is selected")
        text = reply["selection"]["text"]
    else:
        text = reply["text"]
    sys.stdout.write(text)


def edit(arguments):
    new = arguments.new
    if new is None:
        new = sys.stdin.read()
        new = new.removesuffix("\n")
    request = {"op": "edit", "mode": arguments.mode, "new": new}
    if arguments.mode == "replace":
        request["old"] = arguments.old
    if arguments.file:
        request["file"] = os.path.abspath(arguments.file)
    reply = ask(request)
    print(f"edited {shown_name(reply)} at line {reply['line']}")


def view_parser():
    parser = argparse.ArgumentParser(
        prog="book-view",
        description="Print the text open in the book editor, including edits "
        "that are not saved yet.",
    )
    what = parser.add_mutually_exclusive_group()
    what.add_argument(
        "--status",
        action="store_true",
        help="print the file name, saved state, size, cursor and selection",
    )
    what.add_argument(
        "--selection", action="store_true", help="print the selected passage"
    )
    what.add_argument("--json", action="store_true", help="print all of it as JSON")
    return parser


def edit_parser():
    parser = argparse.ArgumentParser(
        prog="book-edit",
        description="Change the text open in the book editor. The writer sees "
        "the change at once and can undo it. NEW is read from standard input "
        "when left out (one final newline is dropped).",
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        help="refuse unless the editor shows this file",
    )
    modes = parser.add_subparsers(dest="mode", required=True, metavar="MODE")
    replace = modes.add_parser(
        "replace", help="replace OLD, which must occur exactly once, with NEW"
    )
    replace.add_argument("old", metavar="OLD")
    replace.add_argument("new", metavar="NEW", nargs="?")
    selection = modes.add_parser("selection", help="replace the selection with NEW")
    selection.add_argument("new", metavar="NEW", nargs="?")
    insert = modes.add_parser("insert", help="insert NEW at the cursor")
    insert.add_argument("new", metavar="NEW", nargs="?")
    return parser


TOOLS = {"view": (view_parser, view), "edit": (edit_parser, edit)}


def main(tool, argv=None):
    """Run book-view or book-edit; returns the exit status."""
    make_parser, run = TOOLS[tool]
    parser = make_parser()
    arguments = parser.parse_args(argv)
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):  # the book's text, whatever the locale
            stream.reconfigure(encoding="utf-8", errors="surrogateescape")
    try:
        run(arguments)
        sys.stdout.flush()
    except Unanswered as error:
        print(f"{parser.prog}: {error}", file=sys.stderr)
        if tool == "edit":
            print("The edit may have been made; check with book-view.", file=sys.stderr)
        return UNANSWERED
    except ChannelError as error:
        print(f"{parser.prog}: {error}", file=sys.stderr)
        return REFUSED
    except BrokenPipeError:  # the reader, such as head, has seen enough
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return 0
