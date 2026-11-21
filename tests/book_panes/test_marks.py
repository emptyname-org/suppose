"""Marks in the text: what marking, recolouring and unmarking change."""

import re
import time
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk

from book_panes import marks

Y, G, END = '<mark class="yellow">', '<mark class="green">', "</mark>"
HIDDEN = ("book-mark-tag",)


def marked(text, color):
    """The text after marking what stands between « and » in it, or at ‸
    with no selection: (the new text, what is selected in it then, or
    where the cursor is)."""
    if "‸" in text:
        start = end = text.index("‸")
        text = text.replace("‸", "")
    else:
        start = text.index("«")
        end = text.index("»") - 1
        text = text.replace("«", "").replace("»", "")
    change = marks.edit(text, start, end, color)
    if change is None:
        return None
    new = text[: change.start] + change.new + text[change.end :]
    begin, finish = change.select
    return new, new[begin:finish] if begin != finish else begin


class SpansTest(unittest.TestCase):
    def test_marks_are_found_with_their_colours(self):
        text = f"a {Y}b{END} c {G}d\ne{END}"
        found = marks.spans(text)
        self.assertEqual([span.color for span in found], ["yellow", "green"])
        self.assertEqual(
            [text[span.inner : span.inner_end] for span in found], ["b", "d\ne"]
        )
        self.assertEqual(
            [text[span.start : span.end] for span in found],
            [f"{Y}b{END}", f"{G}d\ne{END}"],
        )

    def test_a_tag_without_its_partner_is_text(self):
        self.assertEqual(marks.spans(f"a {END} b {Y}c"), [])
        found = marks.spans(f"{END}{Y}a{END}{END} {G}")
        self.assertEqual([(span.start, span.color) for span in found], [(7, "yellow")])

    def test_a_mark_may_hold_another(self):
        text = f"{Y}a {G}b{END} c{END} {G}d{END}"
        found = marks.spans(text)
        self.assertEqual(
            [
                (span.color, span.depth, text[span.inner : span.inner_end])
                for span in found
            ],
            [("yellow", 0, f"a {G}b{END} c"), ("green", 1, "b"), ("green", 0, "d")],
        )

    def test_a_tag_in_code_is_text(self):
        mark = f"{Y}inside{END}"
        for text in (
            f"```\n{mark}\n```",
            f"````\n```\n{mark}\n````",  # a shorter fence ends nothing
            f"```\n~~~\n{mark}\n```",  # nor does another sign
            f"~~~ text\n{mark}\n~~~~",
            f"> ```\n> {mark}\n> ```",
            f"- ```\n  {mark}\n  ```",
            f"```\n{mark}",  # open to the end
            f"a `{mark}` b",
            f"a ``{mark} ` b`` c",
            f"a `b\n{mark}` c",
        ):
            self.assertEqual(marks.spans(text), [], text)
            self.assertEqual(marks.unmarked(text), text)
            self.assertEqual(marks.for_preview(text), text)

    def test_code_ends_where_markdown_ends_it(self):
        mark = f"{Y}outside{END}"
        for text in (
            f"```\ncode\n```\n{mark}",
            f"~~~\ncode\n~~~~~\n{mark}",
            f"> ```\n> code\n{mark}",  # the quote is over
            f"a `b` {mark}",
            f"a `b\n\n{mark}`",  # no span over paragraphs
            f"a \\`{mark}`",  # an escaped backtick opens none
            f"a ``b` {mark}",  # no run as long closes it
            f"``` a`b\n{mark}",  # backticks of a span, no fence
        ):
            found = marks.spans(text)
            self.assertEqual(
                [text[span.inner : span.inner_end] for span in found], ["outside"], text
            )

    def test_other_tags_are_no_marks(self):
        text = '<mark>a</mark> <mark class="red">b</mark> <MARK class="yellow">c'
        self.assertEqual(marks.spans(text), [])

    def test_long_texts_are_read_in_one_pass(self):
        started = time.monotonic()
        marks.spans(Y * 200000)
        marks.edit(Y * 100000 + "x", 0, 5, "green")
        for text in ("`" * 200000, (Y + "`") * 50000, "```\n" * 50000, "> " * 50000):
            marks.spans(text + END)
            marks.plan(text + END)
        self.assertLess(time.monotonic() - started, 5)


class PlanTest(unittest.TestCase):
    def test_the_editor_hides_tags_and_colours_what_is_marked(self):
        text = f"a {Y}b{END} c"
        self.assertEqual(
            {name: ranges for name, ranges in marks.plan(text).items() if ranges},
            {"hide": [(2, 23), (24, 31)], "yellow": [(23, 24)]},
        )

    def test_tags_side_by_side_are_one_stretch(self):
        plan = marks.plan(f"{Y}a{END}{G}b{END}")
        self.assertEqual(plan["hide"], [(0, 21), (22, 49), (50, 57)])

    def test_within_a_mark_the_inner_colour_shows(self):
        text = f"{Y}a{G}b{END}c{END}"
        plan = marks.plan(text)
        self.assertEqual(plan["yellow"], [(21, 22), (50, 51)])
        self.assertEqual(plan["green"], [(42, 43)])
        self.assertEqual(plan["hide"], [(0, 21), (22, 42), (43, 50), (51, 58)])

    def test_the_tags_of_a_mark_without_text_stay_to_be_seen(self):
        plan = marks.plan(f"{Y}{END} {Y}{G}{END}{END} a")
        self.assertEqual(plan["hide"], [])
        self.assertEqual(plan["dim"], [(0, 28), (29, 84)])

    def test_a_text_without_marks_asks_for_nothing(self):
        for text in ("", "plain", f"{Y}open", f"`{Y}a{END}`"):
            self.assertFalse(any(marks.plan(text).values()), text)


class PlanLinksTest(unittest.TestCase):
    TEXT = "see [the page](http://e.org) now"

    def test_the_editor_hides_the_markup_of_links_and_colours_their_words(self):
        shown = marks.plan(self.TEXT)
        self.assertEqual(shown["hide"], [(4, 5), (13, 28)])
        self.assertEqual(shown["link"], [(5, 13)])

    def test_a_link_the_cursor_is_in_keeps_its_markup(self):
        for cursor in ((4, 4), (9, 9), (28, 28), (6, 20)):
            shown = marks.plan(self.TEXT, cursor)
            self.assertEqual((shown["hide"], shown["link"]), ([], [(5, 13)]), cursor)
        for cursor in ((3, 3), (29, 29), (2, 9), (9, 30)):
            self.assertEqual(marks.plan(self.TEXT, cursor)["hide"], [(4, 5), (13, 28)])

    def test_tags_and_markup_side_by_side_are_one_stretch(self):
        text = f"{Y}[x](u){END}"
        self.assertEqual(
            marks.plan(text)["hide"], [(0, len(Y) + 1), (len(Y) + 2, len(text))]
        )
        self.assertEqual(marks.plan(text)["yellow"], [(len(Y), len(text) - len(END))])

    def test_images_and_code_keep_theirs(self):
        self.assertEqual(marks.plan("![a](p.png) `[b](u)`")["hide"], [])


