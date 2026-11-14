"""The agent engines: which conversation a folder continues, and where its
transcript is kept."""

import contextlib
import errno
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest import mock

from book_panes import engines

from .test_channel import TMP

SCRATCH = os.path.abspath(TMP)
FAKE_CLAUDE = """#!/bin/sh
# says how it was called; writes where Claude Code would, tells its hook
: >"$OUT"
for word; do printf '%s\\n' "$word" >>"$OUT"; done
project=$CLAUDE_CONFIG_DIR/projects/$(printf %s "$PWD" | tr -c 'A-Za-z0-9' -)
while [ $# -gt 0 ]; do
    case $1 in
    --session-id)
        id=$2 source=startup
        mkdir -p "$project/$id"
        echo aside >"$project/$id/note"
        echo first >"$project/$id.jsonl"
        shift
        ;;
    --resume)
        id=$(basename "$2" .jsonl) source=resume
        echo more >>"$2"
        shift
        ;;
    --settings)
        hook=$(python3 -c 'import json, sys
print(json.loads(sys.argv[1])["hooks"]["SessionStart"][0]["hooks"][0]["command"])' \\
            "$2")
        shift
        ;;
    esac
    shift
done
tell() {
    if [ -n "$hook" ] && [ -z "$FAKE_HOOKLESS" ]; then
        printf '{"session_id": "%s", "transcript_path": "%s", "source": "%s"}' \\
            "$1" "$project/$1.jsonl" "$2" | sh -c "$hook"
    fi
}
if [ -n "$id" ]; then tell "$id" "$source"; fi
if [ -n "$FAKE_CLEAR" ]; then  # /clear: another conversation
    mkdir -p "$project"
    echo cleared >"$project/$FAKE_CLEAR.jsonl"
    tell "$FAKE_CLEAR" clear
fi
if [ -n "$FAKE_SLEEP" ]; then exec sleep "$FAKE_SLEEP"; fi
exit "${FAKE_STATUS:-0}"
"""
FAKE_CODEX = """#!/bin/sh
# says how it was called; writes a rollout where Codex would, and keeps it open
: >"$OUT"
for word; do printf '%s\\n' "$word" >>"$OUT"; done
rollout() {
    day=$CODEX_HOME/sessions/2026/11/14
    mkdir -p "$day"
    printf '{"type":"session_meta","payload":{"id":"%s","cwd":"%s","thread_source":"%s"}}\\n' \\
        "$1" "${FAKE_CWD:-$PWD}" "${FAKE_SOURCE:-user}" \\
        >"$day/rollout-2026-11-14T10-00-00-$1.jsonl"
    exec 4>>"$day/rollout-2026-11-14T10-00-00-$1.jsonl"
}
if [ "$1" = resume ]; then
    for word; do
        case $word in ????????-????-????-????-????????????) id=$word ;; esac
    done
    exec 3>>"$(find "$CODEX_HOME/sessions" -name "rollout-*-$id.jsonl")"
    echo more >&3
    if [ -z "$FAKE_NEW" ]; then exit "${FAKE_STATUS:-0}"; fi
    sleep 0.1  # /new comes later than the last word of the conversation before
    rollout "$FAKE_NEW"
else
    rollout "$FAKE_ID"
fi
sleep "${FAKE_SLEEP:-0.5}"
if [ -f .book-editor/codex/session ]; then cp .book-editor/codex/session "$OUT.seen"; fi
exit "${FAKE_STATUS:-0}"
"""


def read(path):
    with open(path, encoding="utf-8") as source:
        return source.read()


