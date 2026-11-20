"""Text marked in colours, from the Mark menu beside Save.

A mark is in the text itself, as HTML that Markdown lets through:

    <mark class="yellow">marked text</mark>

so it stays with the file whoever changes it, and the agent reads it as
the writer does. The editor paints what is marked and hides the tags. The
preview paints it from its stylesheet and does not take a mark for HTML
it has to ask about; the tags are no words to count (patches/: base.css,
preview_converter.py, preview_security.py, stats_counter.py).

Marking goes by the selection; without one, by the mark the cursor is in.
One tag cannot hold several paragraphs, nor the sign a line begins with
(#, -, >, 1.): each line gets a mark of its own, behind its sign. Code,
between fences or between backticks, holds no marks: a tag there is text.
Not told from text: code that is only indented.

A hidden tag takes no place and is not typed into or deleted by hand.
Keys delete the text beside it; a mark whose text goes, goes with it; a
line broken within a mark breaks the mark; what is copied takes whole
marks along, and a tag pasted without its partner goes.
"""

import bisect
import itertools
import re
from contextlib import contextmanager
from typing import NamedTuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

# name -> background in the editor on a light theme, on a dark one
COLORS = {
    "yellow": ("#fff0a0", "#6a5c00"),
    "green": ("#c9f2c4", "#22592b"),
    "blue": ("#c7e2ff", "#1f4d78"),
    "pink": ("#ffd0e6", "#782650"),
    "orange": ("#ffd8ae", "#7a4810"),
}
NAMES = "|".join(COLORS)
TAG = re.compile(rf'<mark class="({NAMES})">|</mark>')
OPENING_END = re.compile(rf'<mark class="(?:{NAMES})">\Z')
CLOSING = "</mark>"
KEEP = "keep"  # for a colour: the one the text has
# what a line begins with before its text: indent, quote, list or heading sign
SIGN = re.compile(
    r"[ \t]*(?:>[ \t]?)*"
    r"(?:(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?|#{1,6}[ \t]+)?"
)
# a line no tag goes around: a rule, a row of a table
BARE = re.compile(r"(?:[-*_][ \t]*){3,}|\|.*")
# a line's indent and quote signs; behind them, a fence that opens a block
# of code, also as the first line of an item of a list
QUOTES = re.compile(r"[ \t]*(?:>[ \t]?)*")
FENCE = re.compile(r"[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)*(`{3,}|~{3,})(.*)")
TICKS = re.compile(r"`+")
ACTION = "mark"
CLEAR = "unmark"
CSS = """
.book-mark-swatch { min-width: 18px; min-height: 18px; border-radius: 5px; }
""" + "".join(
    f".book-mark-swatch.{name} {{ background-color: {light}; }}\n"
    for name, (light, _dark) in COLORS.items()
)


class Code(NamedTuple):
    """A stretch of a text that is code: a block with its fences, or a
    span with its backticks."""

    start: int
    end: int
    fenced: bool


class Span(NamedTuple):
    """A mark in a text: where its opening tag begins, its text begins and
    ends, its closing tag ends; its colour; how many marks are around it."""

    start: int
    inner: int
    inner_end: int
    end: int
    color: str
    depth: int


class Edit(NamedTuple):
    """Replace text[start:end] by new; then the selection is select[0] to
    select[1], offsets in the text as it is after."""

    start: int
    end: int
    new: str
    select: tuple[int, int]


def opening(color):
    return f'<mark class="{color}">'


LONGEST = max(len(opening(name)) for name in COLORS)


def ticked(text, start, end, found):
    """Add the spans of code of a paragraph: a run of backticks opens one
    that the next run as long closes."""
    runs = [(run.start(), run.end()) for run in TICKS.finditer(text, start, end)]
    if len(runs) < 2:
        return
    as_long: dict[int, list[int]] = {}
    for index, (first, last) in enumerate(runs):
        as_long.setdefault(last - first, []).append(index)
    index = 0
    while index < len(runs):
        first, last = runs[index]
        slashes = 0  # a backslash before it makes its first backtick a plain one
        while first - slashes > start and text[first - slashes - 1] == "\\":
            slashes += 1
        first += slashes % 2
        same = as_long.get(last - first, [])
        partner = bisect.bisect_right(same, index)
        if last > first and partner < len(same):
            found.append(Code(first, runs[same[partner]][1], False))
            index = same[partner] + 1
        else:
            index += 1