class HiddenPiecesTest(unittest.TestCase):
    def test_a_hidden_stretch_is_read_piece_by_piece(self):
        text = f"](u){END}{Y}[x<mar"
        self.assertEqual(
            [
                (text[a:b], kind, opens)
                for a, b, kind, opens in marks.hidden_pieces(text)
            ],
            [
                ("](u)", "link", False),
                (END, "mark", False),
                (Y, "mark", True),
                ("[", "link", True),
                ("x<mar", None, False),
            ],
        )
        self.assertEqual(marks.hidden_pieces(""), [])


class PreviewTest(unittest.TestCase):
    def test_the_preview_gets_marks_as_spans(self):
        self.assertEqual(
            marks.for_preview(f"a {Y}b **c**{END} d {G}e{END}"),
            'a <span class="mark-yellow">b **c**</span> d '
            '<span class="mark-green">e</span>',
        )

    def test_what_is_no_mark_stays_as_it_is(self):
        text = f'<mark>a</mark> {END} <span class="x">b</span> {Y}open'
        self.assertEqual(marks.for_preview(text), text)
        self.assertEqual(marks.unmarked(text), text)

    def test_a_mark_within_a_mark_is_a_span_within_a_span(self):
        self.assertEqual(
            marks.for_preview(f"{Y}a {G}b{END} c{END}"),
            '<span class="mark-yellow">a <span class="mark-green">b</span> c</span>',
        )
        self.assertEqual(marks.unmarked(f"{Y}a {G}b{END} c{END}"), "a b c")

    def test_code_that_shows_a_mark_keeps_it(self):
        text = f"```html\n{Y}literal{END}\n```\nand `{Y}x{END}` but {Y}real{END}"
        self.assertEqual(
            marks.for_preview(text),
            f"```html\n{Y}literal{END}\n```\nand `{Y}x{END}` but "
            '<span class="mark-yellow">real</span>',
        )
        self.assertEqual(
            marks.unmarked(text),
            f"```html\n{Y}literal{END}\n```\nand `{Y}x{END}` but real",
        )

    def test_marks_are_no_html_to_ask_about(self):
        self.assertEqual(marks.unmarked(f"a {Y}b{END} c {G}d{END}"), "a b c d")
        self.assertEqual(
            marks.unmarked(f"{Y}a{END} <script>x</script>"), "a <script>x</script>"
        )


