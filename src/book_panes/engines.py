"""The agent engines of the terminal pane and their conversations.

A working folder keeps its conversations in its `.book-editor` folder, one
for each engine, transcripts included: moved or copied, the folder goes on
where it stopped.

    .book-editor/engine            the engine chosen
    .book-editor/claude/session    id of the conversation
    .book-editor/claude/ID.jsonl   its transcript; Claude Code resumes by path
    .book-editor/claude/ID/        copy of what Claude Code keeps beside it
    .book-editor/codex/session
    .book-editor/codex/rollout-…-ID.jsonl
                                   Codex resumes by id; its home holds a link

The conversation is the one the engine had open last: /clear, /new and
/resume in the engine change it. Only the engine's own word counts for
that. Claude Code tells through a hook, which reports to the agent that
runs it. Codex shows by the rollouts it keeps open, looked at every
WATCH_SECONDS: what it opens later than the last look before its end is
not followed, the folder then goes on with the conversation before.

An agent holds a lock on each conversation it uses. A copy of a folder has
the conversations of its original; while an agent of the original runs,
the copy's agent runs off the record, with a conversation of its own.

Environment:
  BOOK_ENGINE   engine to run (default: the folder's choice, else claude)
  BOOK_CLAUDE   the claude command (default: claude)
  BOOK_CODEX    the codex command (default: codex)
"""

import errno
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
WATCH_SECONDS = 0.25  # between looks at what the engine opened
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


def record_folder(folder, *parts):
    """The folder's .book-editor, or a folder under it, made when missing.
    It never appears without its .gitignore: conversations are not for a
    repository the folder may be. One the user removed stays removed."""
    top = os.path.join(folder, STATE_DIR)
    if not os.path.isdir(top):
        fresh = f"{top}.{os.getpid()}"
        shutil.rmtree(fresh, ignore_errors=True)  # of a process that died here
        os.mkdir(fresh, 0o700)
        try:
            with open(os.path.join(fresh, ".gitignore"), "w", encoding="ascii") as note:
                note.write("*\n")
            os.rename(fresh, top)
        except OSError:
            shutil.rmtree(fresh, ignore_errors=True)
            if not os.path.isdir(top):  # not just made by another
                raise
    path = os.path.join(top, *parts)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def write_word(folder, name, word):
    """Record a word under the folder's .book-editor, whole or not at all."""
    directory = record_folder(folder, os.path.dirname(name))
    path = os.path.join(directory, os.path.basename(name))
    partial = f"{path}.{os.getpid()}"
    try:
        with open(partial, "w", encoding="utf-8") as target:
            target.write(word + "\n")
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.unlink(partial)


def copy_whole(source, target):
    """Copy a file; the target is there whole or not at all."""
    for stale in glob.glob(glob.escape(target) + ".*.part"):
        os.unlink(stale)  # of a copy that did not end
    partial = f"{target}.{os.getpid()}.part"
    try:
        copy = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with open(source, "rb") as origin, open(copy, "wb") as written:
            shutil.copyfileobj(origin, written)
            written.flush()
            os.fsync(written.fileno())
        shutil.copystat(source, partial)
        os.replace(partial, target)
    finally:
        if os.path.lexists(partial):
            os.unlink(partial)


def take_over(source, target):
    """Move a file, also to another filesystem; the source goes once the
    target is there whole."""
    try:
        os.rename(source, target)
        return
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
    copy_whole(source, target)
    os.unlink(source)


def copy_newer(source, target):
    """Copy what a folder holds into another, each file whole. A file the
    other has as large and as new stays; nothing is taken away."""
    for directory, _folders, names in os.walk(source):
        inside = os.path.relpath(directory, source)
        for name in names:
            origin = os.path.join(directory, name)
            goal = os.path.normpath(os.path.join(target, inside, name))
            if os.path.islink(origin) or not os.path.isfile(origin):
                continue
            here = os.stat(origin)
            try:
                there = os.stat(goal)
            except FileNotFoundError:
                there = None
            if there is None or (there.st_size, there.st_mtime) != (
                here.st_size,
                max(here.st_mtime, there.st_mtime),
            ):
                os.makedirs(os.path.dirname(goal), mode=0o700, exist_ok=True)
                copy_whole(origin, goal)


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


def hold(*parts):
    """A lock under the claims for this process: a locked descriptor to
    keep open, gone with the process. None when another process has it, or
    no lock can be kept."""
    path = os.path.join(state_home(), "book-editor", "claims", *parts)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        guard = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(guard)
        return None
    return guard


def claim_file(folder, engine, name):
    """A file of the agent that has the folder's conversation with the
    engine; this machine's, not the folder's."""
    return os.path.join(
        state_home(), "book-editor", "claims", folder_key(folder), engine, name
    )