def code(text, fences=True):
    """The code of a text, in order: blocks between fences and spans
    between backticks. A block ends with a fence of the sign that opened
    it, as long or longer, or with the quote it stands in. Without fences:
    the spans alone, for a piece of a text that holds no block."""
    if "`" not in text and not (fences and "~~~" in text):
        return []
    found: list[Code] = []
    fence = None  # of the open block: its sign, its length, whether quoted
    block = paragraph = place = 0
    writing = False  # a paragraph is under way
    for line in text.split("\n"):
        quotes = QUOTES.match(line)
        assert quotes is not None
        quoted = ">" in quotes.group()
        rest = line[quotes.end() :]
        end = min(place + len(line) + 1, len(text))
        if fence is not None:
            sign, length, in_quote = fence
            if in_quote and not quoted:
                found.append(Code(block, place, True))
                fence = None  # and the line is one like any other
            else:
                last = rest.strip()
                if len(last) >= length and last == sign * len(last):
                    found.append(Code(block, end, True))
                    fence = None
                place = end
                continue
        opened = FENCE.match(rest) if fences else None
        if opened is not None and opened.group(1)[0] == "`" and "`" in opened.group(2):
            opened = None  # backticks of a span
        if opened is not None or not rest.strip():
            if writing:
                ticked(text, paragraph, place, found)
                writing = False
            if opened is not None:
                fence = (opened.group(1)[0], len(opened.group(1)), quoted)
                block = place
        elif not writing:
            writing = True
            paragraph = place
        place = end
    if fence is not None:
        found.append(Code(block, len(text), True))
    elif writing:
        ticked(text, paragraph, len(text), found)
    return found


def spans(text, literal=None):
    """The marks of a text, in order of where they begin. A mark may hold
    others. A tag without its partner is text, and so is a tag in code."""
    if CLOSING not in text:
        return []
    if literal is None:
        literal = code(text)
    pairs, opened, at = [], [], 0
    for tag in TAG.finditer(text):
        while at < len(literal) and literal[at].end <= tag.start():
            at += 1
        if at < len(literal) and literal[at].start <= tag.start():
            continue
        if tag.group(1):
            opened.append(tag)
        elif opened:
            first = opened.pop()
            pairs.append(
                (first.start(), first.end(), tag.start(), tag.end(), first.group(1))
            )
    pairs.sort()
    found = []
    around: list[int] = []  # where the marks around this one end
    for pair in pairs:
        while around and around[-1] <= pair[0]:
            around.pop()
        found.append(Span(*pair, len(around)))
        around.append(pair[3])
    return found


def tags_of(found):
    """The tags of marks, in order: where each begins and ends, the
    colour of an opening one, None for a closing one."""
    tags = []
    for span in found:
        tags.append((span.start, span.inner, span.color))
        tags.append((span.inner_end, span.end, None))
    tags.sort()
    return tags


def joined(ranges):
    """Ranges in order, those that touch as one."""
    whole: list[tuple[int, int]] = []
    for start, end in ranges:
        if whole and whole[-1][1] >= start:
            whole[-1] = (whole[-1][0], max(end, whole[-1][1]))
        elif end > start:
            whole.append((start, end))
    return whole


def plan(text):
    """What the editor shows of a text's marks, as ranges in order: for
    "hide" the tags, for "dim" the tags of marks without text, for each
    colour the text in it (the innermost mark's)."""
    shown: dict[str, list[tuple[int, int]]] = {
        name: [] for name in ("hide", "dim", *COLORS)
    }
    opened: list[list] = []  # of each mark around: its tag, its colour, text seen in it
    place = 0
    for start, end, color in tags_of(spans(text)):
        if opened and start > place:
            shown[opened[-1][1]].append((place, start))
            opened[-1][2] = True
        if color is not None:
            opened.append([(start, end), color, False])
        else:
            first, _color, full = opened.pop()
            shown["hide" if full else "dim"] += [first, (start, end)]
            if opened and full:
                opened[-1][2] = True
        place = end
    return {name: joined(sorted(ranges)) for name, ranges in shown.items()}


