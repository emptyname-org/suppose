"""Editor channel: the agent reads and edits the text open in the editor.

A Unix socket in a folder private to the user. One request per connection:
a JSON object on one line in, a JSON object on one line out. The reply has
"ok": true plus the result, or "ok": false and "error".
"""

import errno
import json
import logging
import os
import stat
import tempfile

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

SOCKET_ENV = "SUPPOSE_SOCKET"
DIR_PREFIX = "suppose-"
SOCKET_NAME = "socket"
MAX_SOCKET_PATH_BYTES = 107  # sun_path without its final NUL
MAX_REQUEST_BYTES = 16 * 1024 * 1024
MAX_CONNECTIONS = 8
DEADLINE_SECONDS = 10.0  # whole exchange: request, answer, reply
READ_CHUNK = 65536

LOGGER = logging.getLogger(__name__)


class Refused(Exception):
    """The request cannot be carried out; the message goes to the client."""


def text_field(request, name, default=None):
    """A string of the request that the text buffer can hold."""
    value = request.get(name, default)
    if not isinstance(value, str):
        raise Refused(f"{name} must be a string")
    if "\0" in value:
        raise Refused(f"{name} holds a NUL character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused(f"{name} is not valid Unicode") from None
    return value


def plan_edit(text, cursor, selection, request):
    """Where an edit request changes the text: (start, end, replacement), in
    characters. cursor is an offset, selection a (start, end) pair or None."""
    mode = request.get("mode")
    new = text_field(request, "new", "")
    if mode == "replace":
        old = text_field(request, "old")
        if not old:
            raise Refused("old must not be empty")
        start = text.find(old)
        if start < 0:
            raise Refused("old text not found in the editor")
        if text.find(old, start + 1) >= 0:
            raise Refused("old text occurs more than once, make it unique")
        return start, start + len(old), new
    if mode == "selection":
        if selection is None:
            raise Refused("nothing is selected")
        return selection[0], selection[1], new
    if mode == "insert":
        return cursor, cursor, new
    raise Refused("unknown edit mode")


def process_runs(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def remove_directory(directory):
    """Remove a socket folder of ours: the socket, then the empty folder.
    Anything else in it is left alone."""
    try:
        status = os.lstat(directory)
        if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.getuid():
            return
        path = os.path.join(directory, SOCKET_NAME)
        try:
            if stat.S_ISSOCK(os.lstat(path).st_mode):
                os.unlink(path)
        except FileNotFoundError:
            pass
        os.rmdir(directory)
    except OSError:
        pass


def remove_stale(base):
    """Remove the socket folders of editors that no longer run."""
    try:
        names = os.listdir(base)
    except OSError:
        return
    for name in names:
        if not name.startswith(DIR_PREFIX):
            continue
        pid = name[len(DIR_PREFIX) :].split("-", 1)[0]
        if pid.isdigit() and not process_runs(int(pid)):
            remove_directory(os.path.join(base, name))


def same_user(connection):
    try:
        credentials = connection.get_socket().get_credentials()
        return credentials.get_unix_user() == os.getuid()
    except GLib.GError:
        return False


class Channel:
    """Serves requests on the socket at self.path until close().

    handler(request) returns the result as a dict or raises Refused. The
    constructor raises OSError or GLib.GError and leaves nothing behind.
    """

    def __init__(self, handler, base=None, deadline_seconds=DEADLINE_SECONDS):
        self.handler = handler
        self.deadline_seconds = deadline_seconds
        self.exchanges = set()
        self.service = None
        base = base or GLib.get_user_runtime_dir()
        os.makedirs(base, mode=0o700, exist_ok=True)
        remove_stale(base)
        self.directory = tempfile.mkdtemp(
            prefix=f"{DIR_PREFIX}{os.getpid()}-", dir=base
        )
        self.path = os.path.join(self.directory, SOCKET_NAME)
        service = Gio.SocketService.new()
        try:
            if len(os.fsencode(self.path)) > MAX_SOCKET_PATH_BYTES:
                raise OSError(errno.ENAMETOOLONG, "socket path too long", self.path)
            service.add_address(
                Gio.UnixSocketAddress.new(self.path),
                Gio.SocketType.STREAM,
                Gio.SocketProtocol.DEFAULT,
                None,
            )
            os.chmod(self.path, 0o600)
        except (GLib.GError, OSError):
            service.stop()
            service.close()
            remove_directory(self.directory)
            raise
        service.connect("incoming", self._on_incoming)
        self.service = service

    def _on_incoming(self, _service, connection, _source):
        if len(self.exchanges) >= MAX_CONNECTIONS or not same_user(connection):
            try:
                connection.get_socket().close()
            except GLib.GError:
                pass
        else:
            Exchange(self, connection)
        return True

    def answer(self, line):
        """The reply line for a request line."""
        try:
            request = json.loads(line.decode("utf-8"))
        except ValueError:
            request = None
        if not isinstance(request, dict):
            reply = {"ok": False, "error": "bad request"}
        else:
            try:
                reply = dict(self.handler(request), ok=True)
            except Refused as refusal:
                reply = {"ok": False, "error": str(refusal)}
            except Exception:  # answer the client, never leave it waiting
                LOGGER.exception("editor channel request failed")
                reply = {"ok": False, "error": "internal error in the editor"}
        return json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n"

    def close(self):
        """Stop serving, drop open connections, remove the socket."""
        if self.service is None:
            return
        service, self.service = self.service, None
        service.stop()
        service.close()
        for exchange in list(self.exchanges):
            exchange.finish()
        remove_directory(self.directory)


class Exchange:
    """One connection: read a request line, answer it, close."""

    def __init__(self, channel, connection):
        self.channel = channel
        self.connection = connection
        self.cancellable = Gio.Cancellable()
        self.received = bytearray()
        self.timer = GLib.timeout_add(
            int(channel.deadline_seconds * 1000), self._on_deadline
        )
        channel.exchanges.add(self)
        self._read()

    def _on_deadline(self):
        self.timer = 0
        self.finish()
        return GLib.SOURCE_REMOVE

    def _read(self):
        self.connection.get_input_stream().read_bytes_async(
            READ_CHUNK, GLib.PRIORITY_DEFAULT, self.cancellable, self._on_read
        )

    def _on_read(self, stream, result):
        try:
            data = stream.read_bytes_finish(result).get_data()
        except GLib.GError:
            data = None  # cancelled or broken
        if self.connection is None:
            return  # finished while the read was under way
        if not data:
            self.finish()  # closed before a full line
            return
        end = data.find(b"\n")
        if end >= 0:
            end += len(self.received)
        self.received += data
        if end < 0 and len(self.received) <= MAX_REQUEST_BYTES:
            self._read()
        elif end < 0 or end > MAX_REQUEST_BYTES:
            self.finish()  # too long
        else:
            reply = self.channel.answer(bytes(self.received[:end]))
            self.received.clear()
            self._write(GLib.Bytes.new(reply))

    def _write(self, data):
        self.connection.get_output_stream().write_bytes_async(
            data, GLib.PRIORITY_DEFAULT, self.cancellable, self._on_written, data
        )

    def _on_written(self, stream, result, data):
        try:
            written = stream.write_bytes_finish(result)
        except GLib.GError:
            written = -1
        if self.connection is None:
            return
        size = data.get_size()
        if 0 <= written < size:
            self._write(GLib.Bytes.new_from_bytes(data, written, size - written))
        else:
            self.finish()  # all sent, or the client is gone

    def finish(self):
        """Close the connection; pending reads and writes end as cancelled."""
        if self.connection is None:
            return
        connection, self.connection = self.connection, None
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.cancellable.cancel()
        try:
            connection.get_socket().close()
        except GLib.GError:
            pass
        self.channel.exchanges.discard(self)