def claim(folder, engine):
    """Take the folder's conversation with the engine for this process,
    while the engine runs. None when another agent has it."""
    return hold(folder_key(folder), engine, "lock")


class Held:
    """The conversations this agent uses, each under a lock that no agent
    of another folder gets meanwhile."""

    def __init__(self, engine):
        self.engine = engine
        self.guards = {}

    def take(self, session):
        if session not in self.guards:
            guard = hold(self.engine, session)
            if guard is None:
                return False
            self.guards[session] = guard
        return True

    def __contains__(self, session):
        return session in self.guards

    def close(self):
        for guard in self.guards.values():
            os.close(guard)
        self.guards.clear()


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


def claude_home(folder):
    """The folder in Claude Code's home it keeps the folder's conversations
    in, as it told; None when it did not."""
    word = read_word(claim_file(folder, "claude", "home"))
    return word if word and os.path.isdir(word) else None


def note_home(folder, home):
    try:
        with open(claim_file(folder, "claude", "home"), "w", encoding="utf-8") as note:
            note.write(home + "\n")
    except (OSError, ValueError):
        pass  # then it is looked for


def adopt_claude(folder, session):
    """Bring the conversation Claude Code keeps in its home to the folder.
    The transcript moves; what Claude Code sets aside beside it, and goes
    on using there, the folder gets a copy of. True when the folder has the
    transcript. The caller holds the conversation."""
    target = claude_transcript(folder, session)
    home = claude_home(folder)
    if not os.path.isfile(target):
        source = os.path.join(home or "", session + ".jsonl")
        if home is None or not os.path.isfile(source):
            config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser(
                "~/.claude"
            )
            found = glob.glob(
                os.path.join(glob.escape(config), "projects", "*", session + ".jsonl")
            )
            if not found:
                return False
            source = max(found, key=os.path.getmtime)
            home = os.path.dirname(source)
            note_home(folder, home)
        record_folder(folder, "claude")
        take_over(source, target)
    beside = os.path.join(home or "", session)
    if home is not None and os.path.isdir(beside) and not os.path.islink(beside):
        copy_newer(beside, target.removesuffix(".jsonl"))
    return True


def claude_settings(report):
    """Settings that make Claude Code tell which conversation it opens, at
    its start and after /clear and /resume: its SessionStart hook."""
    command = shlex.join([os.path.join(TOOLS_DIR, "book-session"), report])
    hook = {"hooks": [{"type": "command", "command": command}]}
    return json.dumps({"hooks": {"SessionStart": [hook]}})


def report_claude(report, source):
    """The hook: pass what Claude Code says it opened on to the agent that
    runs it, a line in that agent's file. Without the agent, nothing."""
    try:
        told = json.load(source)
        line = json.dumps(
            {"session": told["session_id"], "transcript": told.get("transcript_path")}
        )
        target = os.open(report, os.O_WRONLY | os.O_APPEND)
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        return
    try:
        os.write(target, (line + "\n").encode())
    except OSError:
        pass
    finally:
        os.close(target)


