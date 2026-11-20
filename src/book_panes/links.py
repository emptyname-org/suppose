"""Links in the text: [the words](where they lead "a title").

The editor shows the words, in blue, and hides the rest (marks.py does,
as it hides the tags of marks). The cursor in a link, or at an end of it,
shows the link as it is written, to change it. So are shown and hidden
links written in one line, with their destination in parentheses; other
links and images stay as written. Code holds no links.

A line is read once, from its beginning: brackets pair as they nest, a
backslash makes the next character a plain one, brackets in code are
none. A pair with a destination behind it is a link, and then nothing
around it is one.
"""

import bisect
import re
from typing import NamedTuple

# light theme, dark theme
COLORS = ("#1a5fb4", "#78aeed")
# what a line is read by: a character made plain, a bracket (of an image)
SIGN = re.compile(r"\\.|!?\[|\]")
# Behind the words: the destination, in <> or without blanks, and a title;
# or a title alone. No two parts can take the same blanks more than once,
# so a tail that never closes is given up in one pass.
PART = r"(?:[^\s()\\]|\\.|\((?:[^\s()\\]|\\.)*\))"
TITLE = r"(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|\((?:[^()\\\n]|\\.)*\))"
TAIL = re.compile(
    r"\]\((?:"
    r"[ \t]*(?:(?:<[^<>\n]*>|" + PART + r"+)(?:[ \t]+(?:" + TITLE + r"[ \t]*)?)?)?"
    r"|[ \t]+" + TITLE + r"[ \t]*"
    r")\)"
)
OPENING = "["


class Link(NamedTuple):
    """A link in a text: where its bracket opens, where its words end,
    where it ends. The words begin behind the bracket."""

    start: int
    inner_end: int
    end: int


def in_code(literal, starts, place):
    index = bisect.bisect_right(starts, place) - 1
    return index >= 0 and place < literal[index][1]


def read_line(text, begin, end, literal, starts, links):
    """Add the links of the line from begin to end."""
    opened: list[tuple[int, bool]] = []  # brackets open: where, of an image
    place = begin
    while True:
        sign = SIGN.search(text, place, end)
        if sign is None:
            return
        place = sign.end()
        if sign.group()[0] == "\\" or in_code(literal, starts, place - 1):
            continue
        if sign.group() != "]":
            opened.append((place - 1, len(sign.group()) == 2))
            continue
        if not opened:
            continue
        start, image = opened.pop()
        tail = TAIL.match(text, place - 1, end)
        if tail is None:
            continue
        # an image, and a link without words, stay as written; like a
        # link, they hold no link and none holds them
        if not image and text[start + 1 : place - 1].strip():
            links.append(Link(start, place - 1, tail.end()))
        opened.clear()
        place = tail.end()


def found(text, literal=()):
    """The links of a text, in order. literal: its stretches of code, in
    order, each beginning with where it starts and ends."""
    links: list[Link] = []
    starts = [piece[0] for piece in literal]
    place = text.find("](")
    while place >= 0:
        begin = text.rfind("\n", 0, place) + 1
        end = text.find("\n", place)
        if end < 0:
            end = len(text)
        read_line(text, begin, end, literal, starts, links)
        place = text.find("](", end)
    return links


def around(links, low, high):
    """Of the links, in order: those the places low to high are in, or at
    an end of. Two, where one link ends and the next begins."""
    index = bisect.bisect_right(links, low, key=lambda link: link.start)
    return [
        link
        for link in links[max(index - 2, 0) : index]
        if link.start <= low and high <= link.end
    ]


def piece(text, place=0):
    """The markup of a link that begins at the place of a hidden stretch:
    its length, and whether it opens a link (the bracket) or closes one
    (the rest). None for anything else."""
    if text.startswith(OPENING, place):
        return len(OPENING), True
    tail = TAIL.match(text, place)
    if tail is not None:
        return tail.end() - place, False
    return None