class EditTest(unittest.TestCase):
    def test_a_selection_is_marked_and_stays_selected(self):
        self.assertEqual(
            marked("one «two» three", "yellow"), (f"one {Y}two{END} three", "two")
        )

    def test_a_mark_takes_another_colour(self):
        self.assertEqual(
            marked(f"one {Y}«two»{END} three", "green"),
            (f"one {G}two{END} three", "two"),
        )
        self.assertEqual(
            marked(f"one «{Y}two{END}» three", "green"),
            (f"one {G}two{END} three", "two"),
        )

    def test_the_colour_it_has_changes_nothing(self):
        self.assertIsNone(marked(f"one {Y}«two»{END} three", "yellow"))
        self.assertIsNone(marked("one «two» three", None))

    def test_a_mark_is_removed(self):
        self.assertEqual(
            marked(f"one {Y}«two»{END} three", None), ("one two three", "two")
        )

    def test_without_a_selection_the_mark_at_the_cursor_is_meant(self):
        for text in (
            f"one {Y}t‸wo{END} three",
            f"one ‸{Y}two{END} three",
            f"one {Y}two{END}‸ three",
            f'one <mark cl‸ass="yellow">two{END} three',
        ):
            new, cursor = marked(text, "green")
            self.assertEqual(new, f"one {G}two{END} three", text)
            self.assertTrue(4 <= cursor <= new.index("two") + 3, text)
        new, cursor = marked(f"one {Y}t‸wo{END} three", None)
        self.assertEqual((new, cursor), ("one two three", 5))
        new, cursor = marked(f"one {Y}t‸wo{END} three", "green")
        self.assertEqual(new[cursor:], f"wo{END} three")

    def test_the_cursor_rests_before_the_tags_at_its_place(self):
        _new, cursor = marked(f"one ‸{Y}two{END} three", "green")
        self.assertEqual(cursor, 4)
        new, cursor = marked(f"one {Y}two{END}‸ three", "green")
        self.assertEqual(new[cursor:], f"{END} three")

    def test_without_a_selection_or_a_mark_nothing_changes(self):
        self.assertIsNone(marked("one t‸wo three", "yellow"))
        self.assertIsNone(marked(f"o‸ne {Y}two{END} three", None))
        self.assertIsNone(marked("‸", "yellow"))

    def test_part_of_a_mark_is_cut_out_of_it(self):
        self.assertEqual(
            marked(f"{Y}aaa«bbb»ccc{END}", "green"),
            (f"{Y}aaa{END}{G}bbb{END}{Y}ccc{END}", "bbb"),
        )
        self.assertEqual(
            marked(f"{Y}aaa«bbb»ccc{END}", None),
            (f"{Y}aaa{END}bbb{Y}ccc{END}", "bbb"),
        )

    def test_a_selection_across_the_end_of_a_mark_takes_its_part(self):
        self.assertEqual(
            marked(f"{Y}aa«aa{END}bb»bb", "green"),
            (f"{Y}aa{END}{G}aabb{END}bb", "aabb"),
        )
        self.assertEqual(
            marked(f"aa«aa{Y}bb»bb{END}", None), (f"aaaabb{Y}bb{END}", "aabb")
        )

    def test_marks_within_a_selection_become_one(self):
        self.assertEqual(
            marked(f"«a {Y}b{END} c {G}d{END} e»", "green"),
            (f"{G}a b c d e{END}", "a b c d e"),
        )
        self.assertEqual(marked(f"{Y}a{END}« b»", "yellow"), (f"{Y}a b{END}", " b"))

    def test_selected_tags_alone_are_nothing_to_mark(self):
        self.assertIsNone(marked(f"a «{Y}»b{END}", "green"))
        self.assertIsNone(marked(f"a {Y}b«{END}» c", None))

    def test_a_mark_of_nothing_can_only_go(self):
        self.assertEqual(marked(f"a{Y}‸{END}b", None), ("ab", 1))
        self.assertIsNone(marked(f"a{Y}‸{END}b", "green"))
        self.assertEqual(marked(f"a{Y}{G}‸{END}{END}b", None), (f"a{Y}{END}b", 22))

    def test_a_mark_within_a_mark_is_unmarked_with_it(self):
        text = f"{Y}a {G}b{END} c{END}"
        self.assertEqual(marked(f"«{text}»", None), ("a b c", "a b c"))
        self.assertEqual(
            marked(f"«{text}»", "blue")[0], '<mark class="blue">a b c</mark>'
        )

    def test_at_the_cursor_the_innermost_mark_is_meant(self):
        self.assertEqual(
            marked(f"{Y}a {G}‸b{END} c{END}", "yellow")[0], f"{Y}a b c{END}"
        )
        self.assertEqual(
            marked(f"{Y}a {G}‸b{END} c{END}", None)[0], f"{Y}a{END} b {Y}c{END}"
        )
        self.assertEqual(marked(f"{Y}‸a {G}b{END} c{END}", None)[0], "a b c")

    def test_what_is_written_anew_holds_no_mark_within_a_mark(self):
        new, selected = marked(f"{Y}a {G}b{END} «c»{END}", "pink")
        self.assertEqual(new, f'{Y}a{END} {G}b{END} <mark class="pink">c</mark>')
        self.assertEqual(selected, "c")
        self.assertTrue(all(not span.depth for span in marks.spans(new)))

    def test_a_tag_without_its_partner_gets_none(self):
        self.assertEqual(
            marked(f"«a {END} b {Y} c»", "green")[0],
            f"{G}a{END} {END} {G}b{END} {Y} {G}c{END}",
        )

    def test_many_marks_are_written_anew_in_one_pass(self):
        text = (Y + "x" * 27 + "𝄞" + END) * 80000
        started = time.process_time()
        change = marks.edit(text, 0, len(text), "green")
        self.assertLess(time.process_time() - started, 5)
        self.assertEqual(
            text[: change.start] + change.new + text[change.end :],
            G + ("x" * 27 + "𝄞") * 80000 + END,
        )

    def test_blanks_at_the_ends_stay_outside(self):
        self.assertEqual(
            marked("a«  b »c", "yellow"), (f"a  {Y}b{END} c", f"  {Y}b{END} ")
        )
        self.assertIsNone(marked("a«  »b", "yellow"))

    def test_each_line_gets_its_own_mark_behind_its_sign(self):
        text = (
            "«# Title\n\n- item\n  1. inner\n- [ ] task\n> > quote\n\nplain\n"
            "---\n| a | b |\nlast»"
        )
        new, _selected = marked(text, "yellow")
        self.assertEqual(
            new,
            f"# {Y}Title{END}\n\n- {Y}item{END}\n  1. {Y}inner{END}\n"
            f"- [ ] {Y}task{END}\n> > {Y}quote{END}\n\n{Y}plain{END}\n"
            f"---\n| a | b |\n{Y}last{END}",
        )
        self.assertEqual(marked("«" + new + "»", None)[0], text[1:-1])

    def test_a_sign_within_a_line_is_text(self):
        self.assertEqual(
            marked("- one «- two\n- three» four", "green"),
            (
                f"- one {G}- two{END}\n- {G}three{END} four",
                f"- two{END}\n- {G}three",
            ),
        )
        self.assertEqual(marked("a «# b»", "green")[0], f"a {G}# b{END}")

    def test_code_between_fences_takes_no_tags(self):
        text = "«before\n```python\ncode\n```\nafter\n~~~\nmore\n~~~»"
        self.assertEqual(
            marked(text, "yellow")[0],
            f"{Y}before{END}\n```python\ncode\n```\n{Y}after{END}\n~~~\nmore\n~~~",
        )
        self.assertIsNone(marked("```\nco«de\nmo»re\n```\nafter", "yellow"))
        self.assertEqual(
            marked("```\ncode\n```\n«after»", "yellow")[0],
            f"```\ncode\n```\n{Y}after{END}",
        )
        for text in (
            "````\n```\n«inside»\n````",
            "```\n~~~\n«inside»\n```",
            "> ```\n> «inside»\n> ```",
            "- ```\n  «inside»\n  ```\nafter",
            "```\n«inside»",
        ):
            self.assertIsNone(marked(text, "yellow"), text)
        self.assertEqual(
            marked(f"«a\n```\n{Y}b{END}\n```\nc»", None), None, "tags in code stay"
        )

    def test_a_span_of_code_is_marked_whole(self):
        self.assertEqual(
            marked("a `in«si»de` b", "yellow"), (f"a {Y}`inside`{END} b", "`inside`")
        )
        self.assertEqual(marked("«a `in»side` b", "yellow")[0], f"{Y}a `inside`{END} b")
        self.assertEqual(
            marked("a ``in«side`` b» c", "yellow")[0], f"a {Y}``inside`` b{END} c"
        )
        self.assertEqual(
            marked("«a `one\ntwo` b»", "yellow")[0], f"{Y}a `one\ntwo` b{END}"
        )

    def test_a_change_that_would_make_code_of_text_is_not_made(self):
        # without its mark the line would open a block of code, and the
        # mark behind it would be text in it
        self.assertIsNone(marked(f"{Y}‸```{END}\n{G}x{END}", None))
        self.assertEqual(
            marked(f"«{Y}```{END}\n{G}x{END}»", None)[0], "```\nx", "both go: as asked"
        )

    def test_a_mark_someone_wrote_over_lines_is_split_when_touched(self):
        new, _cursor = marked(f"{Y}one\n\n- t‸wo{END}", "green")
        self.assertEqual(new, f"{G}one{END}\n\n- {G}two{END}")

    def test_a_mark_a_line_break_runs_through_is_broken_there(self):
        text = f"- {Y}one \n- two{END} three"
        change = marks.tidy(text, text.index("two"))
        new = text[: change.start] + change.new + text[change.end :]
        self.assertEqual(new, f"- {Y}one{END} \n- {Y}two{END} three")
        self.assertEqual(change.select, (new.index("\n") + 3,) * 2)  # before the tag
        self.assertIsNone(marks.tidy(new, change.select[0]))
        text = f"{Y}one\n{END}"
        change = marks.tidy(text, text.index(END))
        new = text[: change.start] + change.new + text[change.end :]
        self.assertEqual((new, change.select), (f"{Y}one{END}\n", (len(new),) * 2))

    def test_only_the_mark_at_the_place_is_broken(self):
        text = f"{Y}a\nb{END} c {G}d{END}"
        self.assertIsNone(marks.tidy(text, text.index(" c") + 1))
        self.assertIsNone(marks.tidy(text, text.index("d")))
        self.assertIsNone(marks.tidy("a\nb", 2))
        self.assertIsNotNone(marks.tidy(text, text.index("b")))

    def test_only_what_differs_is_replaced(self):
        text = f"start {Y}one{END} middle two end"
        change = marks.edit(text, text.index("two"), text.index("two") + 3, "green")
        self.assertEqual((change.new, change.end - change.start), (f"{G}two{END}", 3))
        text = f"{Y}aaa{END} {Y}bbb{END}"
        change = marks.edit(text, 21, 24, "green")
        self.assertEqual(
            text[: change.start] + change.new + text[change.end :],
            f"{G}aaa{END} {Y}bbb{END}",
        )
        self.assertEqual((change.start, change.end, change.new), (13, 19, "green"))

    def test_places_outside_the_text_are_its_ends(self):
        change = marks.edit("abc", -5, 99, "yellow")
        self.assertEqual((change.start, change.end, change.new), (0, 3, f"{Y}abc{END}"))
        change = marks.edit("abc", 3, 1, "yellow")
        self.assertEqual(change.new, f"{Y}bc{END}")

    def test_text_beyond_the_latin_letters_counts_by_character(self):
        self.assertEqual(
            marked("é𝄞 «ñ中» z", "blue"),
            ('é𝄞 <mark class="blue">ñ中</mark> z', "ñ中"),
        )


class Style:
    """Stands for the application's style: light, until told otherwise."""

    def __init__(self):
        self.dark = False
        self.handler = None

    def connect(self, _signal, handler):
        self.handler = handler
        return 7

    def disconnect(self, _handler):
        self.handler = None

    def get_dark(self):
        return self.dark

    def turn(self, dark):
        self.dark = dark
        self.handler(self, None)


class Display:
    def __init__(self):
        self.beeps = 0

    def beep(self):
        self.beeps += 1


