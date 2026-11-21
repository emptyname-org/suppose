"""Colours chosen in Preferences: what a setting names, the editor's style
sheet, the terminal's colours, the rows."""

import unittest
from types import SimpleNamespace
from unittest import mock

from book_panes import colors

from .test_fonts import LargeTextRow, Provider, Widget
from .test_fonts import Settings as FontSettings

KEYS = (
    colors.EDITOR_TEXT_KEY,
    colors.EDITOR_GROUND_KEY,
    colors.TERMINAL_TEXT_KEY,
    colors.TERMINAL_GROUND_KEY,
)
LIGHT = colors.EDITOR_COLORS["light"]


class Settings(FontSettings):
    """Gio.Settings of the four keys and of Apostrophe's theme."""

    def __init__(self, **values):
        super().__init__(**{colors.THEME_KEY: "system", **dict.fromkeys(KEYS, "")})
        self.values.update(values)


class Style:
    """The application's style: light, until told otherwise."""

    def __init__(self, dark=False):
        self.dark = dark
        self.handlers = {}
        self.serial = 0

    def get_dark(self):
        return self.dark

    def connect(self, _signal, handler):
        self.serial += 1
        self.handlers[self.serial] = handler
        return self.serial

    def disconnect(self, handler):
        del self.handlers[handler]

    def turn(self, dark):
        self.dark = dark
        for handler in list(self.handlers.values()):
            handler(self, None)


class ColorButton(Widget):
    color = None

    def set_rgba(self, color):
        self.color = color
        self.handlers["notify::rgba"](self, None)

    def get_rgba(self):
        return self.color

    def shown(self):
        return colors.chosen(self.color.to_string())


def toolkit():
    """Gtk and Adw as far as the colours need them, without a display."""
    added = []
    gtk = SimpleNamespace(
        ColorDialog=Widget,
        ColorDialogButton=ColorButton,
        Button=Widget,
        Align=SimpleNamespace(CENTER="center"),
        CssProvider=Provider,
        StyleContext=SimpleNamespace(
            add_provider_for_display=lambda *given: added.append(given)
        ),
        STYLE_PROVIDER_PRIORITY_USER=800,
        added=added,
    )
    adw = SimpleNamespace(
        ActionRow=Widget, PreferencesGroup=Widget, PreferencesPage=object
    )
    return gtk, adw


class ChosenTest(unittest.TestCase):
    def test_nothing_chosen_is_the_theme(self):
        self.assertIsNone(colors.chosen(""))
        self.assertIsNone(colors.chosen("  \n"))

    def test_a_colour_is_told_in_six_digits_however_it_is_written(self):
        for written, told in (
            ("#ABCDEF", "#abcdef"),
            (" #fff ", "#ffffff"),
            ("rgb(255,0,128)", "#ff0080"),
            ("red", "#ff0000"),
        ):
            with self.subTest(written=written):
                self.assertEqual(colors.chosen(written), told)

    def test_a_colour_one_sees_through_is_taken_whole(self):
        self.assertEqual(colors.chosen("rgba(1,2,3,0.5)"), "#010203")

    def test_what_names_no_colour_is_the_theme(self):
        for written in ("nonsense", "#12", "12", "red; } * { color: blue", "#fff }"):
            with self.subTest(written=written):
                self.assertIsNone(colors.chosen(written))


