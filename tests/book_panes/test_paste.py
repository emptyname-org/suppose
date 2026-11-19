"""Formatted clipboard text pasted as Markdown: conversion and the paste."""

import os
import subprocess
import time
import unittest
from unittest import mock

from gi.repository import Gio, GLib

from book_panes import paste


def markdown(html):
    """What the pandoc of the paste makes of the HTML."""
    done = subprocess.run(
        paste.pandoc_command(),
        input=paste.prepare(html).encode(),
        capture_output=True,
        check=True,
    )
    return done.stdout.decode().strip()


def spin(condition, seconds=10.0):
    """Serve the main loop until the condition holds."""
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > end:
            raise AssertionError(f"condition not met in {seconds:g} s")
        context.iteration(False)
        time.sleep(0.005)


def settle(seconds=0.3):
    """Serve the main loop for a while: for what must not happen."""
    context = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        context.iteration(False)
        time.sleep(0.005)


class MarkdownTest(unittest.TestCase):
    def test_headings_lists_emphasis_and_links_become_markdown(self):
        html = (
            "<meta charset='utf-8'><html><body><!--StartFragment-->"
            '<h2 style="color:red" class="t">A <em>heading</em></h2>'
            "<p>First <strong>bold</strong>, <i>italic</i>, <del>gone</del>, "
            '<a href="https://example.org/a_b?x=1&amp;y=2" target="_blank">a link'
            "</a>, <code>inline()</code>, 5 * 3, [x] &amp; &lt;y&gt;.</p>"
            "<ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul>"
            '<ol start="3"><li>three</li></ol><blockquote><p>Quoted</p></blockquote>'
            "<hr><!--EndFragment--></body></html>"
        )
        self.assertEqual(
            markdown(html),
            "## A *heading*\n\n"
            "First **bold**, *italic*, ~~gone~~, "
            "[a link](https://example.org/a_b?x=1&y=2), `inline()`, 5 \\* 3, "
            "\\[x\\] & \\<y\\>.\n\n"
            "- one\n  - nested\n- two\n\n"
            "3.  three\n\n"
            "> Quoted\n\n" + "-" * 72,
        )

    def test_code_keeps_its_text_between_fences(self):
        self.assertEqual(
            markdown("<p>t</p><pre>a &lt;b&gt;\n  *c*</pre>"),
            "t\n\n```\na <b>\n  *c*\n```",
        )
        self.assertEqual(
            markdown(
                '<pre><code class="hljs language-js"><span class="k">const</span>'
                " a;\n</code></pre>"
            ),
            "``` js\nconst a;\n```",
        )
        self.assertEqual(
            markdown('<PRE>\n <CODE class="language-sh">ls</CODE></PRE>'),
            "``` sh\nls\n```",
        )
        self.assertEqual(
            markdown("<ul><li>item<pre>x = `y` ```z```</pre></li></ul>"),
            "- item\n\n  ````\n  x = `y` ```z```\n  ````",
        )
        self.assertEqual(markdown("<pre></pre><p>after</p>"), "after")
        self.assertEqual(
            markdown("<pre>open <pre>in</pre> out</pre>"), "```\nopen in\n```\n\nout"
        )
        self.assertEqual(markdown("<pre>never closed"), "```\nnever closed\n```")

    def test_tables_become_pipe_tables_whatever_their_cells_hold(self):
        self.assertEqual(
            markdown(
                "<table><thead><tr><th>Name</th><th>Value</th></tr></thead>"
                "<tbody><tr><td>a|b</td><td><b>1</b></td></tr></tbody></table>"
            ),
            "| Name | Value |\n|------|-------|\n| a\\|b | **1** |",
        )
        self.assertEqual(
            markdown(
                '<table><tr><th colspan="2">Head</th></tr><tr><td><p>x</p><ul>'
                '<li>l</li></ul></td><td rowspan="2">y<br>z</td></tr>'
                "<tr><td>w</td></tr></table>"
            ),
            "| Head |     |\n|------|-----|\n| x l  | y z |\n| w    |     |",
        )
        self.assertEqual(
            markdown("<table><tr><td><pre>a\n b</pre> c</td><td>d|e</td></tr></table>"),
            "|         |      |\n|---------|------|\n| `a b` c | d\\|e |",
        )

    def test_what_a_style_says_markdown_says(self):
        html = (
            '<b style="font-weight:normal;" id="docs-internal-guid-1">'
            '<h1><span style="font-weight:400">Title</span></h1><p>'
            '<span style="font-weight:400">Plain </span>'
            '<span style="FONT-WEIGHT: 700">bold</span>'
            '<span style="font-style:italic;font-weight:bold"> both</span>'
            '<span style="font-weight:700"> </span>'
            '<span style="font-style:italic">slanted</span></p></b>'
        )
        self.assertEqual(
            markdown(html), "# Title\n\nPlain **bold** ***both*** *slanted*"
        )
        self.assertEqual(
            markdown(
                '<p><strong>a <span style="font-weight:700">b</span> <b>c</b>'
                "</strong> <em><i>d</i></em></p>"
            ),
            "**a b c** *d*",
        )

    def test_wrappers_scripts_and_styles_leave_no_trace(self):
        html = (
            '<div class="w"><div><span class="s">text</span> <u>u</u> '
            "<mark>m</mark></div><script>alert(1)</script><style>p{}</style>"
            "<!-- c --><p>x<sup>2</sup> and more</p></div>"
        )
        self.assertEqual(markdown(html), "text *u* m\n\nx² and more")

    def test_images_stay_links_but_embedded_ones_go(self):
        html = (
            '<p><img src="https://example.org/i.png" alt="pic"> '
            '<img src="data:image/png;base64,AAAA" alt="inline"></p>'
        )
        self.assertEqual(markdown(html), "![pic](https://example.org/i.png) inline")

    def test_links_to_programs_lose_their_target(self):
        html = (
            '<p><a href="#part">part</a> <a href=" JavaScript:x()">js</a> '
            '<a href="java\tscript:x">tab</a> <a href="vbscript:x">vb</a> '
            '<a href="data:text/html,x">data</a> '
            '<a href="mailto:a@example.org">mail</a></p>'
        )
        self.assertEqual(
            markdown(html), "[part](#part) js tab vb data [mail](mailto:a@example.org)"
        )

    def test_gaps_and_empty_headings_go(self):
        html = "<p>a</p><br><p><br> </p><h1></h1><p>b<br>c</p>"
        self.assertEqual(markdown(html), "a\n\nb\\\nc")

    def test_plain_text_as_html_stays_text(self):
        self.assertEqual(markdown("just text &amp; more"), "just text & more")
        self.assertEqual(markdown(""), "")

    def test_the_clipboards_bytes_are_read_as_they_say(self):
        self.assertEqual(paste.decode("<p>é</p>".encode()), "<p>é</p>")
        self.assertEqual(paste.decode(b"\xef\xbb\xbf<p>x</p>"), "<p>x</p>")
        for name in ("utf-16-le", "utf-16-be"):
            data = "﻿<p>é</p>".encode(name)
            self.assertEqual(paste.decode(data), "<p>é</p>")
        self.assertEqual(paste.decode(b"<p>x</p>\0\0"), "<p>x</p>")
        self.assertEqual(paste.decode(b"a\xffb"), "a�b")

    def test_html_is_prepared_in_one_pass(self):
        self.assertEqual(paste.prepare("no pre"), "no pre")
        self.assertEqual(paste.prepare("</pre>stray"), "</pre>stray")
        self.assertEqual(
            paste.prepare('<Pre class="x">a</PRE >'),
            '<Pre class="x"><code>a</code></PRE >',
        )
        self.assertEqual(
            paste.prepare("<pre>  <code>a</code></pre>"), "<pre><code>a</code></pre>"
        )
        started = time.monotonic()
        paste.prepare("<pre " * 400000 + ("<pre>" + " " * 40) * 40000)
        self.assertLess(time.monotonic() - started, 5)

    def test_without_pandoc_there_is_no_command(self):
        with mock.patch.object(GLib, "find_program_in_path", return_value=None):
            self.assertIsNone(paste.pandoc_command())