class View:
    """Stands for the editor's text view, around a buffer of its own. Its
    keys do to the buffer what GTK's do."""

    def __init__(self, buffer, text):
        self.buffer = buffer
        self.display = Display()
        self.editable = True
        self.focused = 0
        self.handlers = {}
        self.copied = None
        self.stopped = False
        self.defaults = []  # the keys left to the view itself
        buffer.set_text(text)

    def get_buffer(self):
        return self.buffer

    def get_editable(self):
        return self.editable

    def get_display(self):
        return self.display

    def grab_focus(self):
        self.focused += 1

    def connect(self, name, handler, *args):
        self.handlers[len(self.handlers) + 1] = (name, False, handler, args)
        return len(self.handlers)

    def connect_after(self, name, handler, *args):
        self.handlers[len(self.handlers) + 1] = (name, True, handler, args)
        return len(self.handlers)

    def disconnect(self, handler):
        self.handlers[handler] = None

    def emit(self, name, *values):
        self.stopped = False
        for late in (False, True):
            for entry in list(self.handlers.values()):
                if entry is not None and entry[:2] == (name, late):
                    entry[2](self, *values, *entry[3])
                    if self.stopped:
                        return
            if not late:
                self.defaults.append(name)
                getattr(self, "do_" + name.replace("-", "_"))(*values)

    def stop_emission_by_name(self, _name):
        self.stopped = True

    def scroll_mark_onscreen(self, _mark):
        pass

    def do_backspace(self):
        buffer = self.buffer
        if not buffer.delete_selection(True, True):
            buffer.backspace(buffer.get_iter_at_mark(buffer.get_insert()), True, True)

    def do_delete_from_cursor(self, _kind, count):
        buffer = self.buffer
        if not buffer.delete_selection(True, True):
            start = buffer.get_iter_at_mark(buffer.get_insert())
            end = start.copy()
            end.forward_cursor_positions(count)
            buffer.delete_interactive(start, end, True)

    def do_copy_clipboard(self):
        bounds = self.buffer.get_selection_bounds()
        if bounds:
            self.copied = self.buffer.get_slice(*bounds, True)

    def do_cut_clipboard(self):
        self.do_copy_clipboard()
        self.buffer.delete_selection(True, True)

    def type(self, text):
        """Type the text over the selection; False when the place takes
        none."""
        buffer = self.buffer
        buffer.begin_user_action()
        buffer.delete_selection(True, True)
        taken = buffer.insert_interactive_at_cursor(text, -1, True)
        buffer.end_user_action()
        return taken

    def enter(self, sign=""):
        """A new line as Apostrophe begins it: by its own hand, with the
        sign of the list the line is in."""
        buffer = self.buffer
        buffer.begin_user_action()
        buffer.insert_at_cursor("\n" + sign)
        buffer.end_user_action()

    def paste(self, start, end):
        """Paste what the buffer holds between two places, as GTK pastes
        what was copied in the same editor: with its tags."""
        buffer = self.buffer
        at = buffer.get_iter_at_offset
        buffer.begin_user_action()
        buffer.delete_selection(True, True)
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        buffer.insert_range_interactive(cursor, at(start), at(end), True)
        buffer.end_user_action()


class Hemingway(Gtk.TextBuffer):
    """Takes no text away while its mode is on, as Apostrophe's buffer."""

    hemingway_mode = GObject.Property(type=bool, default=False)

    def do_delete_range(self, start, end):
        if not self.hemingway_mode:
            Gtk.TextBuffer.do_delete_range(self, start, end)

    def do_insert_text(self, position, text, length):
        if not (self.hemingway_mode and self.get_has_selection()):
            Gtk.TextBuffer.do_insert_text(self, position, text, length)


class Loading(Gtk.TextBuffer):
    """Sets a file's text as Apostrophe's buffer and view do."""

    setting_text = False

    def load(self, text):
        self.begin_irreversible_action()
        self.begin_user_action()
        self.setting_text = True
        self.set_text(text)
        self.setting_text = False
        self.end_user_action()
        self.end_irreversible_action()


class Completing(Gtk.TextBuffer):
    """Begins a new line itself, later, where one is typed and not
    pasted: as Apostrophe's buffer does, for its lists."""

    paste_ongoing = GObject.Property(type=bool, default=False)
    later = 0

    def do_insert_text(self, position, text, length):
        if text == "\n" and not self.paste_ongoing:
            self.later += 1
        else:
            Gtk.TextBuffer.do_insert_text(self, position, text, length)


def spin(seconds):
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


class Plain:
    """Stands for a text view without marks and without a window. Its word
    deletion takes the word at the cursor with the blanks between."""

    def __init__(self):
        self.buffer = Gtk.TextBuffer()
        self.tried = []

    def get_buffer(self):
        return self.buffer

    def emit(self, _name, _kind, count):
        buffer = self.buffer
        text = buffer.get_text(*buffer.get_bounds(), True)
        place = buffer.get_property("cursor-position")
        self.tried.append((text, place))
        if count < 0:
            other = re.search(r"\S*\s*\Z", text[:place]).start()
        else:
            other = place + re.match(r"\s*\S*", text[place:]).end()
        at = buffer.get_iter_at_offset
        buffer.delete(at(min(place, other)), at(max(place, other)))


class ShownTest(unittest.TestCase):
    def shown(self, low, high):
        buffer = Gtk.TextBuffer()
        buffer.set_text("ab<x>cd</x><y>ef</y> g")
        return marks.Shown(buffer, [(2, 5), (7, 14), (16, 20)], low, high)

    def test_the_text_is_what_is_not_hidden(self):
        self.assertEqual(self.shown(0, 22).text, "abcdef g")
        self.assertEqual(self.shown(3, 15).text, "cde")
        self.assertEqual(self.shown(8, 12).text, "")

    def test_a_place_of_the_buffer_is_one_of_the_text(self):
        shown = self.shown(0, 22)
        self.assertEqual(
            [shown.offset(place) for place in (0, 2, 4, 5, 6, 7, 14, 16, 20, 22)],
            [0, 2, 2, 2, 3, 4, 4, 6, 6, 8],
        )

    def test_a_place_of_the_text_is_before_what_is_hidden_there(self):
        shown = self.shown(0, 22)
        self.assertEqual(
            [shown.place(offset) for offset in (0, 1, 2, 3, 4, 6, 8)],
            [0, 1, 2, 6, 7, 16, 22],
        )

    def test_a_stretch_counts_from_its_beginning(self):
        shown = self.shown(5, 16)
        self.assertEqual((shown.offset(15), shown.place(3)), (3, 15))
        self.assertEqual(self.shown(8, 12).place(0), 8)