def recast(text, opened, closed, found=None):
    """The text with the tags of its marks replaced: by opened, in which
    {} stands for the colour, and by closed."""
    parts, taken = [], 0
    for start, end, color in tags_of(spans(text) if found is None else found):
        parts += [text[taken:start], closed if color is None else opened.format(color)]
        taken = end
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


def wrap(run, color, begins_line):
    """A run of text as marked in the colour, or as it is with None: its
    pieces in order, each with whether it is text of the run (tags are
    not). A line break within a span of code ends no line here."""
    if color is None:
        return [(run, True)]
    lines, taken, at = [], 0, 0
    literal = code(run, False)
    place = run.find("\n")
    while place >= 0:
        while at < len(literal) and literal[at].end <= place:
            at += 1
        if not (at < len(literal) and literal[at].start <= place):
            lines.append(run[taken : place + 1])
            taken = place + 1
        place = run.find("\n", place + 1)
    lines.append(run[taken:])
    pieces = []
    for line in lines:
        body = line.rstrip("\r\n")
        signed = SIGN.match(body) if begins_line else None
        sign = signed.end() if signed is not None else 0
        core = body[sign:].strip()
        if not core or (begins_line and BARE.fullmatch(body.strip())):
            pieces.append((line, True))
        else:
            at_core = body.index(core, sign)
            pieces += [
                (body[:at_core], True),
                (opening(color), False),
                (core, True),
                (CLOSING, False),
                (line[at_core + len(core) :], True),
            ]
        begins_line = line.endswith("\n")
    return pieces


def rebuilt(text, found, literal, start, end, color, cursor=None):
    """The change that gives text[start:end] the colour (None: none, KEEP:
    the one it has) and writes the marks it touches anew: one to a line,
    none within another, none in a block of code or around a tag that has
    no partner. None when that changes nothing. The selection after it is
    what was selected; with a cursor, that place, before the tags there."""
    tops = [
        span
        for span in found
        if not span.depth and span.start <= end and span.end >= start
    ]
    low = min(start, tops[0].start) if tops else start
    high = max(end, tops[-1].end) if tops else end
    tags = tags_of(span for span in found if low <= span.start and span.end <= high)
    # what takes no mark: blocks of code, and tags without a partner, which
    # a mark around them would give one
    paired = {place for span in found for place in (span.start, span.inner_end)}
    bare = [
        piece[:2]
        for piece in literal
        if piece.fenced and piece.start < high and piece.end > low
    ]
    at = 0
    for tag in TAG.finditer(text, low, high):
        while at < len(literal) and literal[at].end <= tag.start():
            at += 1
        in_code = at < len(literal) and literal[at].start <= tag.start()
        if tag.start() not in paired and not in_code:
            bare.append(tag.span())
    bare.sort()

    # the text of the stretch without its tags, in runs of one colour
    runs: list[tuple[list[str], str | None]] = []
    first = last = None  # of what is selected, counted in that text
    count = at = 0
    place = low
    around: list[str] = []  # the colours of the marks around the place
    for begins, ends, opened in [*tags, (high, high, "")]:
        cuts = {place, begins}
        cuts.update(cut for cut in (start, end) if place < cut < begins)
        while at < len(bare) and bare[at][1] <= place:
            at += 1
        ahead = at
        while ahead < len(bare) and bare[ahead][0] < begins:
            cuts.update(cut for cut in bare[ahead] if place < cut < begins)
            ahead += 1
        edges = sorted(cuts)
        for part, part_end in itertools.pairwise(edges):
            while at < len(bare) and bare[at][1] <= part:
                at += 1
            plain = at < len(bare) and bare[at][0] <= part
            chosen = start <= part and part_end <= end and not plain
            now = around[-1] if around else None
            if plain:
                now = None
            elif chosen and color != KEEP:
                now = color
            if chosen:
                first = count if first is None else first
                last = count + part_end - part
            if runs and runs[-1][1] == now:
                runs[-1][0].append(text[part:part_end])
            else:
                runs.append(([text[part:part_end]], now))
            count += part_end - part
        if ends > begins:
            if opened:
                around.append(opened)
            else:
                around.pop()
        place = ends
    if first is None or last is None:
        return None  # tags or code alone were selected

    pieces = []
    before = text[low - 1] if low else "\n"
    for parts, now in runs:
        run = "".join(parts)
        pieces += wrap(run, now, before == "\n")
        before = run[-1]
    new = "".join(piece for piece, _is_text in pieces)
    old = text[low:high]
    if new == old:
        return None
    # A tag that goes or comes can make code of what was none, a fence at
    # the beginning of a line, and other marks of the tags after it. Then
    # nothing is changed: the text without its marks must stay what it is.
    if unmarked(text[:low] + new + text[high:]) != recast(text, "", "", found):
        return None

    def placed(index, resting=False):
        """Where the index-th character of the stretch's text is now;
        resting: where a cursor before it rests, before the tags there."""
        offset = seen = rest = 0
        for piece, is_text in pieces:
            if is_text and piece:
                if index < seen + len(piece):
                    if resting and index == seen:
                        return low + rest
                    return low + offset + index - seen
                seen += len(piece)
                rest = offset + len(piece)
            offset += len(piece)
        return low + offset

    if cursor is None:
        select = (placed(first), placed(last - 1) + 1)
    else:
        cursor = min(max(cursor, low), high)
        index = cursor - low  # of the cursor, counted in the stretch's text
        for begins, ends, _opened in tags:
            if begins >= cursor:
                break
            index -= min(ends, cursor) - begins
        there = placed(index, True) if index < count else placed(count - 1) + 1
        select = (there, there)
    head = 0
    while head < min(len(old), len(new)) and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < min(len(old), len(new)) - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    return Edit(low + head, high - tail, new[head : len(new) - tail], select)