class ContrastTest(unittest.TestCase):
    def test_two_colours_are_told_apart_from_not_at_all_to_black_on_white(self):
        self.assertEqual(colors.contrast("#808080", "#808080"), 1)
        self.assertAlmostEqual(colors.contrast("#000000", "#ffffff"), 21)
        self.assertEqual(
            colors.contrast("#400040", "#ffff00"), colors.contrast("#ffff00", "#400040")
        )

    def test_a_dark_colour_is_one_white_is_read_on_better_than_black(self):
        for color in ("#000000", "#400040", "#002b36", "#2d2d2d", "#707070"):
            with self.subTest(color=color):
                self.assertTrue(colors.is_dark(color))
        for color in ("#ffffff", "#f9f3e9", "#ffff00", "#808080"):
            with self.subTest(color=color):
                self.assertFalse(colors.is_dark(color))

    def test_a_colour_goes_with_the_theme_whose_colours_it_is_told_from(self):
        pairs = [("#fff0a0", "#6a5c00"), ("#c7e2ff", "#1f4d78")]
        for color, dark in (
            ("#2e3436", False),
            ("#ffffff", True),
            ("#808080", False),  # no guess by its lightness alone
            ("#b0b0b0", True),
        ):
            with self.subTest(color=color):
                self.assertIs(colors.suits_dark(color, pairs), dark)

    def test_the_one_told_worst_decides(self):
        # told well from one dark colour, not at all from the other
        pairs = [("#c0c0c0", "#000000"), ("#c0c0c0", "#ffffff")]
        self.assertFalse(colors.suits_dark("#ffffff", pairs))


class ThemeTest(unittest.TestCase):
    def test_the_theme_is_the_one_named_or_the_system_s(self):
        for name, dark, theme in (
            ("light", False, "light"),
            ("sepia", False, "sepia"),
            ("dark", True, "dark"),
            ("light", True, "light"),  # named before the style follows
            ("system", False, "light"),
            ("system", True, "dark"),
            ("unknown", True, "dark"),
        ):
            with self.subTest(name=name, dark=dark):
                settings = Settings(**{colors.THEME_KEY: name})
                self.assertEqual(colors.theme(settings, Style(dark)), theme)


class CssTest(unittest.TestCase):
    def test_nothing_chosen_is_no_style_sheet(self):
        self.assertEqual(colors.editor_css(None, None), "")

    def test_a_text_colour_is_the_editor_s_its_cursor_s_and_the_bar_s(self):
        sheet = colors.editor_css("#112233", None)
        self.assertIn(
            "textview.apostrophe-editor, .bottombar"
            " { color: #112233; caret-color: #112233; }",
            sheet,
        )
        self.assertNotIn("background-color", sheet)

    def test_a_background_is_the_editor_s_and_the_bar_s(self):
        sheet = colors.editor_css(None, "#400040")
        self.assertIn(
            "textview.apostrophe-editor, textview.apostrophe-editor > text,"
            " .stats-button { background-color: #400040; }",
            sheet,
        )
        self.assertIn(
            ".bottombar_background.shown box"
            " { background-color: alpha(#400040, 0.95); }",
            sheet,
        )
        self.assertNotIn(" color:", sheet)

    def test_code_and_dimmed_markup_are_a_share_of_the_chosen_text(self):
        for ground in ("#400040", None):
            with self.subTest(ground=ground):
                sheet = colors.editor_css("#ffff00", ground)
                self.assertIn(
                    "@define-color code_bg_color rgba(255, 255, 0, 0.12);", sheet
                )
                self.assertIn(
                    "@define-color link_fg_color rgba(255, 255, 0, 0.4);", sheet
                )

    def test_with_the_theme_s_text_they_are_what_is_read_on_the_background(self):
        dark = colors.editor_css(None, "#400040")
        self.assertIn("@define-color code_bg_color rgba(255, 255, 255, 0.12);", dark)
        self.assertIn("@define-color link_fg_color rgba(255, 255, 255, 0.4);", dark)
        light = colors.editor_css(None, "#fdf6e3")
        self.assertIn("@define-color code_bg_color rgba(0, 0, 0, 0.12);", light)
        self.assertIn("@define-color link_fg_color rgba(0, 0, 0, 0.4);", light)

    def test_a_text_colour_alone_guesses_no_background(self):
        # laid over the background there is: under white text a style sheet
        # of the user's may have put a black one, under grey text the
        # theme's white one stays
        white = colors.editor_css("#ffffff", None)
        self.assertIn("@define-color code_bg_color rgba(255, 255, 255, 0.12);", white)
        grey = colors.editor_css("#808080", None)
        self.assertIn("@define-color code_bg_color rgba(128, 128, 128, 0.12);", grey)
        self.assertIn("@define-color link_fg_color rgba(128, 128, 128, 0.4);", grey)
        self.assertNotIn("255", grey)

    def test_the_style_sheet_is_one_gtk_reads(self):
        from gi.repository import Gtk

        provider = Gtk.CssProvider()
        errors = []
        provider.connect("parsing-error", lambda *given: errors.append(given))
        for text, ground in (
            ("#ffff00", "#400040"),
            ("#ffff00", None),
            (None, "#ffffff"),
        ):
            provider.load_from_string(colors.editor_css(text, ground))
        self.assertEqual(errors, [])