class ConversionTest(unittest.TestCase):
    def convert(self, html, **options):
        told = []
        conversion = paste.Conversion(html, lambda *said: told.append(said), **options)
        self.assertEqual(told, [])  # never from within
        return conversion, told

    def test_pandoc_converts(self):
        _conversion, told = self.convert("<h1>T</h1><p>a <b>b</b></p>")
        spin(lambda: told)
        self.assertEqual(told, [(paste.CONVERTED, "# T\n\na **b**")])

    def test_output_that_is_no_text_is_still_text(self):
        command = ["/bin/sh", "-c", "cat >/dev/null; printf 'a\\377b\\n'"]
        _conversion, told = self.convert("x", command=command)
        spin(lambda: told)
        self.assertEqual(told, [(paste.CONVERTED, "a�b")])

    def test_a_pandoc_that_fails_or_says_nothing_has_failed(self):
        for script in ("cat >/dev/null; echo half; exit 3", "cat >/dev/null", "exit 0"):
            _conversion, told = self.convert("x", command=["/bin/sh", "-c", script])
            spin(lambda told=told: told)
            self.assertEqual(told, [(paste.FAILED, None)], script)

    def test_a_pandoc_that_cannot_start_has_failed(self):
        _conversion, told = self.convert("x", command=["/nonexistent/pandoc"])
        spin(lambda: told)
        self.assertEqual(told, [(paste.FAILED, None)])
        with mock.patch.object(paste, "pandoc_command", return_value=None):
            _conversion, told = self.convert("x")
        spin(lambda: told)
        self.assertEqual(told, [(paste.FAILED, None)])

    def test_a_pandoc_that_takes_too_long_is_ended(self):
        command = ["/bin/sh", "-c", "exec sleep 30"]
        conversion, told = self.convert("x", command=command, seconds=0.2)
        started = time.monotonic()
        spin(lambda: told)
        self.assertEqual(told, [(paste.LATE, None)])
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(conversion.process.get_if_signaled())
        self.assertEqual(conversion.timer, 0)

    def test_a_cancelled_conversion_tells_nothing_and_leaves_no_process(self):
        command = ["/bin/sh", "-c", "exec sleep 30"]
        conversion, told = self.convert("x", command=command)
        conversion.cancel()
        spin(lambda: conversion.process.get_identifier() is None)  # ended
        settle()
        self.assertTrue(conversion.process.get_if_signaled())
        self.assertEqual(told, [])
        self.assertEqual(conversion.timer, 0)
        conversion, told = self.convert("x", command=["/nonexistent/pandoc"])
        conversion.cancel()
        settle()
        self.assertEqual(told, [])


