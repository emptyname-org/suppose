"""Text marked in colours, from the Mark menu beside Save.

A mark is in the text itself, as HTML that Markdown lets through:

    <mark class="yellow">marked text</mark>

so it stays with the file whoever changes it, and the agent reads it as
the writer does. The editor paints what is marked and dims the tags. The
preview paints it from its stylesheet and does not take a mark for HTML
it has to ask about; the tags are no words to count (patches/: base.css,
preview_converter.py, preview_security.py, stats_counter.py).

Marking goes by the selection; without one, by the mark the cursor is in.
One tag cannot hold several paragraphs, nor the sign a line begins with
(#, -, >, 1.): each line gets a mark of its own, behind its sign.
"""

import re
from typing import NamedTuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

# name -> background in the editor on a light theme, on a dark one
COLORS = {
    "yellow": ("#fff0a0", "#6a5c00"),
    "green": ("#c9f2c4", "#22592b"),
    "blue": ("#c7e2ff", "#1f4d78"),
    "pink": ("#ffd0e6", "#782650"),
    "orange": ("#ffd8ae", "#7a4810"),
}
TAG = re.compile(r'<mark class="({})">|</mark>'.format("|".join(COLORS)))
# what a line begins with before its text: indent, quote, list or heading sign
SIGN = re.compile(
    r"[ \t]*(?:>[ \t]?)*"
    r"(?:(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?|#{1,6}[ \t]+)?"
)
# a line that opens or closes a block of code
FENCE = re.compile(r"^[ \t]*(?:`{3,}|~{3,})", re.MULTILINE)
# a line no tag goes around: a rule, a row of a table
BARE = re.compile(r"(?:[-*_][ \t]*){3,}|\|.*")
ACTION = "mark"
CLEAR = "unmark"
PAINT_DELAY_MS = 60
CSS = """
.book-mark-swatch { min-width: 18px; min-height: 18px; border-radius: 5px; }
""" + "".join(
    f".book-mark-swatch.{name} {{ background-color: {light}; }}\n"
    for name, (light, _dark) in COLORS.items()
)


class Span(NamedTuple):
    """A mark in a text: where its opening tag begins, its text begins and
    ends, its closing tag ends; and its colour."""

    start: int
    inner: int
    inner_end: int
    end: int
    color: str


class Edit(NamedTuple):
    """Replace text[start:end] by new; then the selection is select[0] to
    select[1], offsets in the text as it is after."""

    start: int
    end: int
    new: str
    select: tuple[int, int]


def opening(color):
    return f'<mark class="{color}">'


CLOSING = "</mark>"


def spans(text):
    """The marks of a text, in order. A tag without its partner is text."""
    found, opened = [], None
    for tag in TAG.finditer(text):
        if tag.group(1):
            if opened is None:
                opened = tag
        elif opened is not None:
            found.append(
                Span(
                    opened.start(),
                    opened.end(),
                    tag.start(),
                    tag.end(),
                    opened.group(1),
                )
            )
            opened = None
    return found


def recast(text, opened, closed):
    """The text with the tags of its marks replaced: by opened, in which
    {} stands for the colour, and by closed."""
    parts, taken = [], 0
    for span in spans(text):
        parts += [
            text[taken : span.start],
            opened.format(span.color),
            text[span.inner : span.inner_end],
            closed,
        ]
        taken = span.end
    parts.append(text[taken:])
    return "".join(parts)


def unmarked(text):
    """The text without the tags of its marks: for what counts words or
    asks whether there is HTML."""
    return recast(text, "", "")


def for_preview(text):
    """The text with its marks as spans: those pandoc takes for its own,
    and keeps where the preview leaves HTML out."""
    return recast(text, '<span class="mark-{}">', "</span>")


def in_code(text, place):
    """Whether the line at the place is within a fenced block of code."""
    return len(FENCE.findall(text, 0, place)) % 2 == 1