def edit(text, start, end, color):
    """The change that marks text[start:end] in the colour, or with None
    unmarks it. With start == end it is the mark around that place, the
    innermost. None when there is nothing to change."""
    start, end = sorted(min(max(place, 0), len(text)) for place in (start, end))
    literal = code(text)
    found = spans(text, literal)
    if start != end:
        # a tag within a run of backticks would make two runs of it
        while 0 < start < len(text) and text[start - 1] == text[start] == "`":
            start -= 1
        while 0 < end < len(text) and text[end - 1] == text[end] == "`":
            end += 1
        for piece in literal:  # a span of code takes no tag: it goes whole
            if not piece.fenced and piece.start < start < piece.end:
                start = piece.start
            if not piece.fenced and piece.start < end < piece.end:
                end = piece.end
        return rebuilt(text, found, literal, start, end, color)
    around = [span for span in found if span.start <= start <= span.end]
    if not around:
        return None
    mark = around[-1]
    within = sum(
        span.inner - span.start + span.end - span.inner_end
        for span in found
        if mark.inner <= span.start and span.end <= mark.inner_end
    )
    if mark.inner_end - mark.inner == within:  # a mark of nothing
        if color is not None:
            return None
        return Edit(
            mark.start, mark.end, text[mark.inner : mark.inner_end], (mark.start,) * 2
        )
    cursor = min(max(start, mark.inner), mark.inner_end)
    return rebuilt(text, found, literal, mark.inner, mark.inner_end, color, cursor)


def tidy(text, place):
    """The change that gives each line a mark of its own where the mark
    at the place runs over several lines; the cursor stays at the place.
    None where no mark does."""
    if CLOSING not in text:
        return None
    literal = code(text)
    found = spans(text, literal)
    around = [span for span in found if span.inner <= place <= span.inner_end]
    if not around or "\n" not in text[around[0].inner : around[0].inner_end]:
        return None
    return rebuilt(
        text, found, literal, around[0].inner, around[0].inner_end, KEEP, place
    )


WORD_LINES = 2  # around the cursor: where a word to delete is looked for first


def plain_twin(view):
    """A text view of the kind the editor is built on, with no window: a
    key tried on it tells what the key does to a text without marks."""
    kind = view.__gtype__
    while kind.name not in ("GtkSourceView", "GtkTextView"):
        kind = kind.parent
    twin = GObject.new(kind)
    twin.get_buffer().set_enable_undo(False)
    return twin


