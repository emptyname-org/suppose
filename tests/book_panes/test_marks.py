"""Marks in the text: what marking, recolouring and unmarking change."""

import time
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk

from book_panes import marks

Y, G, END = '<mark class="yellow">', '<mark class="green">', "</mark>"


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
        found = marks.spans(f"{Y}a {G}b{END} c{END}")
        self.assertEqual([(span.start, span.color) for span in found], [(0, "yellow")])

    def test_other_tags_are_no_marks(self):
        text = '<mark>a</mark> <mark class="red">b</mark> <MARK class="yellow">c'
        self.assertEqual(marks.spans(text), [])

    def test_many_open_tags_are_read_in_one_pass(self):
        started = time.monotonic()
        marks.spans(Y * 200000)
        marks.edit(Y * 100000 + "x", 0, 5, "green")
        self.assertLess(time.monotonic() - started, 5)


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
            self.assertTrue(new.index("two") <= cursor <= new.index("two") + 3, text)
        new, cursor = marked(f"one {Y}t‸wo{END} three", None)
        self.assertEqual((new, cursor), ("one two three", 5))
        new, cursor = marked(f"one {Y}t‸wo{END} three", "green")
        self.assertEqual(new[cursor:], f"wo{END} three")

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

    def test_a_mark_someone_wrote_over_lines_is_split_when_touched(self):
        new, _cursor = marked(f"{Y}one\n\n- t‸wo{END}", "green")
        self.assertEqual(new, f"{G}one{END}\n\n- {G}two{END}")

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
    """Stands for the editor's text view, around a buffer of its own."""

    def __init__(self, buffer, text):
        self.buffer = buffer
        self.display = Display()
        self.editable = True
        self.focused = 0
        buffer.set_text(text)

    def get_buffer(self):
        return self.buffer

    def get_editable(self):
        return self.editable

    def get_display(self):
        return self.display

    def grab_focus(self):
        self.focused += 1


class Hemingway(Gtk.TextBuffer):
    """Takes no text away while its mode is on, as Apostrophe's buffer."""

    hemingway_mode = GObject.Property(type=bool, default=False)

    def do_delete_range(self, start, end):
        if not self.hemingway_mode:
            Gtk.TextBuffer.do_delete_range(self, start, end)

    def do_insert_text(self, position, text, length):
        if not (self.hemingway_mode and self.get_has_selection()):
            Gtk.TextBuffer.do_insert_text(self, position, text, length)


def spin(seconds):
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


class MarkerTest(unittest.TestCase):
    def editor(self, text, buffer=None):
        self.buffer = buffer or Gtk.TextBuffer()
        self.view = View(self.buffer, text)
        self.style = Style()
        self.marker = marks.Marker(self.view, self.style)
        self.addCleanup(self.marker.close)
        return self.marker

    def text(self):
        first, last = self.buffer.get_bounds()
        return self.buffer.get_text(first, last, True)

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
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(26))
        marker.mark("green")
        self.assertEqual(self.text(), f"one {G}two{END} three")
        self.assertIsNone(self.selected())
        marker.mark(None)
        self.assertEqual(self.text(), "one two three")
        self.assertEqual(self.buffer.get_property("cursor-position"), 5)

    def test_nothing_to_mark_is_a_beep(self):
        marker = self.editor("one two three")
        marker.mark("yellow")  # no selection, no mark at the cursor
        self.select("two")
        self.view.editable = False
        marker.mark("yellow")  # a file is loading
        self.assertEqual((self.text(), self.view.display.beeps), ("one two three", 2))
        self.assertEqual(self.view.focused, 0)

    def test_what_is_marked_is_painted_and_its_tags_dimmed(self):
        self.editor(f"one {Y}two{END} {G}three{END} four")
        self.assertEqual(self.painted("two"), {("book-mark-yellow",)})
        self.assertEqual(self.painted("three"), {("book-mark-green",)})
        self.assertEqual(self.painted(Y), {("book-mark-tag",)})
        self.assertEqual(self.painted(END), {("book-mark-tag",)})
        self.assertEqual(self.painted("one "), {()})
        self.assertEqual(self.painted(" four"), {()})

    def test_the_paint_follows_the_text(self):
        marker = self.editor("one two three")
        self.buffer.set_text(f"{G}one{END} two")  # another file, an agent's edit
        self.buffer.insert(self.buffer.get_end_iter(), " more")
        self.assertTrue(marker.timer)
        spin(marks.PAINT_DELAY_MS / 1000 + 0.2)
        self.assertFalse(marker.timer)
        self.assertEqual(self.painted("one"), {("book-mark-green",)})
        self.buffer.set_text("one two")
        spin(marks.PAINT_DELAY_MS / 1000 + 0.2)
        self.assertEqual(self.painted("one two"), {()})
        self.assertFalse(marker.painted)

    def test_a_mark_is_painted_at_once(self):
        marker = self.editor("one two three")
        self.select("two")
        marker.mark("pink")
        self.assertEqual(self.painted("two"), {("book-mark-pink",)})
        marker.mark(None)
        self.assertEqual(self.painted("one two three"), {()})

    def test_the_colours_follow_the_theme(self):
        marker = self.editor("one")
        tag = marker.tags["yellow"]
        light = tag.get_property("background-rgba").to_string()
        self.style.turn(True)
        dark = tag.get_property("background-rgba").to_string()
        self.assertNotEqual(light, dark)
        self.style.turn(False)
        self.assertEqual(tag.get_property("background-rgba").to_string(), light)

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
        self.buffer.set_text(f"{G}two{END}")
        self.assertFalse(marker.timer)
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