def wrap(run, color, begins_line, code):
    """A run of text as marked in the colour, or as it is with None: its
    pieces in order, each with whether it is text of the run (tags are
    not), and whether a block of code is open at its end."""
    pieces = []
    for line in run.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        fence = begins_line and FENCE.match(body) is not None
        signed = SIGN.match(body) if begins_line else None
        sign = signed.end() if signed is not None else 0
        core = body[sign:].strip()
        bare = begins_line and BARE.fullmatch(body.strip()) is not None
        if color is None or not core or code or fence or bare:
            pieces.append((line, True))
        else:
            at = body.index(core, sign)
            pieces += [
                (body[:at], True),
                (opening(color), False),
                (core, True),
                (CLOSING, False),
                (line[at + len(core) :], True),
            ]
        code = code != fence
        begins_line = line != body
    return pieces, code


def edit(text, start, end, color):
    """The change that marks text[start:end] in the colour, or with None
    unmarks it. With start == end it is the mark around that place. None
    when there is nothing to change."""
    start, end = sorted(min(max(place, 0), len(text)) for place in (start, end))
    found = spans(text)
    whole = None
    cursor = start
    if start == end:
        around = [span for span in found if span.start <= start <= span.end]
        if not around:
            return None
        whole = around[0]
        start, end = whole.inner, whole.inner_end
        if start == end:  # a mark of nothing
            if color is not None:
                return None
            return Edit(whole.start, whole.end, "", (whole.start, whole.start))
    # with its neighbours: marks of one colour that touch become one
    touched = [span for span in found if span.start <= end and span.end >= start]
    low = min([start] + [span.start for span in touched])
    high = max([end] + [span.end for span in touched])

    # the text of the stretch without its tags, in runs of one colour, and
    # the selection counted in that text
    runs: list[tuple[str, str | None]] = []
    chosen: list[int] = []
    count = 0
    stretches = []
    place = low
    for span in touched:
        stretches += [
            (place, span.start, None),
            (span.inner, span.inner_end, span.color),
        ]
        place = span.end
    stretches.append((place, high, None))
    for begin, finish, was in stretches:
        for part, part_end, selected in (
            (begin, min(finish, start), False),
            (max(begin, start), min(finish, end), True),
            (max(begin, end), finish, False),
        ):
            if part < part_end:
                if selected:
                    chosen += [count, count + part_end - part]
                count += part_end - part
                now = color if selected else was
                if runs and runs[-1][1] == now:
                    runs[-1] = (runs[-1][0] + text[part:part_end], now)
                else:
                    runs.append((text[part:part_end], now))
    if not chosen:
        return None  # tags alone were selected
    first, last = chosen[0], chosen[-1]

    pieces = []
    before = text[low - 1] if low else "\n"
    code = in_code(text, low)
    for run, now in runs:
        marked, code = wrap(run, now, before in "\r\n", code)
        pieces += marked
        before = run[-1]
    new = "".join(piece for piece, _is_text in pieces)

    def placed(index):
        """Where the index-th character of the stretch's text is now."""
        offset = seen = 0
        for piece, is_text in pieces:
            if is_text:
                if index < seen + len(piece):
                    return low + offset + index - seen
                seen += len(piece)
            offset += len(piece)
        return low + offset

    if whole is None:
        select = (placed(first), placed(last - 1) + 1)
    else:  # the cursor stays in its text
        at = first + min(max(cursor, whole.inner), whole.inner_end) - whole.inner
        at = placed(at) if at < last else placed(last - 1) + 1
        select = (at, at)
    old = text[low:high]
    if new == old:
        return None
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    return Edit(low + head, high - tail, new[head : len(new) - tail], select)