class Shown:
    """A stretch of the buffer as it shows, without the hidden tags, and
    which place of the buffer a place of that text is."""

    def __init__(self, buffer, hidden, low, high):
        at = buffer.get_iter_at_offset
        self.pieces = []  # (place in the text shown, place in the buffer, length)
        self.low = low
        parts = []
        length, place = 0, low
        for start, end in [*hidden, (high, high)]:
            start, end = min(max(start, low), high), min(max(end, low), high)
            if start > place:
                self.pieces.append((length, place, start - place))
                parts.append(buffer.get_slice(at(place), at(start), True))
                length += start - place
            place = max(place, end)
        self.text = "".join(parts)

    def offset(self, place):
        """The place of the buffer in the text shown."""
        for begins, start, length in self.pieces:
            if place <= start:
                return begins
            if place <= start + length:
                return begins + place - start
        return len(self.text)

    def place(self, offset):
        """The place of the text shown in the buffer, before what is
        hidden there."""
        for begins, start, length in self.pieces:
            if offset <= begins + length:
                return start + offset - begins
        return self.low


class Marker:
    """The marks of one editor: marking, painting what is marked, and
    keeping the hidden tags whole while the text is written by hand."""

    def __init__(self, view, style=None, plain=None):
        self.view = view
        self.plain = plain  # a view without marks, made when first needed
        self.buffer = buffer = view.get_buffer()
        self.timer = 0  # the paint that waits for the main loop
        self.stale = True  # the text changed since it was painted
        self.painted = False
        self.painting = False
        self.exact_depth = 0  # the text changes, and not by the writer's hand
        self.replaying = False  # ... by undo or redo
        self.steering = False  # the cursor is put where it shall stay
        self.acting = False  # within a user action
        self.written = False  # ... in which the writer changed the text
        self.lines = 0  # ... and the lines of the text when it began
        self.arrived: list[tuple] = []  # hidden tags that came with pasted text
        self.back = None  # where the cursor returns after a Delete past a tag
        self.hide = buffer.create_tag("book-mark-tag", invisible=True, editable=False)
        self.dim = buffer.create_tag("book-mark-empty", foreground="gray")
        self.tags = {name: buffer.create_tag("book-mark-" + name) for name in COLORS}
        self.shown = {"hide": self.hide, "dim": self.dim, **self.tags}
        self.style = style or Adw.StyleManager.get_default()
        self.handlers = [
            (self.style, self.style.connect("notify::dark", self._on_theme)),
            (buffer, buffer.connect("changed", self._on_changed)),
            (buffer, buffer.connect("begin-user-action", self._on_action_begin)),
            (buffer, buffer.connect("end-user-action", self._on_action_end)),
            (buffer, buffer.connect("apply-tag", self._on_tagged)),
            (buffer, buffer.connect_after("delete-range", self._on_deleted)),
            (buffer, buffer.connect_after("mark-set", self._on_mark_set)),
            (buffer, buffer.connect("undo", self._on_replay, True)),
            (buffer, buffer.connect_after("undo", self._on_replay, False)),
            (buffer, buffer.connect("redo", self._on_replay, True)),
            (buffer, buffer.connect_after("redo", self._on_replay, False)),
            (view, view.connect("backspace", self._on_backspace)),
            (view, view.connect("delete-from-cursor", self._on_delete)),
            (view, view.connect_after("delete-from-cursor", self._on_delete_done)),
            (view, view.connect("copy-clipboard", self._on_copy)),
            (view, view.connect("cut-clipboard", self._on_copy)),
        ]
        self._on_theme()
        self._paint()

    def close(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        for source, handler in self.handlers:
            source.disconnect(handler)
        self.handlers = []
        self.plain = None
        self._forget_arrived()
        self._forget_back()

    @contextmanager
    def exact(self):
        """What changes the text within is not written by hand and stays
        as it is made: an agent's edit."""
        self.exact_depth += 1
        try:
            yield
        finally:
            self.exact_depth -= 1

    @contextmanager
    def _own(self):
        """For a change of the marker's own: exact; Apostrophe completes
        nothing in what is inserted, and its Hemingway mode, which takes
        no text away, lets a tag go."""
        buffer = self.buffer
        hemingway = getattr(buffer, "hemingway_mode", False)
        pasting = getattr(buffer, "paste_ongoing", False)
        if hemingway:
            buffer.hemingway_mode = False
        buffer.paste_ongoing = True
        try:
            with self.exact():
                yield
        finally:
            buffer.paste_ongoing = pasting
            if hemingway:
                buffer.hemingway_mode = True

    def _by_hand(self):
        """The writer changes the text: no agent, no undo, and no file
        that is loaded (Apostrophe's buffer tells, patches/)."""
        return not (
            self.exact_depth
            or self.replaying
            or getattr(self.buffer, "setting_text", False)
        )

    def _text(self):
        return self.buffer.get_slice(*self.buffer.get_bounds(), True)

    # ---- marking -------------------------------------------------------------

    def mark(self, color):
        """Mark the selection, or the mark the cursor is in; None unmarks.
        A beep when there is nothing to mark."""
        view, buffer = self.view, self.buffer
        change = None
        if view.get_editable():
            start = end = buffer.get_property("cursor-position")
            if buffer.get_has_selection():
                start, end = (
                    place.get_offset() for place in buffer.get_selection_bounds()
                )
            change = edit(self._text(), start, end, color)
        if change is None:
            view.get_display().beep()
            return
        self._apply(change, True)
        view.grab_focus()

    def _apply(self, change, as_action):
        """Make the change, as one undo step or within the one under way."""
        buffer = self.buffer
        at = buffer.get_iter_at_offset
        with self._own():
            if as_action:
                buffer.begin_user_action()
            try:
                buffer.delete(at(change.start), at(change.end))
                buffer.insert(at(change.start), change.new)
            finally:
                if as_action:
                    buffer.end_user_action()  # paints
        buffer.select_range(at(change.select[0]), at(change.select[1]))

    # ---- writing by hand around hidden tags -----------------------------------

    def _on_action_begin(self, buffer):
        self.acting = True
        self.written = False
        self.lines = buffer.get_line_count()

    def _on_action_end(self, _buffer):
        """Still within its undo step: what the writer's action left of
        marks is put right. And the text is painted at once: no tag is
        seen or typed into."""
        self.acting = False
        if self.written and self._by_hand():
            self._settle()
        self._forget_arrived()
        self._paint_if_stale()

    def _on_replay(self, _buffer, begins):
        self.replaying = begins
        if not begins:
            self._paint_if_stale()

    def _on_tagged(self, buffer, tag, start, end):
        """A hidden tag came with text copied in the editor."""
        if tag is self.hide and self.acting and not self.painting and self._by_hand():
            self.arrived.append(
                (  # both stay where they are when more is inserted behind
                    buffer.create_mark(None, start, True),
                    buffer.create_mark(None, end, True),
                )
            )

    def _forget_arrived(self):
        for marks in self.arrived:
            for mark in marks:
                self.buffer.delete_mark(mark)
        self.arrived = []

    def _strays(self):
        """Of the hidden tags that came with pasted text: those without
        their partner, and what is no whole tag; last first."""
        buffer = self.buffer
        # (start, end, whether an opening tag; None: no whole tag)
        pieces: list[tuple[int, int, bool | None]] = []
        for first, last in self.arrived:
            start = buffer.get_iter_at_mark(first)
            base, seen = start.get_offset(), 0
            came = buffer.get_slice(start, buffer.get_iter_at_mark(last), True)
            for tag in TAG.finditer(came):
                if tag.start() > seen:
                    pieces.append((base + seen, base + tag.start(), None))
                pieces.append(
                    (base + tag.start(), base + tag.end(), bool(tag.group(1)))
                )
                seen = tag.end()
            if len(came) > seen:
                pieces.append((base + seen, base + len(came), None))
        strays, opened = [], []  # type: list, list
        for piece in sorted(pieces, key=lambda piece: piece[:2]):
            if piece[2] is None:
                strays.append(piece)
            elif piece[2]:
                opened.append(piece)
            elif opened:
                opened.pop()
            else:
                strays.append(piece)
        return sorted(strays + opened, reverse=True)

    def _settle(self):
        """After the writer changed the text: tags that came without
        their partners go, and a mark a new line runs through is broken
        there."""
        buffer = self.buffer
        at = buffer.get_iter_at_offset
        strays = self._strays()
        self._forget_arrived()
        with self._own():
            for start, end, _kind in strays:
                buffer.delete(at(start), at(end))
        if buffer.get_line_count() > self.lines and not buffer.get_has_selection():
            change = tidy(self._text(), buffer.get_property("cursor-position"))
            if change is not None:
                self._apply(change, False)

    def _on_deleted(self, buffer, start, end):
        """A mark whose text was deleted goes with it, at once: nothing
        can be typed between two hidden tags."""
        if not self.acting or not self._by_hand():
            return
        before = start.copy()
        if not (
            start.has_tag(self.hide)
            and before.backward_char()
            and before.has_tag(self.hide)
        ):
            return
        while True:
            low = self._opening_before(start, 1)
            high = self._closing_after(start, 1)
            if low.equal(start) or high.equal(start):
                break
            with self._own():
                buffer.delete(low, high)
            for bound in (start, end):  # GTK goes on from them
                bound.assign(low)
        # still between tags, of two marks: the cursor rests before them
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        if cursor.equal(start) and not buffer.get_has_selection():
            self._steer(self._hidden_before(cursor))

    def _on_mark_set(self, buffer, _place, mark):
        """The cursor rests before hidden tags, wherever it is put: what
        is typed at the end of a mark belongs to it, at its beginning
        not."""
        if self.steering or mark.get_name() != "insert" or buffer.get_has_selection():
            return
        self._steer(self._hidden_before(buffer.get_iter_at_mark(mark)))

    def _steer(self, place, bound=None):
        """Put the cursor to the place, to stay; nowhere with None."""
        if place is None:
            return
        self.steering = True
        try:
            self.buffer.select_range(place, bound or place)
        finally:
            self.steering = False

    def _opening_before(self, place, most=-1):
        """Where the hidden opening tags right before the place begin; no
        more of them than most."""
        buffer = self.buffer
        at = buffer.get_iter_at_offset
        while most:
            before = place.copy()
            if not before.backward_char() or not before.has_tag(self.hide):
                break
            offset = place.get_offset()
            opened = OPENING_END.search(
                buffer.get_slice(at(max(0, offset - LONGEST)), place, True)
            )
            if opened is None:
                break
            place = at(offset - len(opened.group()))
            most -= 1
        return place

    def _closing_after(self, place, most=-1):
        """Where the hidden closing tags right at the place end; no more
        of them than most."""
        buffer = self.buffer
        while most and place.has_tag(self.hide):
            after = buffer.get_iter_at_offset(place.get_offset() + len(CLOSING))
            if buffer.get_slice(place, after, True) != CLOSING:
                break
            place = after
            most -= 1
        return place

    def _hidden_before(self, place):
        """Where the hidden tags right before the place begin, or None."""
        before = place.copy()
        if not before.backward_char() or not before.has_tag(self.hide):
            return None
        if not before.starts_tag(self.hide):
            before.backward_to_tag_toggle(self.hide)
        return before

    def _hidden_after(self, place):
        """Where the hidden tags right at the place end, or None."""
        if not place.has_tag(self.hide):
            return None
        after = place.copy()
        after.forward_to_tag_toggle(self.hide)
        return after

    def _on_backspace(self, _view):
        """Backspace behind a hidden tag deletes the text before the tag."""
        buffer = self.buffer
        if not buffer.get_has_selection():
            self._steer(
                self._hidden_before(buffer.get_iter_at_mark(buffer.get_insert()))
            )

    def _on_delete(self, view, kind, count):
        """Delete before a hidden tag deletes the text behind the tag,
        and the cursor stays where it is. Words are deleted as they
        show."""
        buffer = self.buffer
        self._forget_back()
        if buffer.get_has_selection() or kind not in (
            Gtk.DeleteType.CHARS,
            Gtk.DeleteType.WORD_ENDS,
        ):
            return
        if kind == Gtk.DeleteType.WORD_ENDS and count and self._delete_words(count):
            view.stop_emission_by_name("delete-from-cursor")
            return
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        if count < 0:
            self._steer(self._hidden_before(cursor))
        elif count > 0:
            after = self._hidden_after(cursor)
            if after is not None:
                self.back = buffer.create_mark(None, cursor, True)
                self._steer(after)

    def _delete_words(self, count):
        """Delete the words an editor without marks deletes from the text
        as it shows: a hidden tag ends no word. False where no hidden tag
        is in the way; the editor deletes there itself."""
        buffer = self.buffer
        hidden = self._ranges(self.hide)
        if not hidden:
            return False
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        line, size = cursor.get_line(), buffer.get_char_count()
        near = (
            buffer.get_iter_at_line(max(0, line - WORD_LINES))[1].get_offset(),
            buffer.get_iter_at_line(line + WORD_LINES + 1)[1].get_offset(),
        )
        for low, high in (near, (0, size)):
            shown = Shown(buffer, hidden, low, high)
            first, last = self._plain_words(
                shown.text, shown.offset(cursor.get_offset()), count
            )
            # words that reach the edge may go on beyond it: look at it all
            if (first > 0 or low == 0) and (last < len(shown.text) or high == size):
                break
        start, end = shown.place(first), shown.place(last)
        if first == last or not any(
            begins <= end and ends >= start for begins, ends in hidden
        ):
            return False
        at = buffer.get_iter_at_offset
        buffer.begin_user_action()
        buffer.delete_interactive(at(start), at(end), self.view.get_editable())
        buffer.end_user_action()
        self.view.scroll_mark_onscreen(buffer.get_insert())
        return True

    def _plain_words(self, text, cursor, count):
        """What the deletion of words takes from the text without marks,
        the cursor at the place: from where to where."""
        if self.plain is None:
            self.plain = plain_twin(self.view)
        plain = self.plain.get_buffer()
        plain.set_text(text)
        plain.place_cursor(plain.get_iter_at_offset(cursor))
        self.plain.emit("delete-from-cursor", Gtk.DeleteType.WORD_ENDS, count)
        first = plain.get_iter_at_mark(plain.get_insert()).get_offset()
        return first, first + len(text) - plain.get_char_count()

    def _on_delete_done(self, *_args):
        if self.back is not None:
            self._steer(self.buffer.get_iter_at_mark(self.back))
            self._forget_back()

    def _forget_back(self):
        if self.back is not None:
            self.buffer.delete_mark(self.back)
            self.back = None

    def _on_copy(self, _view):
        """What is copied or cut takes along the hidden tags of the marks
        it begins or ends with, so a mark selected as it shows is copied
        whole."""
        buffer = self.buffer
        if not buffer.get_has_selection():
            return
        start, end = buffer.get_selection_bounds()
        low = self._opening_before(start)
        high = self._closing_after(end)
        if low.equal(start) and high.equal(end):
            return
        if buffer.get_iter_at_mark(buffer.get_insert()).equal(start):
            self._steer(low, high)
        else:
            self._steer(high, low)

    # ---- painting ------------------------------------------------------------

    def _on_theme(self, *_args):
        dark = self.style.get_dark()
        for name, tag in self.tags.items():
            color = Gdk.RGBA()
            color.parse(COLORS[name][1 if dark else 0])
            tag.set_property("background-rgba", color)

    def _on_changed(self, *_args):
        self.stale = True
        if self.acting:  # painted when the action ends
            self.written = self.written or self._by_hand()
        elif not self.timer and not self.replaying:  # before the view is drawn
            self.timer = GLib.idle_add(self._on_idle, priority=GLib.PRIORITY_HIGH_IDLE)

    def _on_idle(self):
        self.timer = 0
        self._paint_if_stale()
        return GLib.SOURCE_REMOVE

    def _paint_if_stale(self):
        if self.stale:
            self._paint()

    def _ranges(self, tag):
        """Where the text carries the tag now, as ranges in order."""
        buffer = self.buffer
        place = buffer.get_start_iter()
        ranges, on, start = [], place.has_tag(tag), 0
        while True:
            moved = place.forward_to_tag_toggle(tag)
            here = place.get_offset() if moved else buffer.get_char_count()
            if on and here > start:
                ranges.append((start, here))
            if not moved:
                return ranges
            on, start = not on, here

    def _paint(self):
        """Give the text the tags its marks ask for; only where they
        differ from what it carries, so the view lays out no more anew."""
        buffer = self.buffer
        self.stale = False
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        text = self._text()
        if not self.painted and CLOSING not in text:
            return
        wanted = plan(text)
        at = buffer.get_iter_at_offset
        self.painting = True
        try:
            for name, tag in self.shown.items():
                want, have = wanted[name], self._ranges(tag)
                head = 0
                while head < min(len(want), len(have)) and want[head] == have[head]:
                    head += 1
                tail = 0
                while (
                    tail < min(len(want), len(have)) - head
                    and want[-1 - tail] == have[-1 - tail]
                ):
                    tail += 1
                old = have[head : len(have) - tail]
                if old:
                    buffer.remove_tag(tag, at(old[0][0]), at(old[-1][1]))
                for start, end in want[head : len(want) - tail]:
                    buffer.apply_tag(tag, at(start), at(end))
        finally:
            self.painting = False
        self.painted = any(wanted.values())


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