class MarkerTest(unittest.TestCase):
    def editor(self, text, buffer=None):
        self.buffer = buffer or Gtk.TextBuffer()
        self.view = View(self.buffer, text)
        self.style = Style()
        self.plain = Plain()
        self.marker = marks.Marker(self.view, self.style, self.plain)
        self.addCleanup(self.marker.close)
        return self.marker

    def text(self):
        return self.buffer.get_text(*self.buffer.get_bounds(), True)

    def shown(self):
        """The text as the editor shows it."""
        return self.buffer.get_text(*self.buffer.get_bounds(), False)

    def cursor(self):
        return self.buffer.get_property("cursor-position")

    def put(self, place):
        """Put the cursor before the part, or to the offset."""
        if isinstance(place, str):
            place = self.text().index(place)
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(place))

    def select(self, part):
        at = self.text().index(part)
        at = [self.buffer.get_iter_at_offset(place) for place in (at, at + len(part))]
        self.buffer.select_range(*at)

    def selected(self):
        if not self.buffer.get_has_selection():
            return None
        first, last = self.buffer.get_selection_bounds()
        return self.buffer.get_text(first, last, True)

    def painted(self, part):
        """The names of the marker's tags on each character of the part."""
        at = self.text().index(part)
        names = set()
        for place in range(at, at + len(part)):
            tags = self.buffer.get_iter_at_offset(place).get_tags()
            names.add(tuple(tag.get_property("name") for tag in tags))
        return names

    def test_the_selection_is_marked_in_one_undo_step(self):
        marker = self.editor("one two three")
        self.select("two")
        marker.mark("yellow")
        self.assertEqual(self.text(), f"one {Y}two{END} three")
        self.assertEqual(self.selected(), "two")
        self.assertEqual((self.view.focused, self.view.display.beeps), (1, 0))
        self.buffer.undo()
        self.assertEqual(self.text(), "one two three")
        self.buffer.redo()
        self.assertEqual(self.text(), f"one {Y}two{END} three")

    def test_the_mark_at_the_cursor_is_recoloured_and_removed(self):
        marker = self.editor(f"one {Y}two{END} three")
        self.put(26)
        marker.mark("green")
        self.assertEqual(self.text(), f"one {G}two{END} three")
        self.assertIsNone(self.selected())
        marker.mark(None)
        self.assertEqual(self.text(), "one two three")
        self.assertEqual(self.cursor(), 5)

    def test_nothing_to_mark_is_a_beep(self):
        marker = self.editor("one two three")
        marker.mark("yellow")  # no selection, no mark at the cursor
        self.select("two")
        self.view.editable = False
        marker.mark("yellow")  # a file is loading
        self.assertEqual((self.text(), self.view.display.beeps), ("one two three", 2))
        self.assertEqual(self.view.focused, 0)

    def test_what_is_marked_is_painted_and_its_tags_are_hidden(self):
        marker = self.editor(f"one {Y}two{END} {G}three{END} four")
        self.assertEqual(self.painted("two"), {("book-mark-yellow",)})
        self.assertEqual(self.painted("three"), {("book-mark-green",)})
        self.assertEqual(self.painted(Y), {("book-mark-tag",)})
        self.assertEqual(self.painted(END), {("book-mark-tag",)})
        self.assertEqual(self.painted("one "), {()})
        self.assertEqual(self.painted(" four"), {()})
        self.assertEqual(self.shown(), "one two three four")
        self.assertTrue(marker.hide.get_property("invisible"))
        self.assertFalse(marker.hide.get_property("editable"))

    def test_tags_that_are_no_mark_with_text_stay_to_be_seen(self):
        text = f"{Y}{END} `{Y}a{END}` {END} {G}"
        self.editor(text)
        self.assertEqual(self.shown(), text)
        self.assertEqual(self.painted(Y + END), {("book-mark-empty",)})
        self.assertEqual(self.painted(f"`{Y}a{END}` {END} {G}"), {()})

    def test_the_paint_follows_the_text(self):
        marker = self.editor("one two three")
        self.buffer.set_text(f"{G}one{END} two")  # by no one's hand
        self.buffer.insert(self.buffer.get_end_iter(), " more")
        self.assertTrue(marker.timer)
        spin(0.05)
        self.assertFalse(marker.timer)
        self.assertEqual(self.painted("one"), {("book-mark-green",)})
        self.assertEqual(self.shown(), "one two more")
        self.buffer.set_text("one two")
        spin(0.05)
        self.assertEqual(self.painted("one two"), {()})
        self.assertFalse(marker.painted)

    def test_only_what_differs_is_painted_anew(self):
        marker = self.editor(f"{Y}one{END} two {G}three{END}")
        applied = []
        self.buffer.connect("apply-tag", lambda _b, tag, *_at: applied.append(tag))
        self.buffer.connect("remove-tag", lambda _b, tag, *_at: applied.append(tag))
        self.put("wo ")
        self.view.type("x")
        self.assertEqual(applied, [])
        self.put("hree")
        self.view.type("x")  # within a mark, GTK gives it the mark's tags
        self.assertEqual(applied, [])
        self.put(self.text().rindex(END))
        self.view.type("y")
        self.assertEqual(applied, [marker.tags["green"]] * 2)
        self.assertEqual(self.painted("txhreey"), {("book-mark-green",)})

    def test_a_mark_is_painted_at_once(self):
        marker = self.editor("one two three")
        self.select("two")
        marker.mark("pink")
        self.assertEqual(self.painted("two"), {("book-mark-pink",)})
        self.assertEqual(self.shown(), "one two three")
        marker.mark(None)
        self.assertEqual(self.painted("one two three"), {()})

    def test_undo_and_redo_paint_at_once(self):
        marker = self.editor("one two three")
        self.select("two")
        marker.mark("pink")
        self.buffer.undo()
        self.assertEqual((self.text(), self.shown()), ("one two three",) * 2)
        self.buffer.redo()
        self.assertEqual(self.shown(), "one two three")
        self.assertEqual(self.painted("two"), {("book-mark-pink",)})
        self.assertFalse(marker.timer)

    def test_the_colours_follow_the_theme(self):
        marker = self.editor("one")
        tag = marker.tags["yellow"]
        light = tag.get_property("background-rgba").to_string()
        self.style.turn(True)
        dark = tag.get_property("background-rgba").to_string()
        self.assertNotEqual(light, dark)
        self.style.turn(False)
        self.assertEqual(tag.get_property("background-rgba").to_string(), light)

    def test_a_background_of_its_own_shade_is_painted_for_whatever_the_theme(self):
        marker = self.editor("one")

        def painted():
            return (
                marker.tags["yellow"].get_property("background-rgba").to_string(),
                marker.link.get_property("foreground-rgba").to_string(),
            )

        light = painted()
        self.style.turn(True)
        dark = painted()
        marker.shade(False)  # a light background in the dark theme
        self.assertEqual(painted(), light)
        marker.shade(True)
        self.style.turn(False)
        self.assertEqual(painted(), dark)
        marker.shade(None)  # the theme's again
        self.assertEqual(painted(), light)

    def test_hemingway_mode_lets_marks_come_and_go(self):
        marker = self.editor("one two three", Hemingway())
        self.buffer.hemingway_mode = True
        self.select("two")
        marker.mark("yellow")
        self.assertEqual(self.text(), f"one {Y}two{END} three")
        marker.mark(None)
        self.assertEqual(self.text(), "one two three")
        self.assertTrue(self.buffer.hemingway_mode)
        self.buffer.delete(*self.buffer.get_bounds())
        self.assertEqual(self.text(), "one two three")

    def test_a_closed_marker_paints_no_more(self):
        marker = self.editor("one")
        self.buffer.set_text(f"{Y}one{END}")
        marker.close()
        self.assertFalse(marker.timer)
        self.assertIsNone(self.style.handler)
        self.assertFalse(any(self.view.handlers.values()))
        self.buffer.set_text(f"{G}two{END}")
        self.assertFalse(marker.timer)
        self.put(END)
        self.view.emit("backspace")
        self.assertEqual(self.text(), f"{G}tw{END}")
        marker.close()  # twice does no harm

    def test_the_menu_actions_reach_the_marker(self):
        marker = self.editor("one two three")
        group = Gio.SimpleActionGroup()
        marks.install(group, marker)
        self.select("two")
        group.activate_action(marks.ACTION, GLib.Variant("s", "blue"))
        self.assertEqual(self.text(), 'one <mark class="blue">two</mark> three')
        group.activate_action(marks.ACTION, GLib.Variant("s", "red"))  # no colour
        self.assertEqual(self.text(), 'one <mark class="blue">two</mark> three')
        group.activate_action(marks.CLEAR, None)
        self.assertEqual(self.text(), "one two three")

    # ---- writing around hidden tags ------------------------------------------

    def test_the_cursor_rests_before_hidden_tags(self):
        self.editor(f"one {Y}two{END} three")
        text = self.text()
        self.put("two")  # behind the opening tag
        self.assertEqual(self.cursor(), 4)
        self.put(" three")  # behind the closing tag
        self.assertEqual(self.cursor(), text.index(END))
        self.assertTrue(self.view.type("!"))  # at its end: of the mark
        self.put(4)
        self.assertTrue(self.view.type("("))  # at its beginning: not
        self.assertEqual(self.text(), f"one ({Y}two!{END} three")
        self.select("two!")  # a selection ends where it is drawn
        self.assertEqual(self.selected(), "two!")

    def test_a_tag_that_shows_is_text_like_any_other(self):
        self.editor(f"one {END} two")
        self.put(" two")
        self.assertEqual(self.cursor(), 11)
        self.view.emit("backspace")
        self.assertEqual(self.text(), "one </mark two")

    def test_backspace_deletes_the_text_before_a_hidden_tag(self):
        self.editor(f"one {Y}two{END} three")
        self.put(END)
        self.view.emit("backspace")
        self.assertEqual(self.text(), f"one {Y}tw{END} three")
        self.put(4)
        self.view.emit("backspace")
        self.assertEqual(self.text(), f"one{Y}tw{END} three")
        self.assertEqual(self.cursor(), 3)

    def test_delete_deletes_the_text_behind_a_hidden_tag(self):
        self.editor(f"one {Y}two{END} three")
        self.put(4)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.CHARS, 1)
        self.assertEqual((self.text(), self.cursor()), (f"one {Y}wo{END} three", 4))
        self.put(END)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.CHARS, 1)
        self.assertEqual(self.text(), f"one {Y}wo{END}three")
        self.assertEqual(self.cursor(), self.text().index(END))
        self.assertIsNone(self.marker.back)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.CHARS, -1)
        self.assertEqual(self.text(), f"one {Y}w{END}three")
        self.view.emit("delete-from-cursor", Gtk.DeleteType.PARAGRAPH_ENDS, 1)
        self.assertEqual(self.text(), f"one {Y}w{END}three")

    def test_a_word_is_deleted_as_it_shows(self):
        text = f"{Y}one{END}{G}two{END} three"
        self.editor(text)
        self.put(text.rindex(END))
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual((self.text(), self.cursor()), (" three", 0))
        self.assertEqual(self.view.defaults, [])
        self.buffer.undo()
        self.assertEqual(self.text(), text)  # one step
        self.put(0)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, 1)
        self.assertEqual((self.text(), self.cursor()), (" three", 0))

    def test_a_word_beside_a_mark_is_deleted_with_what_shows_of_it(self):
        self.editor(f"a {Y}one{END}two b")
        self.put(" b")
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual(self.text(), "a  b")
        self.editor(f"a one{Y}two{END} b")
        self.put("one")
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, 1)
        self.assertEqual(self.text(), "a  b")

    def test_a_word_with_no_tag_in_it_is_deleted_as_it_shows_too(self):
        self.editor(f"{Y}one{END} two three")
        self.put(len(self.text()))
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual(self.text(), f"{Y}one{END} two ")
        self.assertEqual(self.view.defaults, [])  # a tag in its line shifts its ends
        self.assertEqual(self.plain.tried, [("one two three", 13)])

    def test_a_text_that_hides_nothing_is_left_to_the_editor(self):
        self.editor("one two three")
        self.put(13)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual(self.view.defaults, ["delete-from-cursor"])
        self.assertEqual(self.plain.tried, [])

    def test_a_word_behind_empty_lines_is_found(self):
        self.editor(f"{Y}one{END}\n\n\n{G}two{END}")
        self.put(END)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, 1)
        self.assertEqual(self.text(), f"{Y}one{END}")
        # the lines at hand held blanks alone, no word
        self.assertEqual(self.plain.tried, [("one\n\n\n", 3), ("one\n\n\ntwo", 3)])

    def test_several_words_are_looked_for_in_the_whole_text(self):
        self.editor(f"{Y}one{END} two" + "\n" * 6 + "three")
        self.put(0)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, 2)
        self.assertEqual(self.plain.tried, [("one two" + "\n" * 6 + "three", 0)])

    def test_a_word_far_from_the_cursor_is_found(self):
        self.editor(f"zero\n{Y}one{END}{G}two{END}" + "\n" * 6)
        self.put(len(self.text()))
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual(self.text(), "zero\n")
        self.assertEqual(
            self.plain.tried, [("\n\n", 2), ("zero\nonetwo\n\n\n\n\n\n", 17)]
        )

    def test_where_no_word_is_nothing_is_deleted(self):
        self.editor(f"{Y}one{END}")
        self.put(END)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, 1)
        self.assertEqual(self.text(), f"{Y}one{END}")
        self.assertEqual(self.view.defaults, [])
        self.assertEqual(self.plain.tried, [("one", 3)])  # asked once

    def test_what_undo_puts_back_is_completed_by_nobody(self):
        text = f"{Y}one{END}\nmore"
        self.editor(text, Completing())
        self.put("more")
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual(self.text(), "more")
        self.buffer.undo()
        self.assertEqual((self.text(), self.buffer.later), (text, 0))
        self.assertFalse(self.buffer.paste_ongoing)
        self.buffer.redo()
        self.assertEqual((self.text(), self.buffer.later), ("more", 0))
        self.buffer.paste_ongoing = True  # a paste under way stays one
        self.buffer.undo()
        self.assertTrue(self.buffer.paste_ongoing)

    def test_no_word_is_deleted_where_nothing_may_be(self):
        text = f"{Y}one{END}{G}two{END}"
        self.editor(text)
        self.view.editable = False
        self.put(text.rindex(END))
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual(self.text(), text)

    def test_a_mark_goes_with_the_last_of_its_text(self):
        self.editor(f"one {Y}two{END} three")
        self.put(END)
        for _key in "two":
            self.view.emit("backspace")
        self.assertEqual((self.text(), self.cursor()), ("one  three", 4))
        self.assertTrue(self.view.type("2"))
        self.buffer.undo()
        self.buffer.undo()  # one step: the letter and the tags
        self.assertEqual(self.text(), f"one {Y}t{END} three")
        self.assertEqual(self.shown(), "one t three")

    def test_a_mark_within_a_mark_takes_the_outer_one_along(self):
        self.editor(f"a {Y}{G}b{END}{END} c")
        self.put(END)
        self.view.emit("backspace")
        self.assertEqual(self.text(), "a  c")

    def test_what_is_typed_over_a_mark_replaces_it(self):
        self.editor(f"one {Y}two{END} three")
        self.select("two")
        self.assertTrue(self.view.type("2"))
        self.assertEqual(self.text(), "one 2 three")
        self.buffer.undo()
        self.assertEqual(self.text(), f"one {Y}two{END} three")
        self.select(f"e {Y}two{END} t")  # and more around it
        self.view.emit("backspace")
        self.assertEqual(self.text(), "onhree")

    def test_part_of_a_mark_deleted_leaves_the_rest_marked(self):
        self.editor(f"one {Y}two{END} three")
        self.select(f"wo{END} th")
        self.assertTrue(self.view.type("-"))
        self.assertEqual(self.text(), f"one {Y}t-{END}ree")

    def test_between_two_marks_typing_goes_on(self):
        self.editor(f"{Y}a{END} {G}b{END}")
        self.put(G)
        self.view.emit("backspace")  # the blank between them
        self.assertEqual(self.text(), f"{Y}a{END}{G}b{END}")
        self.assertEqual(self.cursor(), self.text().index(END))
        self.assertTrue(self.view.type("c"))
        self.assertEqual(self.text(), f"{Y}ac{END}{G}b{END}")

    def test_a_new_line_in_a_mark_breaks_the_mark(self):
        self.editor(f"- {Y}one two{END}")
        self.put("two")
        self.view.enter("- ")
        self.assertEqual(self.text(), f"- {Y}one{END} \n- {Y}two{END}")
        self.assertEqual(self.shown(), "- one \n- two")
        self.assertEqual(self.cursor(), self.text().index(f"{Y}two"))
        self.buffer.undo()  # one step
        self.assertEqual(self.text(), f"- {Y}one two{END}")
        self.put(END)
        self.view.enter()
        self.assertEqual(self.text(), f"- {Y}one two{END}\n")
        self.assertEqual(self.cursor(), len(self.text()))

    def test_a_new_line_elsewhere_leaves_marks_alone(self):
        self.editor(f"{Y}one\ntwo{END} three")  # as someone wrote it
        self.put(" three")
        self.put(len(self.text()))
        self.view.enter()
        self.assertEqual(self.text(), f"{Y}one\ntwo{END} three\n")

    def test_a_file_that_is_loaded_stays_as_it_is(self):
        self.editor("", Loading())
        text = f"{Y}one\ntwo{END} three\n{Y}four{END}"
        self.buffer.load(text)
        self.assertEqual(self.text(), text)
        self.assertEqual(self.shown(), "one\ntwo three\nfour")  # at once
        self.assertFalse(self.marker.timer)

    def test_an_agents_edit_is_made_as_it_is(self):
        marker = self.editor(f"one {Y}two{END}\nthree")
        text = self.text()
        with marker.exact():
            self.buffer.begin_user_action()
            at = self.buffer.get_iter_at_offset
            self.buffer.delete(at(text.index("two")), at(text.index(END)))
            self.buffer.insert(at(text.index("two")), "a\nb")
            self.buffer.delete(at(0), at(4))
            self.buffer.end_user_action()
        self.assertEqual(self.text(), f"{Y}a\nb{END}\nthree")
        self.assertEqual(self.shown(), "a\nb\nthree")
        with marker.exact():
            self.buffer.begin_user_action()
            self.buffer.delete(at(len(Y)), at(len(Y) + 3))
            self.buffer.end_user_action()
        self.assertEqual(self.text(), f"{Y}{END}\nthree")
        self.assertEqual(self.shown(), self.text())

    def test_what_is_copied_takes_whole_marks_along(self):
        self.editor(f"one {Y}two{END} three")
        self.select("two")
        self.view.emit("copy-clipboard")
        self.assertEqual(self.view.copied, f"{Y}two{END}")
        self.select("e ")
        self.view.emit("copy-clipboard")  # no tag at its ends
        self.assertEqual(self.view.copied, "e ")
        self.put(4)
        self.view.emit("copy-clipboard")  # nothing selected
        self.assertEqual(self.view.copied, "e ")
        self.select("wo")
        self.view.emit("cut-clipboard")
        self.assertEqual(self.view.copied, f"wo{END}")
        self.assertEqual(self.text(), f"one {Y}t{END} three")
        self.select("t")
        self.view.emit("cut-clipboard")
        self.assertEqual((self.view.copied, self.text()), (f"{Y}t{END}", "one  three"))

    def test_the_selection_keeps_its_direction_when_it_grows(self):
        self.editor(f"one {Y}two{END} three")
        text = self.text()
        at = self.buffer.get_iter_at_offset
        self.buffer.select_range(at(text.index("two")), at(text.index(END)))
        self.view.emit("copy-clipboard")
        self.assertEqual(self.cursor(), 4)
        self.buffer.select_range(at(text.index(END)), at(text.index("two")))
        self.view.emit("copy-clipboard")
        self.assertEqual(self.cursor(), text.index(" three"))

    def test_a_pasted_mark_arrives_whole_or_as_plain_text(self):
        self.editor(f"one {Y}two{END} three\n")
        text = self.text()
        self.put(len(text))
        self.view.paste(4, text.index(" three"))  # the mark
        self.assertEqual(self.text(), f"{text}{Y}two{END}")
        self.assertEqual(self.shown(), "one two three\ntwo")
        self.view.paste(text.index("wo"), text.index("hree"))  # the end of one
        self.assertEqual(self.text(), f"{text}{Y}two{END}wo t")
        self.put(0)
        self.view.paste(2, text.index("wo"))  # the beginning of one
        self.assertEqual(self.text(), f"e t{text}{Y}two{END}wo t")
        self.buffer.undo()  # one step
        self.assertEqual(self.text(), f"{text}{Y}two{END}wo t")
        self.assertEqual(self.marker.arrived, [])

    def test_pasted_lines_in_a_mark_get_marks_of_their_own(self):
        self.editor(f"{Y}one{END}\n- a\n- b")
        text = self.text()
        self.put(END)
        self.view.paste(text.index("\n- a"), len(text))
        self.assertEqual(
            self.text(), f"{Y}one{END}\n- {Y}a{END}\n- {Y}b{END}\n- a\n- b"
        )

    # ---- links -----------------------------------------------------------------

    LINK = "see [the page](http://e.org) now"

    def test_a_link_shows_its_words_in_blue_and_hides_the_rest(self):
        self.editor(self.LINK)
        self.put(0)
        self.assertEqual(self.shown(), "see the page now")
        self.assertEqual(self.painted("the page"), {("book-link",)})
        self.assertEqual(self.painted("[") | self.painted("](http://e.org)"), {HIDDEN})
        self.assertEqual(self.painted("see "), {()})

    def test_the_cursor_in_a_link_shows_it_as_written(self):
        self.editor(self.LINK)
        for place in ("[", "page", "](", "e.org", " now"):  # its ends are of it
            self.put(0)
            self.assertEqual(self.shown(), "see the page now")
            self.put(place)
            self.assertEqual(self.shown(), self.LINK, place)
            self.assertEqual(self.painted("the page"), {("book-link",)})
        self.put("now")
        self.assertEqual(self.shown(), "see the page now")
        self.assertEqual(self.marker.open, [])

    def test_a_selection_in_a_link_shows_it_and_one_across_it_does_not(self):
        self.editor(self.LINK)
        self.select("http://e.org")
        self.assertEqual(self.shown(), self.LINK)
        self.select("ee [the page](http://e.org) n")
        self.assertEqual(self.shown(), "see the page now")
        self.select("page](http://e.org) n")  # from within it
        self.assertEqual(self.shown(), "see the page now")

    def test_where_two_links_meet_both_show(self):
        text = "[a](u)[b](v) c"
        self.editor(text)
        self.put("[b]")
        self.assertEqual(self.shown(), text)
        self.put("b")
        self.assertEqual(self.shown(), "a[b](v) c")

    def test_what_is_typed_at_the_ends_of_a_link_is_outside_it(self):
        self.editor("see [the page](u)")
        self.assertEqual(self.shown(), "see [the page](u)")  # the cursor is at its end
        self.assertTrue(self.view.type("."))
        self.put(4)
        self.assertTrue(self.view.type("("))
        self.assertEqual(self.text(), "see ([the page](u).")
        self.put(0)
        self.assertEqual(self.shown(), "see (the page.")

    def test_a_link_that_is_written_shows_when_the_cursor_leaves_it(self):
        self.editor("see ")
        for key in "[it](u":
            self.assertTrue(self.view.type(key))
        self.assertEqual(self.painted("it"), {()})  # no link yet
        self.assertTrue(self.view.type(")"))
        self.assertEqual(self.shown(), "see [it](u)")
        self.assertEqual(self.painted("it"), {("book-link",)})
        self.assertTrue(self.view.type(" "))
        self.assertEqual(self.shown(), "see it ")

    def test_a_line_broken_in_a_link_leaves_no_link(self):
        self.editor(self.LINK)
        self.put("page")
        self.view.enter()
        self.put(0)
        self.assertEqual(self.shown(), self.text())
        self.assertEqual(self.painted("page"), {()})

    def test_a_link_goes_with_the_last_of_its_words(self):
        self.editor("see [the page](u) now")
        self.select("e [the page](u) n")  # as it shows: "e the page n"
        self.view.emit("backspace")
        self.assertEqual(self.text(), "seow")
        self.buffer.undo()  # one step
        self.assertEqual(self.text(), "see [the page](u) now")
        self.select("[the page](u) n")
        self.assertTrue(self.view.type("-"))
        self.assertEqual(self.text(), "see -ow")

    def test_part_of_a_link_deleted_leaves_the_rest_a_link(self):
        self.editor("see [the page](u) now")
        self.select("page](u) n")
        self.view.emit("backspace")
        self.assertEqual(self.text(), "see [the ](u)ow")
        self.select("ee [th")
        self.view.emit("backspace")
        self.assertEqual(self.text(), "s[e ](u)ow")

    def test_a_link_in_a_mark_goes_with_it(self):
        self.editor(f"a {Y}[x](u){END} b")
        self.put(2)
        self.assertEqual(self.shown(), "a x b")
        self.view.emit("delete-from-cursor", Gtk.DeleteType.CHARS, 1)
        self.assertEqual((self.text(), self.cursor()), ("a  b", 2))
        self.editor(f"a [{Y}x{END}](u) b")
        self.put(" b")  # the end of the link: its markup shows
        self.assertEqual(self.shown(), "a [x](u) b")
        self.put(0)
        self.select(f" [{Y}x{END}](u) ")
        self.view.emit("backspace")
        self.assertEqual(self.text(), "ab")

    def test_the_cursor_behind_a_link_in_a_mark_rests_behind_the_link(self):
        self.editor(f"{Y}[x](u){END} b")
        self.put(" b")
        self.assertEqual(self.cursor(), self.text().index(END))
        self.assertEqual(self.shown(), "[x](u) b")
        self.assertTrue(self.view.type("!"))  # of the mark, not of the link
        self.assertEqual(self.text(), f"{Y}[x](u)!{END} b")

    def test_a_key_beside_a_link_takes_what_shows(self):
        self.editor("a [one](u) b")
        self.put(0)
        self.view.emit("delete-from-cursor", Gtk.DeleteType.CHARS, 1)
        self.assertEqual(self.text(), " [one](u) b")
        self.view.emit("delete-from-cursor", Gtk.DeleteType.CHARS, 1)
        self.assertEqual(self.text(), "[one](u) b")  # now before the link:
        self.assertEqual(self.shown(), "[one](u) b")  # it shows
        self.view.emit("delete-from-cursor", Gtk.DeleteType.CHARS, 1)
        self.assertEqual(self.text(), "one](u) b")
        self.editor("a [one](u) b")
        self.put("b")
        self.view.emit("backspace")
        self.assertEqual(self.shown(), "a [one](u)b")  # at its end: it shows
        self.view.emit("backspace")
        self.assertEqual(self.text(), "a [one](ub")

    def test_a_word_in_a_link_is_deleted_as_it_shows(self):
        self.editor("a [one](u)two b")
        self.put(" b")
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, -1)
        self.assertEqual(self.text(), "a  b")
        self.assertEqual(self.plain.tried, [("a onetwo b", 8)])
        self.editor("a [one](u)two b")
        self.put("one")  # in the link, as it is written
        self.view.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, 1)
        self.assertEqual(self.view.defaults, ["delete-from-cursor"])

    def test_what_is_copied_takes_whole_links_along(self):
        self.editor("see [the page](u) now")
        self.select("see [the page")
        self.view.emit("copy-clipboard")
        self.assertEqual(self.view.copied, "see [the page](u)")
        at = self.buffer.get_iter_at_offset
        self.buffer.select_range(at(5), at(len(self.text())))  # behind the bracket
        self.view.emit("copy-clipboard")
        self.assertEqual(self.view.copied, "[the page](u) now")
        self.select("the")  # in the link, as it is written
        self.view.emit("copy-clipboard")
        self.assertEqual(self.view.copied, "the")
        self.editor(f"a {Y}[x](u){END} b")
        at = self.buffer.get_iter_at_offset
        self.buffer.select_range(at(2), at(self.text().index("]")))
        self.view.emit("copy-clipboard")
        self.assertEqual(self.view.copied, f"{Y}[x](u){END}")

    def test_a_pasted_link_arrives_whole_or_as_plain_text(self):
        self.editor("see [the page](u) now\n")
        text = self.text()
        self.put(len(text))
        self.view.paste(4, text.index(" now"))  # the link
        self.assertEqual(self.text(), f"{text}[the page](u)")
        self.view.type(" ")
        self.assertEqual(self.shown(), "see the page now\nthe page ")
        self.view.paste(0, text.index("page"))  # the beginning of one
        self.assertEqual(self.text(), f"{text}[the page](u) see the ")
        self.view.paste(text.index("page"), text.index("now"))  # the end of one
        self.assertEqual(self.text(), f"{text}[the page](u) see the page ")
        self.buffer.undo()  # one step
        self.assertEqual(self.text(), f"{text}[the page](u) see the ")
        self.assertEqual(self.marker.arrived, [])

    def test_the_blue_of_links_follows_the_theme(self):
        marker = self.editor(self.LINK)
        light = marker.link.get_property("foreground-rgba").to_string()
        self.style.turn(True)
        dark = marker.link.get_property("foreground-rgba").to_string()
        self.assertNotEqual(light, dark)
        self.assertEqual(marker.link.get_priority(), 0)

    def test_links_that_go_leave_nothing_behind(self):
        marker = self.editor(self.LINK)
        self.put(9)
        self.buffer.set_text("plain")
        spin(0.05)
        self.assertEqual((marker.links, marker.open, marker.painted), ([], [], False))
        self.put(2)  # nothing to follow
        self.assertEqual(self.shown(), "plain")
