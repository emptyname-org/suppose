"""The agent engines of the terminal pane and their conversations.

A working folder keeps its conversations in its `.book-editor` folder, one
for each engine, transcripts included: moved or copied, the folder goes on
where it stopped.

    .book-editor/engine            the engine chosen
    .book-editor/claude/session    id of the conversation
    .book-editor/claude/ID.jsonl   its transcript; Claude Code resumes by path
    .book-editor/codex/session
    .book-editor/codex/rollout-…-ID.jsonl
                                   Codex resumes by id; its home holds a link

The conversation is the one the engine had open last: /clear, /new and
/resume in the engine change it. Claude Code tells through a hook, Codex
shows by the rollout it keeps open.

Environment:
  BOOK_ENGINE   engine to run (default: the folder's choice, else claude)
  BOOK_CLAUDE   the claude command (default: claude)
  BOOK_CODEX    the codex command (default: codex)
"""

import fcntl
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import uuid

STATE_DIR = ".book-editor"
ENGINE_ENV = "BOOK_ENGINE"
ENGINES = {"claude": "Claude Code", "codex": "Codex"}  # name: label, menu order
DEFAULT_ENGINE = "claude"
COMMAND_ENV = {"claude": "BOOK_CLAUDE", "codex": "BOOK_CODEX"}
SOCKET_ENV = "BOOK_EDITOR_SOCKET"
TOOLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"
)
WATCH_SECONDS = 1.0  # between looks at the conversation Codex has open
MAX_HEADER_BYTES = 4 * 1024 * 1024
ROLLOUT = re.compile(
    r"rollout-(\d{4})-(\d{2})-(\d{2})T[0-9-]+?-"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl\Z"
)
CLAUDE_PROMPT = (
    "You run in the terminal pane of a book editor; the writer works in the "
    "editor pane beside you, on a file of this folder. `book-view` prints the "
    "text open there, including edits not saved yet; `book-view --status` "
    "names the file, the cursor and the selection; `book-view --selection` "
    "prints the selected passage. `book-edit` changes the open text in place, "
    "undoable in the editor (see `book-edit --help`). For the open file use "
    "these rather than the file on disk; edit other files on disk as usual."
)


# ---- the folder's record ---------------------------------------------------


def read_word(path):
    try:
        with open(path, encoding="utf-8") as source:
            return source.readline().strip() or None
    except (OSError, ValueError):
        return None


def write_word(folder, name, word):
    """Record a word under the folder's .book-editor, whole or not at all."""
    top = os.path.join(folder, STATE_DIR)
    path = os.path.join(top, name)
    fresh = not os.path.isdir(top)
    os.makedirs(top, mode=0o700, exist_ok=True)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if fresh:  # conversations are not for a repository the folder may be
        with open(os.path.join(top, ".gitignore"), "w", encoding="ascii") as target:
            target.write("*\n")
    partial = f"{path}.{os.getpid()}"
    try:
        with open(partial, "w", encoding="utf-8") as target:
            target.write(word + "\n")
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.unlink(partial)


def chosen_engine(folder):
    word = read_word(os.path.join(folder, STATE_DIR, "engine"))
    return word if word in ENGINES else DEFAULT_ENGINE


def choose_engine(folder, name):
    """Record the folder's engine. False when the folder takes no record."""
    try:
        write_word(folder, "engine", name)
    except OSError:
        return False
    return True


def read_session(folder, engine):
    word = read_word(os.path.join(folder, STATE_DIR, engine, "session"))
    try:
        return str(uuid.UUID(word)) if word else None
    except ValueError:
        return None


def write_session(folder, engine, session):
    write_word(folder, os.path.join(engine, "session"), session)


# ---- one agent at a time on a conversation ---------------------------------


def state_home():
    return os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")


def folder_key(folder):
    return hashlib.sha256(os.fsencode(os.path.realpath(folder))).hexdigest()


def claim(folder, engine):
    """Take the folder's conversation with the engine for this process: a
    locked descriptor to keep open while the engine runs, gone with the
    process. None when another agent has it, or no lock can be kept."""
    directory = os.path.join(
        state_home(), "book-editor", "claims", folder_key(folder), engine
    )
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        guard = os.open(
            os.path.join(directory, "lock"),
            os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC,
            0o600,
        )
    except OSError:
        return None
    try:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(guard)
        return None
    return guard


# ---- Claude Code -----------------------------------------------------------


def claude_transcript(folder, session):
    return os.path.join(
        os.path.abspath(folder), STATE_DIR, "claude", session + ".jsonl"
    )


def earlier_session(folder):
    """The conversation an earlier version kept outside the folder."""
    path = os.path.join(
        state_home(), "book-editor", "sessions", folder_key(folder), "session"
    )
    try:
        word = read_word(path)
        return str(uuid.UUID(word)) if word else None
    except ValueError:
        return None