def last_report(report):
    """What Claude Code told last: the id of the conversation, and the
    folder in its home it keeps it in or None. None when it told nothing."""
    try:
        with open(report, encoding="utf-8") as source:
            lines = source.read().splitlines()
    except (OSError, ValueError):
        return None
    for line in reversed(lines):
        try:
            told = json.loads(line)
            session = str(uuid.UUID(told["session"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        transcript = told.get("transcript")
        if (
            isinstance(transcript, str)
            and os.path.isabs(transcript)
            and os.path.basename(transcript) == session + ".jsonl"
        ):
            return session, os.path.dirname(transcript)
        return session, None
    return None


def follow_claude(folder, held, report):
    """Make what Claude Code told last the folder's conversation, unless an
    agent of another folder has it."""
    told = last_report(report)
    if told is None:
        return
    session, home = told
    if home is not None:
        note_home(folder, home)
    if session != read_session(folder, "claude") and held.take(session):
        write_session(folder, "claude", session)


def claude_arguments(folder, held, report):
    """Arguments that continue the folder's conversation or start it; none
    when an agent of another folder has it."""
    for left in glob.glob(glob.escape(claim_file(folder, "claude", "opened.")) + "*"):
        follow_claude(folder, held, left)  # of an agent that died
        os.unlink(left)
    os.close(os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    recorded = read_session(folder, "claude")
    session = recorded or earlier_session(folder)
    if session is not None and not held.take(session):
        return []
    tell = ["--settings", claude_settings(report)]
    if session is not None and adopt_claude(folder, session):
        if session != recorded:
            write_session(folder, "claude", session)
        return [*tell, "--resume", claude_transcript(folder, session)]
    # never reuse an id: claude refuses one that is taken
    session = str(uuid.uuid4())
    held.take(session)
    write_session(folder, "claude", session)
    return [*tell, "--session-id", session]


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


def our_link(path):
    """Whether a link in Codex's home is one of ours: it leads into the
    record of a folder."""
    try:
        record = os.path.dirname(os.readlink(path))
    except OSError:
        return False
    return (
        os.path.basename(record) == "codex"
        and os.path.basename(os.path.dirname(record)) == STATE_DIR
    )


def link_codex(folder, session):
    """Keep the conversation's rollout in the folder and a link to it in
    Codex's home, where Codex looks for it. A rollout Codex wrote into its
    home moves to the folder. True when the conversation can be resumed.
    The caller holds the conversation: no other folder's agent uses the
    link meanwhile."""
    kept = folder_rollout(folder, session)
    for path in home_rollouts(session):
        if os.path.islink(path):
            if kept is not None and os.path.realpath(path) == os.path.realpath(kept):
                continue
            if kept is None and os.path.exists(path):
                return False  # the conversation of another folder
            if our_link(path):
                os.unlink(path)  # leads to where the folder was, or to its copy
        elif os.path.isfile(path):  # Codex wrote here: this is the current one
            kept = os.path.join(record_folder(folder, "codex"), os.path.basename(path))
            take_over(path, kept)
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
    return os.path.realpath(link) == os.path.realpath(kept)


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
    found, at = [pid], 0
    while at < len(found):
        tasks = f"/proc/{found[at]}/task"
        try:
            for task in os.listdir(tasks):
                with open(f"{tasks}/{task}/children", encoding="ascii") as source:
                    found += [int(word) for word in source.read().split()]
        except (OSError, ValueError):
            pass  # it ended meanwhile
        at += 1
    return found


def open_rollouts(members):
    """Rollouts the processes have open: Codex keeps open what it writes,
    in its own process as long as it is given options (see codex_options)."""
    found = set()
    for member in members:
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


def users_rollout(path):
    """Id of the conversation a rollout is of, None for what Codex opened
    for itself and for what is no rollout."""
    match = ROLLOUT.match(os.path.basename(path))
    payload = rollout_header(path)
    if match is None or payload.get("id") != match.group(4):
        return None
    return match.group(4) if payload.get("thread_source") == "user" else None


def current_codex(pid):
    """Id of the conversation the user has with the running Codex: of those
    it has open the one written last."""
    latest, current = 0.0, None
    for path in open_rollouts(family(pid)):
        session = users_rollout(path)
        try:
            written = os.path.getmtime(path)
        except OSError:
            continue
        if session is not None and (current is None or written > latest):
            latest, current = written, session
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
    start = [command, "--append-system-prompt", CLAUDE_PROMPT]
    if not claimed:
        return run([*start, *extra])
    held = Held("claude")
    report = claim_file(folder, "claude", f"opened.{os.getpid()}")
    seen = [0]

    def follow(_pid=None):
        try:
            size = os.stat(report).st_size
            if size != seen[0]:
                seen[0] = size
                follow_claude(folder, held, report)
        except OSError:
            pass  # the folder keeps the conversation before

    try:
        arguments = []
        try:
            arguments = claude_arguments(folder, held, report)
        except OSError:
            pass  # no record, no memory of the conversation
        status = run([*start, *arguments, *extra], follow)
        follow()
        try:
            session = read_session(folder, "claude")
            if session in held:  # the one resumed or started, or opened since
                adopt_claude(folder, session)
        except OSError:
            pass  # stays in Claude Code's home, taken over at the next start
        return status
    finally:
        held.close()
        if os.path.lexists(report):
            os.unlink(report)


def run_codex(folder, command, extra, claimed):
    plain = [command, *codex_options(), *extra]
    if not claimed:
        return run(plain)
    held = Held("codex")

    def watch(pid):
        try:
            session = current_codex(pid)
            if session is None or session == read_session(folder, "codex"):
                return
            if held.take(session):
                write_session(folder, "codex", session)
        except OSError:
            pass  # the folder keeps the conversation before

    try:
        session = read_session(folder, "codex")
        if session is not None and not held.take(session):
            return run(plain)  # an agent of the folder's original has it
        argv = plain
        try:
            if session is not None and link_codex(folder, session):
                argv = [command, "resume", *codex_options(), session, *extra]
        except OSError:
            pass
        status = run(argv, watch)
        try:
            session = read_session(folder, "codex")
            if session in held:  # the one resumed, or opened since
                link_codex(folder, session)
        except OSError:
            pass  # stays in Codex's home, taken over at the next start
        return status
    finally:
        held.close()


RUNNERS = {"claude": run_claude, "codex": run_codex}


def hook_main(argv=None):
    """book-session REPORT: Claude Code's hook, what it opened on stdin.
    Says nothing: what a SessionStart hook prints, the agent reads."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) == 1:
        report_claude(arguments[0], sys.stdin)
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