class Buffer:
    """A text buffer as far as a paste goes."""

    def __init__(self):
        self.text = "before [selected] after"
        self.selection = "[selected]"
        self.steps = []
        self.paste_ongoing = False
        self.completing = []

    def begin_user_action(self):
        self.steps.append("begin")

    def end_user_action(self):
        self.steps.append("end")

    def delete_selection(self, interactive, editable):
        self.steps.append("delete")
        self.text = self.text.replace(self.selection, "|")

    def insert_at_cursor(self, text, length):
        self.steps.append("insert")
        self.completing.append(not self.paste_ongoing)
        self.text = self.text.replace("|", text)

    def get_insert(self):
        return "insert-mark"


class Clipboard:
    """A clipboard: what it offers, and how it hands its HTML over."""

    def __init__(self, html=b"<p>a <b>b</b></p>", kinds=("text/html", "text/plain")):
        self.kinds = kinds
        self.stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(html))
        self.refuses = False
        self.asked = 0

    def get_formats(self):
        return self

    def contain_mime_type(self, kind):
        return kind in self.kinds

    def read_async(self, kinds, _priority, _cancellable, callback, data):
        self.asked += 1
        assert kinds == ["text/html"]
        GLib.idle_add(lambda: callback(self, None, data) and False)

    def read_finish(self, _result):
        if self.refuses:
            raise GLib.GError("refused")
        return self.stream, "text/html"


class View:
    def __init__(self, clipboard):
        self.clipboard = clipboard
        self.buffer = Buffer()
        self.editable = True
        self.emitted = []
        self.beeps = 0
        self.scrolled = []

    def get_editable(self):
        return self.editable

    def get_clipboard(self):
        return self.clipboard

    def get_buffer(self):
        return self.buffer

    def get_display(self):
        return self

    def beep(self):
        self.beeps += 1

    def emit(self, name):
        self.emitted.append(name)

    def scroll_mark_onscreen(self, mark):
        self.scrolled.append(mark)