def adopt_claude(folder, session):
    """Move the transcript Claude Code wrote into its own home to the folder.
    True when the folder has the transcript."""
    target = claude_transcript(folder, session)
    if os.path.isfile(target):
        return True
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    pattern = os.path.join(glob.escape(config), "projects", "*", session + ".jsonl")
    found = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not found:
        return False
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    shutil.move(found[-1], target)
    beside = found[-1].removesuffix(".jsonl")  # what the conversation set aside
    if os.path.isdir(beside) and not os.path.exists(target.removesuffix(".jsonl")):
        shutil.move(beside, target.removesuffix(".jsonl"))
    return True


def claude_settings(folder):
    """Settings that make Claude Code tell which conversation it opens, at
    its start and after /clear and /resume: its SessionStart hook."""
    command = shlex.join([os.path.join(TOOLS_DIR, "book-session"), folder])
    hook = {"hooks": [{"type": "command", "command": command}]}
    return json.dumps({"hooks": {"SessionStart": [hook]}})


def note_claude(folder, source):
    """The hook: record the conversation Claude Code says it opened."""
    try:
        session = str(uuid.UUID(json.load(source)["session_id"]))
    except (ValueError, KeyError, TypeError, AttributeError):
        return
    try:
        if session != read_session(folder, "claude"):
            write_session(folder, "claude", session)
    except OSError:
        pass  # the conversation goes on; the folder keeps the one before


def claude_arguments(folder):
    """Arguments that continue the folder's conversation or start it."""
    recorded = read_session(folder, "claude")
    session = recorded or earlier_session(folder)
    report = ["--settings", claude_settings(folder)]
    if session is not None and adopt_claude(folder, session):
        if session != recorded:
            write_session(folder, "claude", session)
        return [*report, "--resume", claude_transcript(folder, session)]
    # never reuse an id: claude refuses one that is taken
    session = str(uuid.uuid4())
    write_session(folder, "claude", session)
    return [*report, "--session-id", session]


# ---- Codex -----------------------------------------------------------------


def codex_home():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def home_rollouts(session="*"):
    """Paths in Codex's home of the rollouts of a conversation, or of all."""
    name = f"rollout-*-{session}.jsonl"
    pattern = os.path.join(glob.escape(codex_home()), "sessions", "*", "*", "*", name)
    return sorted(glob.glob(pattern))


def folder_rollout(folder, session):
    pattern = os.path.join(
        glob.escape(os.path.abspath(folder)),
        STATE_DIR,
        "codex",
        f"rollout-*-{session}.jsonl",
    )
    found = sorted(glob.glob(pattern))
    return found[-1] if found else None


def link_codex(folder, session):
    """Keep the conversation's rollout in the folder and a link to it in
    Codex's home, where Codex looks for it. A rollout Codex wrote into its
    home moves to the folder. True when the conversation can be resumed."""
    kept = folder_rollout(folder, session)
    for path in home_rollouts(session):
        if os.path.islink(path):
            if kept is None or os.path.realpath(path) != os.path.realpath(kept):
                os.unlink(path)  # leads to where the folder was
        elif os.path.isfile(path):  # Codex wrote here: this is the current one
            directory = os.path.join(os.path.abspath(folder), STATE_DIR, "codex")
            os.makedirs(directory, mode=0o700, exist_ok=True)
            kept = os.path.join(directory, os.path.basename(path))
            shutil.move(path, kept)
    if kept is None:
        return False
    match = ROLLOUT.match(os.path.basename(kept))
    if match is None:
        return False
    link = os.path.join(
        codex_home(), "sessions", *match.group(1, 2, 3), os.path.basename(kept)
    )
    if not os.path.lexists(link):
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(kept, link)
    return True


def rollout_header(path):
    try:
        with open(path, "rb") as source:
            header = json.loads(source.readline(MAX_HEADER_BYTES))
    except (OSError, ValueError):
        return {}
    if not isinstance(header, dict) or header.get("type") != "session_meta":
        return {}
    payload = header.get("payload")
    return payload if isinstance(payload, dict) else {}


def family(pid):
    """The process and what it started, as far as that still has its parent."""
    children: dict[int, list[int]] = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as source:
                fields = source.read().rpartition(b")")[2].split()
            children.setdefault(int(fields[1]), []).append(int(name))
        except (OSError, ValueError, IndexError):
            continue  # it ended meanwhile
    found, at = [pid], 0
    while at < len(found):
        found += children.get(found[at], ())
        at += 1
    return found


def open_rollouts(pid):
    """Rollouts the engine has open: Codex keeps open what it writes, in
    its own process as long as it is given options (see codex_options)."""
    found = set()
    for member in family(pid):
        directory = f"/proc/{member}/fd"
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            try:
                path = os.readlink(os.path.join(directory, name))
            except OSError:
                continue
            if ROLLOUT.match(os.path.basename(path)):
                found.add(path)
    return found