class EngineCase(unittest.TestCase):
    def setUp(self):
        os.makedirs(SCRATCH, exist_ok=True)
        self.scratch = os.path.realpath(tempfile.mkdtemp(prefix="en.", dir=SCRATCH))
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.book = os.path.join(self.scratch, "book")
        self.config = os.path.join(self.scratch, "claude")
        self.home = os.path.join(self.scratch, "codex")
        self.out = os.path.join(self.scratch, "out")
        os.makedirs(self.book)
        for name, text in (("claude-fake", FAKE_CLAUDE), ("codex-fake", FAKE_CODEX)):
            with open(os.path.join(self.scratch, name), "w") as target:
                target.write(text)
            os.chmod(os.path.join(self.scratch, name), 0o755)
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in (
            "BOOK_ENGINE",
            "BOOK_EDITOR_SOCKET",
            *(name for name in os.environ if name.startswith("FAKE_")),
        ):
            os.environ.pop(name, None)
        os.environ.update(
            BOOK_CLAUDE=os.path.join(self.scratch, "claude-fake"),
            BOOK_CODEX=os.path.join(self.scratch, "codex-fake"),
            CLAUDE_CONFIG_DIR=self.config,
            CODEX_HOME=self.home,
            XDG_STATE_HOME=os.path.join(self.scratch, "state"),
            HOME=os.path.join(self.scratch, "home"),
            OUT=self.out,
        )
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.book)
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGQUIT):
            self.addCleanup(signal.signal, signum, signal.getsignal(signum))

    def agent(self, engine, *extra, **changes):
        """Run the engine as the pane does; (exit status, its arguments)."""
        os.environ["BOOK_ENGINE"] = engine
        os.environ.update(changes)
        if os.path.exists(self.out):
            os.unlink(self.out)
        status = engines.main(list(extra))
        called = read(self.out).splitlines() if os.path.exists(self.out) else None
        return status, called

    def kept(self, *parts):
        return os.path.join(os.getcwd(), ".book-editor", *parts)

    def another_filesystem(self):
        """Transcripts cannot be renamed into place, as across filesystems."""
        rename = os.rename

        def refuse(source, target):
            if ".jsonl" in os.path.basename(source):
                raise OSError(errno.EXDEV, "another filesystem")
            rename(source, target)

        return mock.patch("os.rename", refuse)

    def full_disk(self):
        """A copy stops halfway."""

        def stop(origin, written):
            written.write(origin.read(2))
            raise OSError(errno.ENOSPC, "full")

        return mock.patch("shutil.copyfileobj", stop)

    @contextlib.contextmanager
    def another_agent(self, engine):
        """The folder's conversation is in use by another agent meanwhile."""
        guard = engines.claim(os.getcwd(), engine)
        self.assertIsNotNone(guard)
        try:
            yield
        finally:
            os.close(guard)


class RecordTest(EngineCase):
    def test_a_folder_without_a_choice_gets_claude(self):
        self.assertEqual(engines.chosen_engine(self.book), "claude")

    def test_the_choice_is_kept_in_the_folder(self):
        self.assertTrue(engines.choose_engine(self.book, "codex"))
        self.assertEqual(engines.chosen_engine(self.book), "codex")
        self.assertEqual(read(self.kept("engine")), "codex\n")

    def test_an_unknown_choice_counts_as_none(self):
        engines.write_word(self.book, "engine", "eliza")
        self.assertEqual(engines.chosen_engine(self.book), "claude")

    def test_the_record_is_private_and_kept_out_of_a_repository(self):
        engines.choose_engine(self.book, "codex")
        self.assertEqual(stat.S_IMODE(os.stat(self.kept()).st_mode), 0o700)
        self.assertEqual(read(self.kept(".gitignore")), "*\n")

    def test_a_gitignore_the_user_removed_stays_removed(self):
        engines.choose_engine(self.book, "codex")
        os.unlink(self.kept(".gitignore"))
        engines.choose_engine(self.book, "claude")
        self.assertFalse(os.path.exists(self.kept(".gitignore")))

    @unittest.skipIf(os.geteuid() == 0, "root writes anywhere")
    def test_the_record_does_not_appear_without_its_gitignore(self):
        with mock.patch("os.rename", side_effect=OSError(errno.ENOSPC, "full")):
            self.assertFalse(engines.choose_engine(self.book, "codex"))
        self.assertEqual(os.listdir(self.book), [])
        self.assertTrue(engines.choose_engine(self.book, "codex"))
        self.assertEqual(read(self.kept(".gitignore")), "*\n")

    def test_a_record_another_made_meanwhile_is_used(self):
        def late(_source, target):
            os.makedirs(os.path.join(target, "claude"))
            raise OSError(errno.ENOTEMPTY, "there")

        with mock.patch("os.rename", late):
            made = engines.record_folder(self.book, "codex")
        self.assertEqual(made, self.kept("codex"))
        self.assertEqual(sorted(os.listdir(self.book)), [".book-editor"])

    def test_a_folder_that_takes_no_record_says_so(self):
        os.chmod(self.book, 0o500)
        self.addCleanup(os.chmod, self.book, 0o700)
        self.assertFalse(engines.choose_engine(self.book, "codex"))
        self.assertEqual(os.listdir(self.book), [])

    def test_the_engine_named_by_the_pane_runs(self):
        engines.choose_engine(self.book, "codex")
        _status, called = self.agent("claude")
        self.assertEqual(called[0], "--append-system-prompt")

    def test_without_a_name_the_folders_choice_runs(self):
        engines.choose_engine(self.book, "codex")
        os.environ["FAKE_ID"] = str(uuid.uuid4())
        self.assertEqual(engines.main([]), 0)
        self.assertEqual(read(self.out), "")  # codex, called without arguments

    def test_an_unknown_engine_name_runs_claude(self):
        _status, called = self.agent("eliza")
        self.assertEqual(called[0], "--append-system-prompt")


class MoveTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.source = os.path.join(self.scratch, "from.jsonl")
        self.target = os.path.join(self.book, "to.jsonl")
        with open(self.source, "w") as target:
            target.write("whole\n")
        os.chmod(self.source, 0o640)

    def test_a_move_within_the_filesystem(self):
        engines.take_over(self.source, self.target)
        self.assertEqual(read(self.target), "whole\n")
        self.assertFalse(os.path.exists(self.source))

    def test_a_move_to_another_filesystem_arrives_whole(self):
        with open(self.target + ".7.part", "w") as stale:
            stale.write("wh")
        with self.another_filesystem():
            engines.take_over(self.source, self.target)
        self.assertEqual(read(self.target), "whole\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.target).st_mode), 0o640)
        self.assertEqual(os.listdir(self.book), ["to.jsonl"])
        self.assertFalse(os.path.exists(self.source))

    def test_a_move_cut_short_leaves_the_source_and_no_target(self):
        with self.another_filesystem(), self.full_disk(), self.assertRaises(OSError):
            engines.take_over(self.source, self.target)
        self.assertEqual(os.listdir(self.book), [])
        self.assertEqual(read(self.source), "whole\n")

    def test_another_failure_of_a_move_is_passed_on(self):
        with self.assertRaises(FileNotFoundError):
            engines.take_over(self.source + ".none", self.target)

    def test_a_folder_is_copied_file_by_file_where_it_is_newer(self):
        source, target = os.path.join(self.scratch, "aside"), self.kept("aside")
        for path, text, age in (
            (os.path.join(source, "deep", "down", "new"), "new\n", 0),
            (os.path.join(source, "grown"), "old\nmore\n", 0),
            (os.path.join(target, "grown"), "old\n", 50),
            (os.path.join(source, "same"), "aaaa\n", 50),
            (os.path.join(target, "same"), "bbbb\n", 0),
        ):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as written:
                written.write(text)
            os.utime(path, (time.time() - age, time.time() - age))
        os.symlink("grown", os.path.join(source, "link"))
        engines.copy_newer(source, target)
        self.assertEqual(read(os.path.join(target, "deep", "down", "new")), "new\n")
        self.assertEqual(read(os.path.join(target, "grown")), "old\nmore\n")
        self.assertEqual(read(os.path.join(target, "same")), "bbbb\n")
        self.assertEqual(sorted(os.listdir(target)), ["deep", "grown", "same"])
        self.assertEqual(sorted(os.listdir(source)), ["deep", "grown", "link", "same"])


class ClaudeTest(EngineCase):
    def start(self, *extra, **changes):
        status, called = self.agent("claude", *extra, **changes)
        self.assertEqual(called[0], "--append-system-prompt")
        self.assertIn("book-view", called[1])
        if called[2:3] == ["--settings"]:
            self.assertEqual(called[3], engines.claude_settings(self.report()))
            return status, called[4:]
        return status, called[2:]

    def report(self, pid=None):
        return engines.claim_file(os.getcwd(), "claude", f"opened.{pid or os.getpid()}")

    def project(self, *parts):
        """Where the fake keeps the conversations of the folder it runs in."""
        name = re.sub("[^A-Za-z0-9]", "-", os.getcwd())
        return os.path.join(self.config, "projects", name, *parts)

    def in_home(self):
        found = []
        for folder, _folders, names in os.walk(self.config):
            found += [os.path.join(folder, name) for name in names]
        return found

    def test_first_start_opens_a_conversation_whose_transcript_moves_in(self):
        _status, (option, session) = self.start()
        self.assertEqual(option, "--session-id")
        self.assertEqual(str(uuid.UUID(session)), session)
        self.assertEqual(read(self.kept("claude", "session")), session + "\n")
        self.assertEqual(read(self.kept("claude", session + ".jsonl")), "first\n")
        self.assertEqual(read(self.kept("claude", session, "note")), "aside\n")
        self.assertEqual(self.in_home(), [self.project(session, "note")])
        self.assertFalse(os.path.exists(self.report()))

    def test_a_claude_that_tells_nothing_is_still_followed_at_its_end(self):
        _status, (_option, session) = self.start(FAKE_HOOKLESS="1")
        self.assertEqual(read(self.kept("claude", session + ".jsonl")), "first\n")
        self.assertEqual(read(self.kept("claude", session, "note")), "aside\n")

    def test_the_next_start_resumes_the_transcript_in_the_folder(self):
        _status, (_option, session) = self.start()
        _status, called = self.start()
        path = self.kept("claude", session + ".jsonl")
        self.assertEqual(called, ["--resume", path])
        self.assertEqual(read(path), "first\nmore\n")
        self.assertEqual(self.in_home(), [self.project(session, "note")])

    def test_a_folder_moved_goes_on_where_it_stopped(self):
        _status, (_option, session) = self.start()
        moved = os.path.join(self.scratch, "elsewhere")
        os.rename(self.book, moved)
        os.chdir(moved)
        _status, called = self.start()
        self.assertEqual(
            called,
            [
                "--resume",
                os.path.join(moved, ".book-editor/claude", session + ".jsonl"),
            ],
        )

    def test_a_transcript_left_in_claudes_home_is_taken_over(self):
        session = str(uuid.uuid4())
        engines.write_session(self.book, "claude", session)
        folder = os.path.join(self.config, "projects", "-a-book")
        os.makedirs(folder)
        with open(os.path.join(folder, session + ".jsonl"), "w") as target:
            target.write("left\n")
        _status, called = self.start()
        self.assertEqual(called[0], "--resume")
        self.assertEqual(read(self.kept("claude", session + ".jsonl")), "left\nmore\n")

    def test_an_id_without_a_transcript_is_replaced_not_reused(self):
        unused = str(uuid.uuid4())
        engines.write_session(self.book, "claude", unused)
        _status, (option, session) = self.start()
        self.assertEqual(option, "--session-id")
        self.assertNotEqual(session, unused)

    def test_the_conversation_of_an_earlier_version_is_taken_over(self):
        session = str(uuid.uuid4())
        earlier = os.path.join(
            os.environ["XDG_STATE_HOME"],
            "book-editor",
            "sessions",
            engines.folder_key(self.book),
        )
        os.makedirs(earlier)
        with open(os.path.join(earlier, "session"), "w") as target:
            target.write(session + "\n")
        folder = os.path.join(self.config, "projects", "-a-book")
        os.makedirs(folder)
        with open(os.path.join(folder, session + ".jsonl"), "w") as target:
            target.write("earlier\n")
        _status, called = self.start()
        self.assertEqual(called, ["--resume", self.kept("claude", session + ".jsonl")])
        self.assertEqual(read(self.kept("claude", "session")), session + "\n")
        self.assertEqual(read(self.kept(".gitignore")), "*\n")

    def test_what_claude_keeps_beside_the_transcript_is_copied_in_as_it_grows(self):
        _status, (_option, session) = self.start()
        agent = self.project(session, "subagents", "agent-1.jsonl")
        os.makedirs(os.path.dirname(agent))
        with open(agent, "w") as target:
            target.write("sub\n")
        self.start()
        kept = self.kept("claude", session, "subagents", "agent-1.jsonl")
        self.assertEqual(read(kept), "sub\n")
        with open(agent, "a") as target:
            target.write("more\n")
        self.start()
        self.assertEqual(read(kept), "sub\nmore\n")
        self.assertEqual(read(agent), "sub\nmore\n")  # claude goes on using it

    def test_copies_of_a_folder_leave_each_others_files_alone(self):
        _status, (_option, session) = self.start()
        copy = os.path.join(self.scratch, "copy")
        shutil.copytree(self.book, copy, symlinks=True)
        os.chdir(copy)
        self.start()
        theirs = self.project(session, "theirs")
        os.makedirs(os.path.dirname(theirs))
        with open(theirs, "w") as target:
            target.write("of the copy\n")
        os.chdir(self.book)
        self.start()
        self.assertFalse(os.path.exists(self.kept("claude", session, "theirs")))
        self.assertEqual(read(theirs), "of the copy\n")

    def test_a_copy_of_the_folder_waits_while_the_originals_agent_runs(self):
        _status, (_option, session) = self.start()
        os.chdir(shutil.copytree(self.book, os.path.join(self.scratch, "copy")))
        guard = engines.hold("claude", session)
        try:
            _status, called = self.start()
        finally:
            os.close(guard)
        self.assertEqual(called, [])
        self.assertEqual(read(self.kept("claude", "session")), session + "\n")
        _status, called = self.start()
        self.assertEqual(called, ["--resume", self.kept("claude", session + ".jsonl")])

    def test_a_transcript_cut_short_on_the_way_in_is_not_taken_for_whole(self):
        session = str(uuid.uuid4())
        engines.write_session(self.book, "claude", session)
        left = os.path.join(self.config, "projects", "-a-book", session + ".jsonl")
        os.makedirs(os.path.dirname(left))
        with open(left, "w") as target:
            target.write("left\n")
        with self.another_filesystem(), self.full_disk():
            _status, called = self.start()
        self.assertEqual(called, [])  # no memory this time
        self.assertEqual(os.listdir(self.kept("claude")), ["session"])
        self.assertEqual(read(left), "left\n")
        with self.another_filesystem():
            _status, called = self.start()
        self.assertEqual(called[0], "--resume")
        self.assertEqual(read(self.kept("claude", session + ".jsonl")), "left\nmore\n")
        self.assertEqual(self.in_home(), [])

    def test_a_conversation_opened_in_claude_becomes_the_folders(self):
        _status, (_option, first) = self.start()
        opened = str(uuid.uuid4())
        self.start(FAKE_CLEAR=opened)
        self.assertEqual(read(self.kept("claude", "session")), opened + "\n")
        self.assertEqual(read(self.kept("claude", opened + ".jsonl")), "cleared\n")
        self.assertTrue(os.path.isfile(self.kept("claude", first + ".jsonl")))
        self.assertEqual(self.in_home(), [self.project(first, "note")])
        _status, called = self.start()
        self.assertEqual(called, ["--resume", self.kept("claude", opened + ".jsonl")])

    def test_a_conversation_an_agent_of_another_folder_has_is_not_taken(self):
        _status, (_option, first) = self.start()
        opened = str(uuid.uuid4())
        guard = engines.hold("claude", opened)
        try:
            self.start(FAKE_CLEAR=opened)
        finally:
            os.close(guard)
        self.assertEqual(read(self.kept("claude", "session")), first + "\n")
        self.assertTrue(os.path.isfile(self.project(opened + ".jsonl")))

    def test_the_hook_finds_the_agents_file_under_any_name(self):
        os.environ["XDG_STATE_HOME"] = os.path.join(self.scratch, 'it\'s "a" $HOME')
        opened = str(uuid.uuid4())
        self.start(FAKE_CLEAR=opened)
        self.assertEqual(read(self.kept("claude", "session")), opened + "\n")

    def test_what_an_agent_that_died_was_told_is_not_lost(self):
        self.start()
        opened = str(uuid.uuid4())
        with open(self.project(opened + ".jsonl"), "w") as target:
            target.write("told\n")
        told = {"session": opened, "transcript": self.project(opened + ".jsonl")}
        with open(self.report(999), "w") as target:
            target.write(json.dumps(told) + "\n")
        _status, called = self.start()
        self.assertEqual(called, ["--resume", self.kept("claude", opened + ".jsonl")])
        self.assertFalse(os.path.exists(self.report(999)))

    def test_the_hook_says_nothing_and_what_it_cannot_read_does_not_count(self):
        os.makedirs(os.path.dirname(self.report()))
        report = self.report()
        session = str(uuid.uuid4())
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            for told in ("", "[]", "{}", '{"session_id": 5}', '{"session_id": "x"}'):
                with mock.patch("sys.stdin", io.StringIO(told)):
                    self.assertEqual(engines.hook_main([report]), 0)
            self.assertFalse(os.path.exists(report))  # no agent: nothing
            with open(report, "w") as target:
                target.write("not json\n[]\n")
            for told in ('{"session_id": 5}', '{"session_id": "x"}'):
                with mock.patch("sys.stdin", io.StringIO(told)):
                    self.assertEqual(engines.hook_main([report]), 0)
            self.assertIsNone(engines.last_report(report))
            told = {"session_id": session.upper(), "transcript_path": "relative.jsonl"}
            with mock.patch("sys.stdin", io.StringIO(json.dumps(told))):
                self.assertEqual(engines.hook_main([report]), 0)
            self.assertEqual(engines.last_report(report), (session, None))
            told = {
                "session_id": session,
                "transcript_path": f"/home/x/{session}.jsonl",
            }
            with mock.patch("sys.stdin", io.StringIO(json.dumps(told))):
                self.assertEqual(engines.hook_main([report]), 0)
            self.assertEqual(engines.last_report(report), (session, "/home/x"))
            self.assertEqual(engines.hook_main([]), 0)
        self.assertEqual(said.getvalue(), "")
        self.assertIsNone(engines.last_report(report + ".none"))

    def test_a_damaged_record_starts_a_new_conversation(self):
        engines.write_word(self.book, "claude/session", "not an id")
        _status, (option, _session) = self.start()
        self.assertEqual(option, "--session-id")

    def test_each_folder_has_its_own_conversation(self):
        _status, (_option, first) = self.start()
        other = os.path.join(self.scratch, "other")
        os.makedirs(other)
        os.chdir(other)
        _status, (option, second) = self.start()
        self.assertEqual(option, "--session-id")
        self.assertNotEqual(first, second)

    def test_a_second_agent_on_the_folder_gets_a_conversation_of_its_own(self):
        _status, (_option, session) = self.start()
        with self.another_agent("claude"):
            _status, called = self.start()
        self.assertEqual(called, [])
        self.assertEqual(read(self.kept("claude", "session")), session + "\n")
        _status, called = self.start()  # the other one ended
        self.assertEqual(called[0], "--resume")

    def test_without_a_place_for_claims_claude_still_starts(self):
        blocked = os.path.join(self.scratch, "file")
        with open(blocked, "w"):
            pass
        _status, called = self.start(XDG_STATE_HOME=blocked)
        self.assertEqual(called, [])

    @unittest.skipIf(os.geteuid() == 0, "root writes anywhere")
    def test_a_folder_that_takes_no_record_still_gets_claude(self):
        os.chmod(self.book, 0o500)
        self.addCleanup(os.chmod, self.book, 0o700)
        _status, called = self.start()
        self.assertEqual(called, [])

    def test_more_arguments_are_handed_on(self):
        _status, called = self.start("--model", "opus")
        self.assertEqual(called[2:], ["--model", "opus"])
        self.assertEqual(called[0], "--session-id")

    def test_the_engines_exit_status_is_ours(self):
        status, _called = self.start(FAKE_STATUS="3")
        self.assertEqual(status, 3)

    def test_a_missing_engine_is_reported(self):
        os.environ["BOOK_CLAUDE"] = os.path.join(self.scratch, "nowhere")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            status, called = self.agent("claude")
        self.assertEqual((status, called), (127, None))
        self.assertIn("cannot run", errors.getvalue())
        self.assertIn("BOOK_CLAUDE", errors.getvalue())


class CodexTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.session = str(uuid.uuid4())
        self.name = f"rollout-2026-11-14T10-00-00-{self.session}.jsonl"
        self.link = os.path.join(self.home, "sessions/2026/11/14", self.name)
        os.environ["FAKE_ID"] = self.session
        watch = mock.patch.object(engines, "WATCH_SECONDS", 0.05)
        watch.start()
        self.addCleanup(watch.stop)

    def rollout(self, session, source="user", cwd=None):
        """A rollout in Codex's home that nothing has open."""
        day = os.path.dirname(self.link)
        os.makedirs(day, exist_ok=True)
        path = os.path.join(day, f"rollout-2026-11-14T10-00-00-{session}.jsonl")
        header = {"type": "session_meta"}
        header["payload"] = {
            "id": session,
            "cwd": cwd or self.book,
            "thread_source": source,
        }
        with open(path, "w") as target:
            target.write(json.dumps(header) + "\n")
        return path

    def start(self, *extra, **changes):
        return self.agent("codex", *extra, **changes)

    def test_first_start_opens_a_conversation_whose_rollout_moves_in(self):
        _status, called = self.start()
        self.assertEqual(called, [])
        self.assertEqual(read(self.kept("codex", "session")), self.session + "\n")
        self.assertTrue(os.path.isfile(self.kept("codex", self.name)))
        self.assertFalse(os.path.islink(self.kept("codex", self.name)))
        self.assertEqual(os.readlink(self.link), self.kept("codex", self.name))

    def test_the_next_start_resumes_and_codex_writes_into_the_folder(self):
        self.start()
        before = read(self.kept("codex", self.name))
        _status, called = self.start()
        self.assertEqual(called, ["resume", self.session])
        self.assertEqual(read(self.kept("codex", self.name)), before + "more\n")
        self.assertTrue(os.path.islink(self.link))

    def test_the_conversation_is_found_while_codex_runs(self):
        self.start()
        self.assertEqual(read(self.out + ".seen"), self.session + "\n")

    def test_a_conversation_opened_in_codex_becomes_the_folders(self):
        self.start()
        opened = str(uuid.uuid4())
        _status, called = self.start(FAKE_NEW=opened)
        self.assertEqual(called, ["resume", self.session])
        self.assertEqual(read(self.out + ".seen"), opened + "\n")
        name = f"rollout-2026-11-14T10-00-00-{opened}.jsonl"
        self.assertEqual(
            os.readlink(os.path.join(os.path.dirname(self.link), name)),
            self.kept("codex", name),
        )
        self.assertTrue(os.path.isfile(self.kept("codex", self.name)))
        os.environ.pop("FAKE_NEW")
        _status, called = self.start()
        self.assertEqual(called, ["resume", opened])

    def test_a_folder_moved_goes_on_where_it_stopped(self):
        self.start()
        moved = os.path.join(self.scratch, "elsewhere")
        os.rename(self.book, moved)
        os.chdir(moved)
        _status, called = self.start()
        self.assertEqual(called, ["resume", self.session])
        kept = os.path.join(moved, ".book-editor/codex", self.name)
        self.assertEqual(os.readlink(self.link), kept)
        self.assertTrue(read(kept).endswith("more\n"))

    def test_a_home_that_never_saw_the_conversation_gets_the_link(self):
        self.start()
        shutil.rmtree(self.home)
        _status, called = self.start()
        self.assertEqual(called, ["resume", self.session])
        self.assertEqual(os.readlink(self.link), self.kept("codex", self.name))

    def test_a_rollout_codex_put_in_place_of_the_link_moves_in(self):
        self.start()
        os.unlink(self.link)
        with open(self.link, "w") as target:
            target.write("rewritten\n")
        _status, called = self.start()
        self.assertEqual(called, ["resume", self.session])
        self.assertEqual(read(self.kept("codex", self.name)), "rewritten\nmore\n")
        self.assertTrue(os.path.islink(self.link))

    def test_a_record_without_a_rollout_starts_a_new_conversation(self):
        engines.write_session(self.book, "codex", str(uuid.uuid4()))
        _status, called = self.start()
        self.assertEqual(called, [])
        self.assertEqual(read(self.kept("codex", "session")), self.session + "\n")

    def test_a_link_to_a_rollout_that_is_gone_is_removed(self):
        self.start()
        os.unlink(self.kept("codex", self.name))
        os.environ["FAKE_ID"] = str(uuid.uuid4())
        _status, called = self.start()
        self.assertEqual(called, [])
        self.assertFalse(os.path.lexists(self.link))

    def test_what_codex_spawned_is_not_the_conversation(self):
        self.start(FAKE_SOURCE="subagent")
        self.assertFalse(os.path.exists(self.kept("codex", "session")))

    def test_what_codex_opens_after_the_last_look_is_not_followed(self):
        with mock.patch.object(engines, "WATCH_SECONDS", 30):
            self.start(FAKE_SLEEP="0")
        self.assertFalse(os.path.exists(self.kept("codex", "session")))
        self.assertTrue(os.path.isfile(self.link))  # stays Codex's

    def copy_of_the_book(self):
        copy = os.path.join(self.scratch, "copy")
        shutil.copytree(self.book, copy, symlinks=True)
        return copy

    def test_a_copy_of_the_folder_waits_while_the_originals_agent_runs(self):
        self.start()
        os.chdir(self.copy_of_the_book())
        guard = engines.hold("codex", self.session)
        try:
            _status, called = self.start(FAKE_ID=str(uuid.uuid4()))
        finally:
            os.close(guard)
        self.assertEqual(called, [])
        self.assertEqual(read(self.kept("codex", "session")), self.session + "\n")
        self.assertEqual(
            os.readlink(self.link),
            os.path.join(self.book, ".book-editor/codex", self.name),
        )

    def test_a_copy_of_the_folder_goes_on_by_itself_once_that_agent_ended(self):
        self.start()
        original = read(self.kept("codex", self.name))
        os.chdir(self.copy_of_the_book())
        _status, called = self.start()
        self.assertEqual(called, ["resume", self.session])
        self.assertEqual(os.readlink(self.link), self.kept("codex", self.name))
        self.assertEqual(read(self.kept("codex", self.name)), original + "more\n")
        os.chdir(self.book)
        self.assertEqual(read(self.kept("codex", self.name)), original)

    def test_a_record_of_another_folders_conversation_starts_a_new_one(self):
        self.start()
        theirs = self.kept("codex", self.name)
        other = os.path.join(self.scratch, "other")
        os.makedirs(other)
        engines.write_session(other, "codex", self.session)
        os.chdir(other)
        opened = str(uuid.uuid4())
        _status, called = self.start(FAKE_ID=opened)
        self.assertEqual(called, [])
        self.assertEqual(os.readlink(self.link), theirs)
        self.assertEqual(read(self.kept("codex", "session")), opened + "\n")

    def test_a_link_that_is_not_the_editors_is_left_alone(self):
        self.start()
        mine = os.path.join(self.scratch, "mine", self.name)
        os.makedirs(os.path.dirname(mine))
        shutil.copy(self.kept("codex", self.name), mine)
        os.unlink(self.link)
        os.symlink(mine, self.link)
        _status, called = self.start(FAKE_ID=str(uuid.uuid4()))
        self.assertEqual(called, [])
        self.assertEqual(os.readlink(self.link), mine)

    def test_a_conversation_another_folders_agent_has_is_not_recorded(self):
        guard = engines.hold("codex", self.session)
        try:
            self.start()
        finally:
            os.close(guard)
        self.assertFalse(os.path.exists(self.kept("codex", "session")))

    def test_a_conversation_codex_does_not_have_open_is_not_ours(self):
        other = self.rollout(str(uuid.uuid4()))  # of an agent off the record
        os.utime(other, (time.time() + 60, time.time() + 60))
        self.start()
        self.assertEqual(read(self.kept("codex", "session")), self.session + "\n")
        self.assertFalse(os.path.islink(other))
        self.start(FAKE_SOURCE="subagent", FAKE_ID=str(uuid.uuid4()))
        self.assertEqual(read(self.kept("codex", "session")), self.session + "\n")

    def test_of_the_conversations_open_the_one_written_last_counts(self):
        first, last = str(uuid.uuid4()), str(uuid.uuid4())
        paths = [self.rollout(first), self.rollout(last), self.rollout(self.session)]
        os.utime(paths[0], (100, 100))
        os.utime(paths[1], (300, 300))
        os.utime(paths[2], (200, 200))
        with contextlib.ExitStack() as held:
            for path in paths:
                held.enter_context(open(path, "a"))
            self.assertEqual(engines.current_codex(os.getpid()), last)
        self.assertIsNone(engines.current_codex(os.getpid()))

    def test_what_a_child_of_codex_has_open_counts_as_well(self):
        path = self.rollout(self.session)
        script = 'exec 3>>"$1"; sleep 30 & echo; wait'
        with subprocess.Popen(
            ["/bin/sh", "-c", script, "sh", path],
            stdout=subprocess.PIPE,
            start_new_session=True,
        ) as child:
            try:
                child.stdout.readline()  # the grandchild runs, the rollout open
                self.assertEqual(engines.current_codex(child.pid), self.session)
                self.assertEqual(len(engines.family(child.pid)), 2)
            finally:
                os.killpg(child.pid, signal.SIGKILL)

    def test_a_rollout_without_a_header_is_passed_over(self):
        path = self.rollout(self.session)
        for text in ("", "not json\n", "[]\n", '{"type": "other"}\n'):
            with open(path, "w") as target:
                target.write(text)
                target.flush()
                self.assertEqual(engines.rollout_header(path), {})
                self.assertIsNone(engines.current_codex(os.getpid()))

    def test_a_rollout_named_for_another_conversation_is_passed_over(self):
        path = self.rollout(self.session)
        header = {"type": "session_meta"}
        header["payload"] = {"id": str(uuid.uuid4()), "thread_source": "user"}
        with open(path, "w") as target:
            target.write(json.dumps(header) + "\n")
            target.flush()
            self.assertIsNone(engines.current_codex(os.getpid()))

    def test_the_editors_tools_come_as_a_server_of_codex(self):
        _status, called = self.start(BOOK_EDITOR_SOCKET='/run/a "b"/socket')
        server = os.path.join(engines.TOOLS_DIR, "book-mcp")
        self.assertEqual(
            called,
            [
                "-c",
                f'mcp_servers.book_editor.command="{server}"',
                "-c",
                'mcp_servers.book_editor.env={BOOK_EDITOR_SOCKET="/run/a \\"b\\"/socket"}',
            ],
        )
        self.assertTrue(os.access(server, os.X_OK))

    def test_resuming_keeps_the_tools_and_more_arguments(self):
        self.start()
        _status, called = self.start("--search", BOOK_EDITOR_SOCKET="/run/socket")
        self.assertEqual(called[0], "resume")
        self.assertEqual(called[-2:], [self.session, "--search"])
        self.assertEqual(len(called), 7)

    def test_a_second_agent_on_the_folder_gets_a_conversation_of_its_own(self):
        self.start()
        other = str(uuid.uuid4())
        with self.another_agent("codex"):
            _status, called = self.start(FAKE_ID=other)
        self.assertEqual(called, [])
        self.assertEqual(read(self.kept("codex", "session")), self.session + "\n")

    @unittest.skipIf(os.geteuid() == 0, "root writes anywhere")
    def test_a_home_that_takes_no_link_gets_a_new_conversation(self):
        self.start()
        os.unlink(self.link)
        os.chmod(os.path.dirname(self.link), 0o500)
        self.addCleanup(os.chmod, os.path.dirname(self.link), 0o700)
        _status, called = self.start(FAKE_ID=str(uuid.uuid4()))
        self.assertEqual(called, [])


class CommandTest(EngineCase):
    def test_a_command_on_the_path_is_taken_as_named(self):
        self.assertEqual(engines.find_command("sh"), "sh")

    def test_a_path_is_taken_as_given(self):
        self.assertEqual(engines.find_command("/nowhere/claude"), "/nowhere/claude")

    def test_a_users_own_install_is_found_off_the_path(self):
        local = os.path.join(os.environ["HOME"], ".local", "bin")
        os.makedirs(local)
        shutil.copy(os.environ["BOOK_CLAUDE"], os.path.join(local, "claude-of-mine"))
        self.assertEqual(
            engines.find_command("claude-of-mine"),
            os.path.join(local, "claude-of-mine"),
        )

    def test_a_command_found_nowhere_is_left_to_fail_by_name(self):
        self.assertEqual(engines.find_command("claude-of-nobody"), "claude-of-nobody")


if __name__ == "__main__":
    unittest.main()