class Marker:
    """The marks of one editor: marking, and painting what is marked."""

    def __init__(self, view, style=None):
        self.view = view
        self.buffer = buffer = view.get_buffer()
        self.timer = 0
        self.painted = False
        self.dim = buffer.create_tag("book-mark-tag", foreground="gray")
        self.tags = {name: buffer.create_tag("book-mark-" + name) for name in COLORS}
        self.style = style or Adw.StyleManager.get_default()
        self.style_handler = self.style.connect("notify::dark", self._on_theme)
        self.changed_handler = buffer.connect("changed", self._on_changed)
        self._on_theme()
        self._paint()

    def close(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.style_handler:
            self.style.disconnect(self.style_handler)
            self.style_handler = 0
        if self.changed_handler:
            self.buffer.disconnect(self.changed_handler)
            self.changed_handler = 0

    # ---- marking -------------------------------------------------------------

    def mark(self, color):
        """Mark the selection, or the mark the cursor is in; None unmarks.
        A beep when there is nothing to mark."""
        view, buffer = self.view, self.buffer
        change = None
        if view.get_editable():
            first, last = buffer.get_bounds()
            text = buffer.get_slice(first, last, True)
            start = end = buffer.get_property("cursor-position")
            if buffer.get_has_selection():
                start, end = (
                    place.get_offset() for place in buffer.get_selection_bounds()
                )
            change = edit(text, start, end, color)
        if change is None:
            view.get_display().beep()
            return
        # Apostrophe's: nothing completed in what is inserted; and its
        # Hemingway mode, which takes no text away, lets a tag go
        hemingway = getattr(buffer, "hemingway_mode", False)
        if hemingway:
            buffer.hemingway_mode = False
        buffer.paste_ongoing = True
        buffer.begin_user_action()
        try:
            buffer.delete(
                buffer.get_iter_at_offset(change.start),
                buffer.get_iter_at_offset(change.end),
            )
            buffer.insert(buffer.get_iter_at_offset(change.start), change.new)
        finally:
            buffer.end_user_action()
            buffer.paste_ongoing = False
            if hemingway:
                buffer.hemingway_mode = True
        buffer.select_range(
            buffer.get_iter_at_offset(change.select[0]),
            buffer.get_iter_at_offset(change.select[1]),
        )
        self._paint()
        view.grab_focus()

    # ---- painting ------------------------------------------------------------

    def _on_theme(self, *_args):
        dark = self.style.get_dark()
        for name, tag in self.tags.items():
            color = Gdk.RGBA()
            color.parse(COLORS[name][1 if dark else 0])
            tag.set_property("background-rgba", color)

    def _on_changed(self, *_args):
        if not self.timer:
            self.timer = GLib.timeout_add(PAINT_DELAY_MS, self._on_timer)

    def _on_timer(self):
        self.timer = 0
        self._paint()
        return GLib.SOURCE_REMOVE

    def _paint(self):
        buffer = self.buffer
        first, last = buffer.get_bounds()
        found = spans(buffer.get_slice(first, last, True))
        if not found and not self.painted:
            return
        for tag in (self.dim, *self.tags.values()):
            buffer.remove_tag(tag, first, last)
        at = buffer.get_iter_at_offset
        for span in found:
            buffer.apply_tag(self.dim, at(span.start), at(span.inner))
            buffer.apply_tag(self.tags[span.color], at(span.inner), at(span.inner_end))
            buffer.apply_tag(self.dim, at(span.inner_end), at(span.end))
        self.painted = bool(found)


def install(group, marker):
    """The actions of the Mark menu, in the window's group."""

    def on_mark(_action, name):
        if name.get_string() in COLORS:
            marker.mark(name.get_string())

    mark = Gio.SimpleAction.new(ACTION, GLib.VariantType.new("s"))
    mark.connect("activate", on_mark)
    group.add_action(mark)
    unmark = Gio.SimpleAction.new(CLEAR, None)
    unmark.connect("activate", lambda *_args: marker.mark(None))
    group.add_action(unmark)


def menu_button(prefix):
    """The Mark menu: a colour to a row, and the way back."""
    popover = Gtk.Popover()
    rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

    def row(label, action, target=None, swatch=None):
        content = Gtk.Box(spacing=10)
        if swatch is not None:
            box = Gtk.Box(valign=Gtk.Align.CENTER)
            box.add_css_class("book-mark-swatch")
            box.add_css_class(swatch)
            content.append(box)
        content.append(Gtk.Label(label=label, xalign=0, hexpand=True))
        button = Gtk.Button(child=content, has_frame=False)
        button.set_action_name(f"{prefix}.{action}")
        if target is not None:
            button.set_action_target_value(GLib.Variant("s", target))
        button.connect("clicked", lambda _button: popover.popdown())
        rows.append(button)

    for name in COLORS:
        row(name.capitalize(), ACTION, name, name)
    rows.append(Gtk.Separator())
    row("Remove mark", CLEAR)
    popover.set_child(rows)
    button = Gtk.MenuButton(label="_Mark", use_underline=True, popover=popover)
    button.set_tooltip_text("Mark the selected text")
    return button
