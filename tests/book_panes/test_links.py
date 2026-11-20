"""Links in the text: which are found, and which the cursor is in."""

import time
import unittest

from book_panes import links, marks


def found(text):
    """The links of the text, each as its words and what follows them."""
    return [
        (text[link.start + 1 : link.inner_end], text[link.inner_end : link.end])
        for link in links.found(text, marks.code(text))
    ]


class FoundTest(unittest.TestCase):
    def test_a_link_is_its_words_and_where_they_lead(self):
        text = "see [the page](http://e.org/p?a=1#b) and [more](x.md)."
        self.assertEqual(
            found(text),
            [("the page", "](http://e.org/p?a=1#b)"), ("more", "](x.md)")],
        )
        self.assertEqual(links.found(text)[0], links.Link(4, 13, 36))

    def test_the_destination_may_have_a_title_brackets_and_parentheses(self):
        for tail in (
            '](u "a (title)")',
            "](u 'it')",
            "](u (it))",
            "](<a b.md>)",
            "](a_(b)_c)",
            "](a\\)b)",
            "]()",
            "]( u )",
            ']( "a b")',
            "]( 'a b')",
            "]( (a b))",
        ):
            with self.subTest(tail=tail):
                self.assertEqual(found(f"[w{tail} x"), [("w", tail)])

    def test_the_words_may_hold_brackets_and_marks(self):
        self.assertEqual(found("[a [b] c](u)"), [("a [b] c", "](u)")])
        self.assertEqual(found("[a \\] c](u)"), [("a \\] c", "](u)")])
        text = '[<mark class="yellow">a</mark>](u)'
        self.assertEqual(found(text), [('<mark class="yellow">a</mark>', "](u)")])

    def test_a_link_within_brackets_is_the_link(self):
        self.assertEqual(found("[a [b](u) c](v)"), [("b", "](u)")])
        self.assertEqual(found("[[b](u)]"), [("b", "](u)")])

    def test_brackets_pair_as_they_nest(self):
        self.assertEqual(found("[a [b [c] d] e](u)"), [("a [b [c] d] e", "](u)")])
        self.assertEqual(found("[a [b](x y) c](v)"), [("a [b](x y) c", "](v)")])
        self.assertEqual(found("[a ![b] c](u)"), [("a ![b] c", "](u)")])
        self.assertEqual(found("a] [b](u)"), [("b", "](u)")])
        self.assertEqual(found("[a [b](u)"), [("b", "](u)")])

    def test_a_backslash_makes_a_bracket_a_plain_one_wherever_it_stands(self):
        self.assertEqual(found("[a [b\\] c](u)"), [("b\\] c", "](u)")])
        self.assertEqual(found("\\[a]([c](d))"), [("c", "](d)")])
        self.assertEqual(found("[a\\](u) [b](v)"), [("b", "](v)")])

    def test_what_is_no_link_in_one_line_stays_as_written(self):
        for text in (
            "![a picture](p.png)",
            "[![a picture](p.png)](u)",
            "\\[no](link)",
            "[two\nlines](u)",
            "[a](two words)",
            "[a](u\n)",
            "[ ](u)",
            "[](u)",
            "[a][b]",
            "[a] (u)",
            "[^1] and (u)",
            "- [ ] a task (u)",
            "[a](u",
        ):
            with self.subTest(text=text):
                self.assertEqual(found(text), [])

    def test_a_backslash_before_a_backslash_escapes_nothing(self):
        self.assertEqual(found("\\\\[a](u)"), [("a", "](u)")])
        self.assertEqual(found("\\![a](u)"), [("a", "](u)")])
        self.assertEqual(found("\\\\![a](u)"), [])

    def test_code_holds_no_links(self):
        self.assertEqual(found("`[a](u)` [b](v)"), [("b", "](v)")])
        self.assertEqual(found("```\n[a](u)\n```\n[b](v)"), [("b", "](v)")])
        self.assertEqual(found("[a `](u)` b"), [])
        self.assertEqual(found("[`a`](u)"), [("`a`", "](u)")])
        self.assertEqual(found("[a `]` b](u)"), [("a `]` b", "](u)")])
        self.assertEqual(found("`[` a](u)"), [])

    def test_long_texts_are_read_in_one_pass(self):
        text = "word [a](u) [ and ( " * 20000
        self.assertEqual(len(found(text)), 20000)

    def test_a_link_that_never_closes_is_given_up_in_one_pass(self):
        started = time.process_time()
        for text in (
            "[x](" + " " * 50000 + "!",
            "[x](u" + " " * 50000 + "!",
            '[x](u "t"' + " " * 50000 + "!",
            "[x](" + "\t " * 50000,
            '[x](u "' + "a" * 50000,
            "[x](" + "(a" * 50000,
            "[x]( " * 50000,
            "[" + "[a] " * 50000,
            "\\[" * 50000 + "](",
            "[\\]" * 50000 + "](",
            "[" * 50000 + "](",
            "[" * 50000 + '](u "a ' * 50000,
        ):
            self.assertEqual(links.found(text), [])
        self.assertIsNone(links.piece("](" + " " * 50000))
        deep = "[" * 50000 + "a" + "]" * 50000 + "(u)"
        self.assertEqual(links.found(deep), [links.Link(0, 100000, 100004)])
        self.assertLess(time.process_time() - started, 2)

    def test_blanks_belong_to_one_part_of_the_tail(self):
        for tail in ("](  )", "](  u  )", '](u  "t"  )', "](<u>\t'a b'\t)"):
            with self.subTest(tail=tail):
                self.assertEqual(found(f"[w{tail} x"), [("w", tail)])
        for tail in ('](u "t" x)', "](u v)", '](u "t""t")'):
            with self.subTest(tail=tail):
                self.assertEqual(found(f"[w{tail} x"), [])


class AroundTest(unittest.TestCase):
    def setUp(self):
        self.text = "a [b](u)[c](v) d [e](w)"
        self.links = links.found(self.text)

    def words(self, low, high=None):
        return [
            self.text[link.start + 1 : link.inner_end]
            for link in links.around(self.links, low, low if high is None else high)
        ]

    def test_a_place_in_a_link_or_at_its_ends_is_in_it(self):
        self.assertEqual([self.words(place) for place in (1, 2, 5)], [[], ["b"], ["b"]])
        self.assertEqual([self.words(place) for place in (14, 15, 16)], [["c"], [], []])
        self.assertEqual([self.words(place) for place in (17, 23)], [["e"], ["e"]])

    def test_where_two_links_meet_a_place_is_in_both(self):
        self.assertEqual(self.words(8), ["b", "c"])

    def test_two_places_are_in_the_link_that_holds_both(self):
        self.assertEqual(self.words(3, 7), ["b"])
        self.assertEqual(self.words(3, 9), [])
        self.assertEqual(self.words(0, 3), [])

    def test_no_links_hold_nothing(self):
        self.assertEqual(links.around([], 0, 0), [])


class PieceTest(unittest.TestCase):
    def test_hidden_markup_is_a_bracket_or_a_tail(self):
        self.assertEqual(links.piece("[<mark>"), (1, True))
        self.assertEqual(links.piece("](u (t))</mark>"), (8, False))
        self.assertEqual(links.piece("x](u)", 1), (4, False))
        self.assertEqual(links.piece(']( "a b")'), (9, False))
        self.assertIsNone(links.piece("</mark>"))
        self.assertIsNone(links.piece("](u"))


if __name__ == "__main__":
    unittest.main()
