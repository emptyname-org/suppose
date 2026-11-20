"""Fonts chosen in Preferences: what a setting names, its style sheet, the rows."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Pango, PangoCairo

from book_panes import fonts

from .test_channel import TMP


def font(text):
    return Pango.FontDescription.from_string(text)


class Settings:
    """Gio.Settings of the two keys."""

    def __init__(self, **values):
        self.values = {fonts.EDITOR_KEY: "", fonts.TERMINAL_KEY: ""}
        self.values.update(values)
        self.handlers = {}
        self.serial = 0

    def get_string(self, key):
        return self.values[key]

    def set_string(self, key, value):
        if self.values[key] != value:
            self.values[key] = value
            self._changed(key)

    def reset(self, key):
        self.set_string(key, "")

    def connect(self, signal, handler):
        self.serial += 1
        self.handlers[self.serial] = (signal, handler)
        return self.serial

    def disconnect(self, handler):
        del self.handlers[handler]

    def _changed(self, key):
        for signal, handler in list(self.handlers.values()):
            if signal == "changed::" + key:
                handler(self, key)


class Widget:
    def __init__(self, **properties):
        self.properties = properties
        self.handlers = {}
        self.classes = []
        self.suffixes = []
        self.children = []
        self.visible = True
        self.sensitive = True

    def connect(self, signal, handler):
        self.handlers[signal] = handler

    def emit(self, signal):
        self.handlers[signal](self)

    def add_css_class(self, name):
        self.classes.append(name)

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def set_visible(self, visible):
        self.visible = visible

    def add_suffix(self, widget):
        self.suffixes.append(widget)

    def set_activatable_widget(self, widget):
        self.activatable = widget

    def add(self, widget):
        self.children.append(widget)

    def set_filter(self, shown):
        self.filter = shown


class Context:
    """Draws with the fonts of a machine that has few: one it lacks is
    drawn with its monospace font where that is asked for, or with its
    plain one."""

    there = ("Fira Mono", "Serif", "DejaVu Sans Mono")

    def load_font(self, description):
        wanted = [name.strip() for name in description.get_family().split(",")]
        family = next((name for name in wanted if name in self.there), None)
        if family is None:
            kind = any(name.lower() == "monospace" for name in wanted)
            family = "DejaVu Sans Mono" if kind else "DejaVu Sans"
        drawn = description.copy()
        drawn.set_family(family)
        return SimpleNamespace(describe=lambda: drawn)


class FontButton(Widget):
    description = None
    context = Context()

    def get_pango_context(self):
        return self.context

    def set_font_desc(self, description):
        self.description = description
        self.handlers["notify::font-desc"](self, None)

    def get_font_desc(self):
        return self.description


class LargeTextRow(Widget):
    def __init__(self, page):
        super().__init__()
        self.page = page

    def get_ancestor(self, _kind):
        return self.page


class Provider:
    def __init__(self):
        self.loaded = None

    def load_from_string(self, text):
        self.loaded = text


def toolkit():
    """Gtk and Adw as far as the rows need them, without a display."""
    added = []
    gtk = SimpleNamespace(
        FontDialog=Widget,
        FontDialogButton=FontButton,
        Button=Widget,
        Align=SimpleNamespace(CENTER="center"),
        CustomFilter=SimpleNamespace(new=lambda shown: shown),
        CssProvider=Provider,
        StyleContext=SimpleNamespace(
            add_provider_for_display=lambda *given: added.append(given)
        ),
        STYLE_PROVIDER_PRIORITY_APPLICATION=600,
        Settings=SimpleNamespace(get_default=lambda: None),
        added=added,
    )
    adw = SimpleNamespace(
        ActionRow=Widget, PreferencesGroup=Widget, PreferencesPage=object
    )
    return gtk, adw


class DescribedTest(unittest.TestCase):
    def test_nothing_chosen_is_the_default(self):
        self.assertIsNone(fonts.described(""))
        self.assertIsNone(fonts.described("  \n"))

    def test_a_family_in_a_size_is_a_font(self):
        chosen = fonts.described("DejaVu Serif Bold Italic 13")
        self.assertEqual(chosen.get_family(), "DejaVu Serif")
        self.assertEqual(chosen.get_size(), 13 * Pango.SCALE)
        self.assertEqual(chosen.get_weight(), Pango.Weight.BOLD)
        self.assertEqual(chosen.get_style(), Pango.Style.ITALIC)

    def test_a_size_without_a_family_is_no_font(self):
        self.assertIsNone(fonts.described("14"))

    def test_a_family_without_a_size_is_no_font(self):
        self.assertIsNone(fonts.described("Serif"))

    def test_the_size_stays_within_bounds(self):
        self.assertEqual(
            fonts.described("Serif 2").get_size(), fonts.SMALLEST * Pango.SCALE
        )
        self.assertEqual(
            fonts.described("Serif 500").get_size(), fonts.LARGEST * Pango.SCALE
        )

    def test_a_size_in_pixels_stays_one(self):
        chosen = fonts.described("Serif 900px")
        self.assertTrue(chosen.get_size_is_absolute())
        self.assertEqual(chosen.get_size(), fonts.LARGEST * Pango.SCALE)


class CssTest(unittest.TestCase):
    def test_the_rule_names_family_size_weight_style_and_stretch(self):
        self.assertEqual(
            fonts.css("textview.x", font("DejaVu Serif Bold Italic Condensed 13")),
            'textview.x { font-family: "DejaVu Serif"; font-size: 13pt;'
            " font-weight: 700; font-style: italic; font-stretch: condensed; }\n",
        )

    def test_a_plain_font_says_so(self):
        self.assertEqual(
            fonts.css("a", font("Serif 10.5")),
            'a { font-family: "Serif"; font-size: 10.5pt;'
            " font-weight: 400; font-style: normal; font-stretch: normal; }\n",
        )

    def test_several_families_are_several_names(self):
        self.assertIn(
            'font-family: "Fira Mono", "monospace";',
            fonts.css("a", font("Fira Mono,monospace 12")),
        )

    def test_pixels_stay_pixels(self):
        self.assertIn("font-size: 16px;", fonts.css("a", font("Serif 16px")))

    def test_a_name_cannot_leave_its_string(self):
        chosen = font("Serif 12")
        chosen.set_family('x"; } * { color: red; } y\\\n')
        self.assertIn(
            'font-family: "x\\"; } * { color: red; } y\\\\";',
            fonts.css("a", chosen),
        )

    def test_the_style_sheet_is_one_gtk_reads(self):
        from gi.repository import Gtk

        provider = Gtk.CssProvider()
        errors = []
        provider.connect("parsing-error", lambda *given: errors.append(given))
        provider.load_from_string(
            fonts.css(fonts.EDITOR_SELECTOR, font("DejaVu Serif Bold Italic 13"))
        )
        self.assertEqual(errors, [])


class Faked(unittest.TestCase):
    """With the toolkit's widgets replaced."""

    def setUp(self):
        self.gtk, self.adw = toolkit()
        for name, fake in (("Gtk", self.gtk), ("Adw", self.adw)):
            patch = mock.patch.object(fonts, name, fake)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.dict(fonts.PROVIDERS, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.view = SimpleNamespace(
            get_display=lambda: "display", chosen=[], choose_font=None
        )
        self.view.choose_font = self.view.chosen.append


class UseTest(Faked):
    def test_the_editor_takes_the_chosen_font(self):
        fonts.use_in_editor(self.view, "Serif 14")
        provider = fonts.PROVIDERS["display"]
        self.assertIn(
            'textview.apostrophe-editor { font-family: "Serif";', provider.loaded
        )
        self.assertEqual(self.view.chosen[-1].to_string(), "Serif 14")
        self.assertEqual(self.gtk.added, [("display", provider, 601)])

    def test_the_editor_gets_its_own_font_back(self):
        fonts.use_in_editor(self.view, "Serif 14")
        fonts.use_in_editor(self.view, "")
        self.assertEqual(fonts.PROVIDERS["display"].loaded, "")
        self.assertIsNone(self.view.chosen[-1])
        self.assertEqual(len(self.gtk.added), 1)  # one provider a display

    def test_the_terminal_takes_the_chosen_font_or_its_own(self):
        given = []
        terminal = SimpleNamespace(set_font=given.append)
        fonts.use_in_terminal(terminal, "Monospace 15")
        fonts.use_in_terminal(terminal, "no size")
        self.assertEqual(given[0].to_string(), "Monospace 15")
        self.assertIsNone(given[1])

    def test_the_terminal_default_has_the_size_of_the_interface(self):
        self.assertEqual(fonts.terminal_default(), "Monospace 10")
        named = SimpleNamespace(props=SimpleNamespace(gtk_font_name="Noto Sans 11"))
        self.gtk.Settings.get_default = lambda: named
        self.assertEqual(fonts.terminal_default(), "Monospace 11")

    def test_only_monospace_families_and_their_faces_pass(self):
        mono = SimpleNamespace(is_monospace=lambda: True)
        serif = SimpleNamespace(is_monospace=lambda: False)
        self.assertTrue(fonts.is_monospace(mono))
        self.assertFalse(fonts.is_monospace(serif))
        self.assertTrue(fonts.is_monospace(SimpleNamespace(get_family=lambda: mono)))
        self.assertFalse(fonts.is_monospace(SimpleNamespace(get_family=lambda: serif)))


class FontMap:
    """Takes font files in, but none that is named broken."""

    def __init__(self):
        self.added = []

    def add_font_file(self, path):
        if "broken" in path:
            raise GLib.Error("no font")
        self.added.append(path)


class BringTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(fonts, "BROUGHT", set())
        patch.start()
        self.addCleanup(patch.stop)

    def test_fira_mono_comes_with_the_application(self):
        font_map = FontMap()
        fonts.bring(font_map)
        self.assertEqual(
            [Path(path).name for path in font_map.added],
            ["FiraMono-Bold.ttf", "FiraMono-Medium.ttf", "FiraMono-Regular.ttf"],
        )
        self.assertTrue((fonts.FACES / "OFL.txt").is_file())  # and its licence
        fonts.bring(font_map)  # once
        self.assertEqual(len(font_map.added), 3)

    def test_the_files_are_fira_mono(self):
        font_map = PangoCairo.FontMap.new()
        if not hasattr(font_map, "add_font_file"):
            self.skipTest("Pango before 1.56")
        fonts.bring(font_map)
        families = {family.get_name(): family for family in font_map.list_families()}
        self.assertIn("Fira Mono", families)
        self.assertTrue(fonts.is_monospace(families["Fira Mono"]))
        drawn = fonts.named(font_map.create_context(), font("Fira Mono,monospace 12"))
        self.assertEqual(drawn.to_string(), "Fira Mono 12")

    def test_a_file_that_is_no_font_is_left_out(self):
        os.makedirs(TMP, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TMP) as folder:
            for name in ("a.ttf", "broken.ttf", "notes.txt"):
                Path(folder, name).write_bytes(b"")
            font_map = FontMap()
            fonts.bring(font_map, Path(folder))
            fonts.bring(font_map, Path(folder))  # the broken one is tried again
            self.assertEqual([Path(path).name for path in font_map.added], ["a.ttf"])

    def test_a_pango_that_takes_no_files_is_left_alone(self):
        fonts.bring(object())
        self.assertEqual(fonts.BROUGHT, set())


class RowsTest(Faked):
    def rows(self, **values):
        self.settings = Settings(**values)
        self.page = Widget()
        self.large = LargeTextRow(self.page)
        self.dialog = Widget()
        return fonts.add_rows(self.dialog, self.settings, self.large)

    def test_the_rows_take_the_place_of_large_text(self):
        rows = self.rows()
        self.assertFalse(self.large.visible)
        (group,) = self.page.children
        self.assertEqual(
            [row.properties["title"] for row in group.children], ["Editor", "Agent"]
        )
        self.assertEqual(sorted(rows), [fonts.EDITOR_KEY, fonts.TERMINAL_KEY])
        self.assertIs(self.dialog.font_rows, rows)

    def test_only_the_agent_is_held_to_monospace(self):
        rows = self.rows()
        editor = rows[fonts.EDITOR_KEY].button.properties["dialog"]
        agent = rows[fonts.TERMINAL_KEY].button.properties["dialog"]
        self.assertFalse(hasattr(editor, "filter"))
        self.assertIs(agent.filter, fonts.is_monospace)

    def test_nothing_chosen_shows_the_default_by_its_name_and_sets_nothing(self):
        rows = self.rows()
        row = rows[fonts.EDITOR_KEY]
        self.assertEqual(row.button.description.to_string(), "Fira Mono 12")
        self.assertFalse(row.reset.sensitive)
        self.assertEqual(  # no kind of fonts: the font drawn for it
            rows[fonts.TERMINAL_KEY].button.description.to_string(),
            "DejaVu Sans Mono 10",
        )
        self.assertEqual(self.settings.values[fonts.EDITOR_KEY], "")
        self.assertEqual(self.settings.values[fonts.TERMINAL_KEY], "")

    def test_a_font_the_machine_lacks_shows_as_the_one_drawn_for_it(self):
        with mock.patch.object(Context, "there", ("DejaVu Sans Mono",)):
            rows = self.rows(**{fonts.TERMINAL_KEY: "Gone Mono Bold 13"})
        self.assertEqual(
            rows[fonts.EDITOR_KEY].button.description.to_string(),
            "DejaVu Sans Mono 12",
        )
        self.assertEqual(
            rows[fonts.TERMINAL_KEY].button.description.to_string(),
            "DejaVu Sans Bold 13",
        )
        self.assertEqual(self.settings.values[fonts.TERMINAL_KEY], "Gone Mono Bold 13")
        self.assertEqual(self.settings.values[fonts.EDITOR_KEY], "")

    def test_a_machine_without_fonts_shows_what_is_asked_for(self):
        with mock.patch.object(Context, "load_font", lambda _self, _wanted: None):
            rows = self.rows()
        self.assertEqual(
            rows[fonts.EDITOR_KEY].button.description.to_string(), fonts.EDITOR_DEFAULT
        )

    def test_a_chosen_font_is_shown(self):
        rows = self.rows(**{fonts.TERMINAL_KEY: "DejaVu Sans Mono 15"})
        row = rows[fonts.TERMINAL_KEY]
        self.assertEqual(row.button.description.to_string(), "DejaVu Sans Mono 15")
        self.assertTrue(row.reset.sensitive)

    def test_choosing_sets_the_key_of_the_row_alone(self):
        rows = self.rows()
        rows[fonts.EDITOR_KEY].button.set_font_desc(font("Serif 14"))
        self.assertEqual(self.settings.values[fonts.EDITOR_KEY], "Serif 14")
        self.assertEqual(self.settings.values[fonts.TERMINAL_KEY], "")
        self.assertTrue(rows[fonts.EDITOR_KEY].reset.sensitive)

    def test_a_size_out_of_bounds_is_shown_as_it_is_used(self):
        rows = self.rows()
        row = rows[fonts.EDITOR_KEY]
        row.button.set_font_desc(font("Serif 300"))
        self.assertEqual(row.button.description.to_string(), "Serif 72")

    def test_the_default_comes_back_on_request(self):
        rows = self.rows(**{fonts.EDITOR_KEY: "Serif 14"})
        row = rows[fonts.EDITOR_KEY]
        row.reset.emit("clicked")
        self.assertEqual(self.settings.values[fonts.EDITOR_KEY], "")
        self.assertEqual(row.button.description.to_string(), "Fira Mono 12")
        self.assertFalse(row.reset.sensitive)

    def test_a_change_from_elsewhere_is_shown(self):
        rows = self.rows()
        self.settings.set_string(fonts.EDITOR_KEY, "Serif 18")
        self.assertEqual(
            rows[fonts.EDITOR_KEY].button.description.to_string(), "Serif 18"
        )

    def test_a_button_without_a_font_sets_nothing(self):
        rows = self.rows()
        row = rows[fonts.EDITOR_KEY]
        row.button.description = None
        row.button.handlers["notify::font-desc"](row.button, None)
        self.assertEqual(self.settings.values[fonts.EDITOR_KEY], "")

    def test_closed_preferences_listen_no_more(self):
        self.rows()
        self.assertEqual(len(self.settings.handlers), 2)
        self.dialog.emit("closed")
        self.assertEqual(self.settings.handlers, {})


if __name__ == "__main__":
    unittest.main()