def current_codex(pid):
    """Id of the conversation the user has with the running Codex: of those
    it has open the one written last. What Codex opened for itself is not
    the user's."""
    latest, current = 0.0, None
    for path in open_rollouts(pid):
        match = ROLLOUT.match(os.path.basename(path))
        payload = rollout_header(path)
        if match is None or payload.get("id") != match.group(4):
            continue
        if payload.get("thread_source") != "user":
            continue
        try:
            written = os.path.getmtime(path)
        except OSError:
            continue
        if current is None or written > latest:
            latest, current = written, match.group(4)
    return current


def toml_string(text):
    return json.dumps(text, ensure_ascii=False)


def codex_options():
    """The editor's tools as an MCP server: Codex keeps its commands from
    the editor's socket, its servers not."""
    socket_path = os.environ.get(SOCKET_ENV)
    if not socket_path:
        return []
    server = os.path.join(TOOLS_DIR, "book-mcp")
    return [
        "-c",
        "mcp_servers.book_editor.command=" + toml_string(server),
        "-c",
        "mcp_servers.book_editor.env={"
        + SOCKET_ENV
        + "="
        + toml_string(socket_path)
        + "}",
    ]


def codex_arguments(folder):
    """Arguments that continue the folder's conversation, or None when a new
    one starts and has to be found."""
    session = read_session(folder, "codex")
    if session is not None and link_codex(folder, session):
        return ["resume", *codex_options(), session]
    return None


# ---- running the engine ----------------------------------------------------


def find_command(name):
    """The command as given, or where a user's own installs go when the
    desktop's PATH leaves that out."""
    if os.sep in name or shutil.which(name):
        return name
    local = os.path.expanduser(os.path.join("~", ".local", "bin", name))
    return local if os.access(local, os.X_OK) else name


def run(argv, watch=None):
    """Run the engine and wait for it; watch(its process id) is called now
    and then while it runs. Returns the exit status for our own exit."""
    running: list[subprocess.Popen] = []

    def hand_on(signum, _frame):
        for child in running:
            child.send_signal(signum)

    def leave_to_child(_signum, _frame):
        pass  # the terminal sent it to the engine as well

    # handlers, not SIG_IGN: the engine starts with the default dispositions
    for signum in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(signum, hand_on)
    for signum in (signal.SIGINT, signal.SIGQUIT):
        signal.signal(signum, leave_to_child)
    child = subprocess.Popen(argv)
    running.append(child)
    while True:
        try:
            status = child.wait(WATCH_SECONDS if watch is not None else None)
            break
        except subprocess.TimeoutExpired:
            watch(child.pid)
    return status if status >= 0 else 128 - status


def run_claude(folder, command, extra, claimed):
    arguments = []
    if claimed:
        try:
            arguments = claude_arguments(folder)
        except OSError:
            pass  # no record, no memory of the conversation
    status = run([command, "--append-system-prompt", CLAUDE_PROMPT, *arguments, *extra])
    if claimed:
        try:
            session = read_session(folder, "claude")
            if session is not None:  # the one started, or opened since
                adopt_claude(folder, session)
        except OSError:
            pass  # stays in Claude Code's home, taken over at the next start
    return status


def run_codex(folder, command, extra, claimed):
    arguments = None
    if claimed:
        try:
            arguments = codex_arguments(folder)
        except OSError:
            pass
    argv = [command, *(arguments or codex_options()), *extra]
    if not claimed:
        return run(argv)

    def watch(pid):
        session = current_codex(pid)
        if session is not None and session != read_session(folder, "codex"):
            try:
                write_session(folder, "codex", session)
            except OSError:
                pass

    status = run(argv, watch)
    try:
        session = read_session(folder, "codex")
        if session is not None:  # the one resumed, or opened since
            link_codex(folder, session)
    except OSError:
        pass  # stays in Codex's home, taken over at the next start
    return status


RUNNERS = {"claude": run_claude, "codex": run_codex}


def hook_main(argv=None):
    """book-session FOLDER: Claude Code's hook, its report on stdin. Says
    nothing: what a SessionStart hook prints, the agent reads."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 1:
        note_claude(arguments[0], sys.stdin)
    return 0


def main(argv=None):
    extra = sys.argv[1:] if argv is None else argv
    folder = os.getcwd()
    engine = os.environ.get(ENGINE_ENV) or chosen_engine(folder)
    if engine not in ENGINES:
        engine = DEFAULT_ENGINE
    command = find_command(os.environ.get(COMMAND_ENV[engine]) or engine)
    guard = claim(folder, engine)
    try:
        return RUNNERS[engine](folder, command, extra, guard is not None)
    except OSError as error:
        reason = error.strerror or error
        print(f"book-agent: cannot run {command}: {reason}", file=sys.stderr)
        print(
            f"Set {COMMAND_ENV[engine]} to the {ENGINES[engine]} command.",
            file=sys.stderr,
        )
        return 127
    finally:
        if guard is not None:
            os.close(guard)