class Faked(unittest.TestCase):
    """With the toolkit's widgets replaced."""

    def setUp(self):
        self.gtk, self.adw = toolkit()
        for name, fake in (("Gtk", self.gtk), ("Adw", self.adw)):
            patch = mock.patch.object(colors, name, fake)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.dict(colors.PROVIDERS, clear=True)
        patch.start()
        self.addCleanup(patch.stop)


class UseTest(Faked):
    def setUp(self):
        super().setUp()
        self.updated = []
        self.view = SimpleNamespace(
            get_display=lambda: "display",
            markup=SimpleNamespace(
                on_style_updated=lambda: self.updated.append(
                    colors.PROVIDERS["display"].loaded
                )
            ),
        )

    def test_the_editor_takes_the_chosen_colours(self):
        both = colors.use_in_editor(self.view, "#FFFF00", "#400040")
        provider = colors.PROVIDERS["display"]
        self.assertEqual(provider.loaded, colors.editor_css("#ffff00", "#400040"))
        self.assertEqual(both, ("#ffff00", "#400040"))
        # above the user's rank, and Apostrophe's tags after the sheet
        self.assertEqual(self.gtk.added, [("display", provider, 801)])
        self.assertEqual(self.updated, [provider.loaded])

    def test_the_editor_tells_the_colours_it_has(self):
        use = colors.use_in_editor
        self.assertEqual(use(self.view, "", "#F9F3E9"), (None, "#f9f3e9"))
        self.assertEqual(use(self.view, "red", "no colour"), ("#ff0000", None))
        self.assertEqual(use(self.view, "", "no colour"), (None, None))

    def test_the_editor_gets_the_theme_back(self):
        colors.use_in_editor(self.view, "#ffff00", "#400040")
        self.assertEqual(colors.use_in_editor(self.view, "", ""), (None, None))
        self.assertEqual(colors.PROVIDERS["display"].loaded, "")
        self.assertEqual(len(self.gtk.added), 1)  # one provider a display
        self.assertEqual(self.updated[-1], "")

    def given(self, text, ground, dark):
        """What the terminal is given: text, background, palette."""
        taken = []
        terminal = SimpleNamespace(set_colors=lambda *colors_: taken.append(colors_))
        colors.use_in_terminal(terminal, text, ground, dark)
        ((front, back, palette),) = taken
        return (
            colors.chosen(front.to_string()),
            colors.chosen(back.to_string()),
            tuple(colors.chosen(color.to_string()) for color in palette),
        )

    def test_the_terminal_has_the_theme_while_nothing_is_chosen(self):
        for dark in (False, True):
            with self.subTest(dark=dark):
                self.assertEqual(self.given("", "", dark), colors.TERMINAL_COLORS[dark])

    def test_the_terminal_takes_the_chosen_colours(self):
        text, ground, palette = self.given("#93a1a1", "#002b36", False)
        self.assertEqual((text, ground), ("#93a1a1", "#002b36"))
        # the other colours are those read on a dark background
        self.assertEqual(palette, colors.TERMINAL_COLORS[True][2])

    def test_a_colour_chosen_alone_leaves_the_other_to_the_theme(self):
        own_text, own_ground, own_palette = colors.TERMINAL_COLORS[True]
        self.assertEqual(
            self.given("#93a1a1", "", True), ("#93a1a1", own_ground, own_palette)
        )
        self.assertEqual(
            self.given("", "#fdf6e3", True),
            (own_text, "#fdf6e3", colors.TERMINAL_COLORS[False][2]),
        )