class Converter:
    """Stands in for pandoc: tells what the test says, when it says."""

    def __init__(self):
        self.asked = []
        self.cancelled = 0

    def __call__(self, html, done):
        self.asked.append(html)
        self.done = done
        return self

    def cancel(self):
        self.cancelled += 1


class PasteTest(unittest.TestCase):
    def setUp(self):
        self.document = ["file:///book/one.md"]
        self.clipboard = Clipboard()
        self.view = View(self.clipboard)
        self.converter = Converter()
        self.paste = paste.MarkdownPaste(
            self.view, lambda: self.document[0], self.converter
        )
        self.addCleanup(self.paste.close)

    def untouched(self):
        return self.view.buffer.steps == [] and self.view.emitted == []

    def idle(self):
        subject = self.paste
        return (
            subject.reading is None
            and subject.stream is None
            and subject.conversion is None
            and subject.timer == 0
            and subject.chunks == []
        )

    def test_html_arrives_as_markdown_in_one_undo_step(self):
        subject = paste.MarkdownPaste(self.view, lambda: self.document[0])
        self.addCleanup(subject.close)
        subject.paste()
        spin(lambda: self.view.buffer.steps)
        buffer = self.view.buffer
        self.assertEqual(buffer.text, "before a **b** after")
        self.assertEqual(buffer.steps, ["begin", "delete", "insert", "end"])
        self.assertEqual(buffer.completing, [False])  # Apostrophe completes nothing
        self.assertFalse(buffer.paste_ongoing)
        self.assertEqual(self.view.scrolled, ["insert-mark"])
        self.assertEqual((self.view.emitted, self.view.beeps), ([], 0))
        self.assertIsNone(subject.reading)
        self.assertTrue(self.clipboard.stream.is_closed())

    def test_html_read_in_parts_is_put_together(self):
        html = b"<p>" + b"word " * 2000 + b"</p>"
        self.clipboard.stream = Gio.MemoryInputStream.new_from_bytes(
            GLib.Bytes.new(html)
        )
        with mock.patch.object(paste, "READ_CHUNK", 1000):
            self.paste.paste()
            spin(lambda: self.converter.asked)
        self.assertEqual(self.converter.asked, [html.decode()])
        self.assertEqual(self.paste.timer, 0)  # the clipboard was in time

    def test_a_clipboard_without_html_is_pasted_plain_at_once(self):
        self.clipboard.kinds = ("text/plain",)
        self.paste.paste()
        self.assertEqual(self.view.emitted, ["paste-clipboard"])
        self.assertEqual((self.clipboard.asked, self.view.buffer.steps), (0, []))
        self.assertTrue(self.idle())

    def test_an_editor_that_takes_no_text_gets_none(self):
        self.view.editable = False
        self.paste.paste()
        settle(0.1)
        self.assertTrue(self.untouched())
        self.assertEqual(self.clipboard.asked, 0)

    def test_html_the_clipboard_does_not_hand_over_is_pasted_plain(self):
        self.clipboard.refuses = True
        self.paste.paste()
        spin(lambda: self.view.emitted)
        self.assertEqual(self.view.emitted, ["paste-clipboard"])
        self.assertEqual(self.converter.asked, [])
        self.assertTrue(self.idle())

    def test_html_that_breaks_off_is_pasted_plain(self):
        self.clipboard.stream.close(None)  # every read of it fails
        self.paste.paste()
        spin(lambda: self.view.emitted)
        self.assertEqual(self.view.emitted, ["paste-clipboard"])
        self.assertEqual(self.converter.asked, [])
        self.assertTrue(self.idle())

    def test_more_html_than_pandoc_is_given_is_pasted_plain(self):
        with mock.patch.object(paste, "MAX_HTML_BYTES", 5):
            self.paste.paste()
            spin(lambda: self.view.emitted)
        self.assertEqual(self.view.emitted, ["paste-clipboard"])
        self.assertEqual(self.converter.asked, [])
        self.assertTrue(self.idle())
        self.assertTrue(self.clipboard.stream.is_closed())

    def test_html_pandoc_cannot_convert_is_pasted_plain(self):
        self.paste.paste()
        spin(lambda: self.converter.asked)
        self.converter.done(paste.FAILED, None)
        self.assertEqual(self.view.emitted, ["paste-clipboard"])
        self.assertEqual((self.view.buffer.steps, self.view.beeps), ([], 0))
        self.assertTrue(self.idle())
        self.assertEqual(self.converter.cancelled, 0)

    def test_a_conversion_that_took_too_long_pastes_nothing(self):
        self.paste.paste()
        spin(lambda: self.converter.asked)
        self.converter.done(paste.LATE, None)
        self.assertTrue(self.untouched())
        self.assertEqual(self.view.beeps, 1)
        self.assertTrue(self.idle())

    def test_nothing_is_pasted_into_another_document(self):
        for outcome in ((paste.CONVERTED, "md"), (paste.FAILED, None)):
            self.clipboard.stream = Gio.MemoryInputStream.new_from_bytes(
                GLib.Bytes.new(b"<p>x</p>")
            )
            self.document[0] = "file:///book/one.md"
            self.paste.paste()
            spin(lambda: len(self.converter.asked) == self.clipboard.asked)
            self.document[0] = "file:///book/two.md"
            self.converter.done(*outcome)
            self.assertTrue(self.untouched(), outcome)
            self.assertTrue(self.idle())
        self.assertEqual(self.view.beeps, 2)

    def test_nothing_is_pasted_once_the_editor_takes_no_text(self):
        self.paste.paste()
        spin(lambda: self.converter.asked)
        self.view.editable = False
        self.converter.done(paste.CONVERTED, "md")
        self.assertTrue(self.untouched())
        self.assertEqual(self.view.beeps, 1)

    def test_a_second_paste_waits_for_the_first(self):
        self.paste.paste()
        self.paste.paste()
        spin(lambda: self.converter.asked)
        self.paste.paste()
        settle(0.1)
        self.assertEqual((self.clipboard.asked, len(self.converter.asked)), (1, 1))
        self.converter.done(paste.CONVERTED, "md")
        self.assertEqual(self.view.buffer.text, "before md after")
        self.clipboard.stream = Gio.MemoryInputStream.new_from_bytes(
            GLib.Bytes.new(b"<p>x</p>")
        )
        self.paste.paste()  # the next one is taken again
        spin(lambda: len(self.converter.asked) == 2)

    def hanging_clipboard(self):
        """Its owner never hands the HTML over."""
        reading, writing = os.pipe()
        self.addCleanup(os.close, writing)
        self.clipboard.stream = Gio.UnixInputStream.new(reading, True)

    def test_a_clipboard_that_does_not_answer_is_given_up(self):
        self.hanging_clipboard()
        with mock.patch.object(paste, "READ_SECONDS", 0.2):
            self.paste.paste()
            spin(lambda: self.view.beeps)
        settle()
        self.assertTrue(self.untouched())
        self.assertEqual((self.view.beeps, self.converter.asked), (1, []))
        self.assertTrue(self.idle())
        self.assertTrue(self.clipboard.stream.is_closed())
        self.clipboard.stream = Gio.MemoryInputStream.new_from_bytes(
            GLib.Bytes.new(b"<p>x</p>")
        )
        self.paste.paste()  # and the next paste is not held up by it
        spin(lambda: self.converter.asked)

    def test_a_window_that_closes_pastes_nothing_any_more(self):
        self.hanging_clipboard()
        self.paste.paste()
        settle(0.1)
        self.paste.close()
        settle()
        self.assertTrue(self.untouched())
        self.assertEqual(self.view.beeps, 0)
        self.assertTrue(self.idle())
        self.assertTrue(self.clipboard.stream.is_closed())

    def test_a_window_that_closes_ends_the_conversion(self):
        self.paste.paste()
        spin(lambda: self.converter.asked)
        self.paste.close()
        self.assertEqual(self.converter.cancelled, 1)
        self.assertTrue(self.untouched())
        self.assertTrue(self.idle())

    def test_a_window_that_closes_before_the_clipboard_answers(self):
        self.paste.paste()
        self.paste.close()
        settle()
        self.assertTrue(self.untouched())
        self.assertEqual(self.converter.asked, [])
        self.assertTrue(self.clipboard.stream.is_closed())
