"""Links in the text: [the words](where they lead "a title").

The editor shows the words, in blue, and hides the rest (marks.py does,
as it hides the tags of marks). The cursor in a link, or at an end of it,
shows the link as it is written, to change it. So are shown and hidden
links written in one line, with their destination in parentheses; other
links and images stay as written. Code holds no links.
"""

import bisect
import re
from typing import NamedTuple

# light theme, dark theme
COLORS = ("#1a5fb4", "#78aeed")
# the words: one line; a pair of brackets within, unless a link of its own
WORDS = r"(?:[^\[\]\n\\]|\\.|\[[^\[\]\n]*\](?!\())+"
# behind the words: the destination, in <> or without blanks, and a title
TAIL = re.compile(
    r"\]\([ \t]*"
    r"(?:<[^<>\n]*>|(?:[^\s()\\]|\\.|\((?:[^\s()\\]|\\.)*\))*)"
    r"(?:[ \t]+(?:\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|\((?:[^()\\\n]|\\.)*\)))?"
    r"[ \t]*\)"
)
LINK = re.compile(r"\[(?P<words>" + WORDS + ")" + TAIL.pattern)
OPENING = "["


class Link(NamedTuple):
    """A link in a text: where its bracket opens, where its words end,
    where it ends. The words begin behind the bracket."""

    start: int
    inner_end: int
    end: int


def plain(text, place):
    """Whether a backslash makes the character at the place a plain one."""
    slashes = 0
    while place > slashes and text[place - slashes - 1] == "\\":
        slashes += 1
    return slashes % 2 == 1


def in_code(literal, starts, place):
    index = bisect.bisect_right(starts, place) - 1
    return index >= 0 and place < literal[index][1]


def found(text, literal=()):
    """The links of a text, in order. literal: its stretches of code, in
    order, each beginning with where it starts and ends."""
    if "](" not in text:
        return []
    starts = [piece[0] for piece in literal]
    links = []
    for match in LINK.finditer(text):
        start, inner_end = match.start(), match.end("words")
        if plain(text, start) or not match.group("words").strip():
            continue
        if start and text[start - 1] == "!" and not plain(text, start - 1):
            continue  # an image
        if in_code(literal, starts, start) or in_code(literal, starts, inner_end):
            continue
        links.append(Link(start, inner_end, match.end()))
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