class RowsTest(Faked):
    def rows(self, dark=False, **values):
        self.settings = Settings(**values)
        self.style = Style(dark)
        self.page = Widget()
        self.dialog = Widget()
        rows = colors.add_rows(
            self.dialog, self.settings, LargeTextRow(self.page), self.style
        )
        for row in rows.values():
            row.button.emit("realize")  # Preferences come on the screen
        return rows

    def shown(self, rows):
        return [row.button.shown() for row in rows.values()]

    def test_the_rows_are_a_group_of_their_own(self):
        rows = self.rows()
        (group,) = self.page.children
        self.assertEqual(group.properties["title"], "Colors")
        self.assertEqual(
            [row.properties["title"] for row in group.children],
            ["Editor text", "Editor background", "Agent text", "Agent background"],
        )
        self.assertEqual(tuple(rows), KEYS)
        self.assertIs(self.dialog.color_rows, rows)
        for row in rows.values():
            self.assertIs(
                row.button.properties["dialog"].properties["with_alpha"], False
            )

    def test_nothing_chosen_shows_the_theme_and_sets_nothing(self):
        rows = self.rows()
        self.assertEqual(self.shown(rows), [*LIGHT, *colors.TERMINAL_COLORS[False][:2]])
        self.assertFalse(any(row.reset.sensitive for row in rows.values()))
        self.assertEqual([self.settings.values[key] for key in KEYS], [""] * 4)

    def test_the_theme_shown_is_the_one_in_use(self):
        rows = self.rows(True, **{colors.THEME_KEY: "dark"})
        self.assertEqual(
            self.shown(rows),
            [*colors.EDITOR_COLORS["dark"], *colors.TERMINAL_COLORS[True][:2]],
        )

    def test_a_chosen_colour_is_shown(self):
        rows = self.rows(**{colors.EDITOR_GROUND_KEY: "#400040"})
        row = rows[colors.EDITOR_GROUND_KEY]
        self.assertEqual(row.button.shown(), "#400040")
        self.assertTrue(row.reset.sensitive)
        self.assertFalse(rows[colors.EDITOR_TEXT_KEY].reset.sensitive)

    def test_what_names_no_colour_shows_the_theme(self):
        rows = self.rows(**{colors.EDITOR_TEXT_KEY: "no colour"})
        row = rows[colors.EDITOR_TEXT_KEY]
        self.assertEqual(row.button.shown(), LIGHT[0])
        self.assertFalse(row.reset.sensitive)
        self.assertEqual(self.settings.values[colors.EDITOR_TEXT_KEY], "no colour")

    def test_choosing_sets_the_key_of_the_row_alone(self):
        rows = self.rows()
        rows[colors.TERMINAL_TEXT_KEY].button.set_rgba(colors.rgba("#93A1A1"))
        self.assertEqual(
            [self.settings.values[key] for key in KEYS], ["", "", "#93a1a1", ""]
        )
        self.assertTrue(rows[colors.TERMINAL_TEXT_KEY].reset.sensitive)

    def test_choosing_the_colour_shown_sets_nothing(self):
        rows = self.rows()
        rows[colors.EDITOR_TEXT_KEY].button.set_rgba(colors.rgba(LIGHT[0]))
        self.assertEqual(self.settings.values[colors.EDITOR_TEXT_KEY], "")

    def test_the_theme_comes_back_on_request(self):
        rows = self.rows(**{colors.EDITOR_TEXT_KEY: "#ffff00"})
        row = rows[colors.EDITOR_TEXT_KEY]
        row.reset.emit("clicked")
        self.assertEqual(self.settings.values[colors.EDITOR_TEXT_KEY], "")
        self.assertEqual(row.button.shown(), LIGHT[0])
        self.assertFalse(row.reset.sensitive)

    def test_a_change_from_elsewhere_is_shown(self):
        rows = self.rows()
        self.settings.set_string(colors.TERMINAL_GROUND_KEY, "#002b36")
        self.assertEqual(rows[colors.TERMINAL_GROUND_KEY].button.shown(), "#002b36")

    def test_another_theme_shows_its_colours_and_leaves_the_chosen(self):
        rows = self.rows(**{colors.EDITOR_TEXT_KEY: "#ffff00"})
        self.style.turn(True)
        self.assertEqual(
            self.shown(rows),
            [
                "#ffff00",
                colors.EDITOR_COLORS["dark"][1],
                *colors.TERMINAL_COLORS[True][:2],
            ],
        )
        self.style.turn(False)
        self.settings.set_string(colors.THEME_KEY, "sepia")
        self.assertEqual(
            self.shown(rows),
            [
                "#ffff00",
                colors.EDITOR_COLORS["sepia"][1],
                *colors.TERMINAL_COLORS[False][:2],
            ],
        )
        self.assertEqual(self.settings.values[colors.EDITOR_GROUND_KEY], "")

    def test_closed_preferences_listen_no_more(self):
        rows = self.rows()
        self.assertEqual(len(self.settings.handlers), 8)  # each: its key, the theme
        self.assertEqual(len(self.style.handlers), 4)
        self.dialog.emit("closed")
        self.assertEqual((self.settings.handlers, self.style.handlers), ({}, {}))
        self.assertEqual([row.handlers for row in rows.values()], [[]] * 4)

    def test_rows_listen_only_while_they_are_on_the_screen(self):
        self.settings = Settings()
        self.style = Style()
        rows = colors.add_rows(
            Widget(), self.settings, LargeTextRow(Widget()), self.style
        )
        self.assertEqual((self.settings.handlers, self.style.handlers), ({}, {}))
        row = rows[colors.EDITOR_TEXT_KEY]
        self.assertEqual(row.button.shown(), LIGHT[0])
        self.settings.set_string(colors.EDITOR_TEXT_KEY, "#ffff00")  # not heard
        for each in rows.values():
            each.button.emit("realize")
        self.assertEqual(row.button.shown(), "#ffff00")
        for each in rows.values():
            each.button.emit("realize")  # twice is once
        self.assertEqual(len(self.settings.handlers), 8)
        self.assertEqual(len(self.style.handlers), 4)

    def test_an_editor_closed_with_preferences_open_ends_the_listening(self):
        rows = self.rows()
        for row in rows.values():
            row.button.emit("unrealize")  # no dialog is closed
        self.assertEqual((self.settings.handlers, self.style.handlers), ({}, {}))
        self.dialog.emit("closed")  # and a second ending ends nothing
        self.settings.set_string(colors.EDITOR_TEXT_KEY, "#ffff00")
        self.assertEqual(rows[colors.EDITOR_TEXT_KEY].button.shown(), LIGHT[0])

    def test_rows_that_come_back_on_the_screen_listen_again(self):
        rows = self.rows()
        for row in rows.values():
            row.button.emit("unrealize")
        self.settings.set_string(colors.EDITOR_TEXT_KEY, "#ffff00")
        for row in rows.values():
            row.button.emit("realize")
        self.assertEqual(rows[colors.EDITOR_TEXT_KEY].button.shown(), "#ffff00")
        self.settings.set_string(colors.EDITOR_TEXT_KEY, "#00ff00")
        self.assertEqual(rows[colors.EDITOR_TEXT_KEY].button.shown(), "#00ff00")


if __name__ == "__main__":
    unittest.main()
