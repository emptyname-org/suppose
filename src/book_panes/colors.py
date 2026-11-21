"""Colours of the text and of the agent's terminal, chosen in Preferences.

Each is a settings key holding a colour ("#rrggbb"): of the text, and of
its background, in the editor and in the terminal. Empty means the
theme's. A chosen colour stays whatever the theme.

What is drawn over the editor's background goes with it: code stands out
by a tint of the text's colour, the bar at the bottom takes both colours,
marks and links are those of a dark theme on a dark background.
"""

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk

EDITOR_TEXT_KEY = "editor-color"
EDITOR_GROUND_KEY = "editor-background"
TERMINAL_TEXT_KEY = "terminal-color"
TERMINAL_GROUND_KEY = "terminal-background"
THEME_KEY = "color-scheme"  # Apostrophe's: system, light, sepia, dark
EDITOR_SELECTOR = "textview.apostrophe-editor"
# the editor's text and background by Apostrophe's theme, as its style
# schemes have them
EDITOR_COLORS = {
    "light": ("#2e3436", "#ffffff"),
    "dark": ("#eeeeee", "#2d2d2d"),
    "sepia": ("#4f3915", "#f9f3e9"),
}
# the terminal's by app theme (dark or not): text, background, palette
TERMINAL_COLORS = {
    False: (
        "#1e1e1e",
        "#ffffff",
        (
            "#24292e", "#d73a49", "#28a745", "#b08800",
            "#0366d6", "#6f42c1", "#1b7c83", "#6a737d",
            "#959da5", "#cb2431", "#22863a", "#9a6700",
            "#005cc5", "#5a32a3", "#3192aa", "#d1d5da",
        ),
    ),
    True: (
        "#deddda",
        "#1d1d20",
        (
            "#241f31", "#c01c28", "#2ec27e", "#f5c211",
            "#1e78e4", "#9841bb", "#0ab9dc", "#c0bfbc",
            "#5e5c64", "#ed333b", "#57e389", "#f8e45c",
            "#51a1ff", "#c061cb", "#4fd2fd", "#f6f5f4",
        ),
    ),
}  # fmt: skip
CODE_SHARE = 0.12  # of the text's colour in the background of code
DIM_SHARE = 0.4  # ... in the markup Apostrophe dims

PROVIDERS: dict = {}  # display: the provider of the editor's colours


def rgba(color):
    parsed = Gdk.RGBA()
    parsed.parse(color)
    return parsed


def chosen(text):
    """The colour a setting names, as "#rrggbb". None for the theme's and
    for what names no colour."""
    parsed = Gdk.RGBA()
    if not text.strip() or not parsed.parse(text.strip()):
        return None
    shares = (parsed.red, parsed.green, parsed.blue)
    return "#" + "".join(
        f"{round(min(max(share, 0), 1) * 255):02x}" for share in shares
    )


def parts(color):
    return [int(color[place : place + 2], 16) for place in (1, 3, 5)]


def is_dark(color):
    """Whether light text is read on the colour."""
    red, green, blue = parts(color)
    return 299 * red + 587 * green + 114 * blue < 127500


def mixed(ground, text, share):
    """GROUND with a SHARE of TEXT in it."""
    return "#" + "".join(
        f"{round(under + (over - under) * share):02x}"
        for under, over in zip(parts(ground), parts(text), strict=True)
    )


def theme(settings, style):
    """Apostrophe's theme in use: light, dark or sepia."""
    name = settings.get_string(THEME_KEY)
    if name not in EDITOR_COLORS:  # the system's
        name = "dark" if style.get_dark() else "light"
    return name


def editor_css(text, ground, default):
    """The style sheet of an editor with those colours chosen. None is the
    theme's: DEFAULT has its two. Empty with none chosen."""
    if text is None and ground is None:
        return ""
    drawn = text or default[0], ground or default[1]
    rules = [
        f"@define-color code_bg_color {mixed(drawn[1], drawn[0], CODE_SHARE)};",
        f"@define-color link_fg_color {mixed(drawn[1], drawn[0], DIM_SHARE)};",
    ]
    if text is not None:
        rules.append(
            f"{EDITOR_SELECTOR}, .bottombar {{ color: {text}; caret-color: {text}; }}"
        )
    if ground is not None:
        grounds = f"{EDITOR_SELECTOR}, {EDITOR_SELECTOR} > text, .stats-button"
        rules.append(f"{grounds} {{ background-color: {ground}; }}")
        shade = f"alpha({ground}, 0.95)"  # as Apostrophe's, with its text under it
        rules.append(
            f".bottombar_background.shown box {{ background-color: {shade}; }}"
        )
    return "\n".join(rules) + "\n"


def use_in_editor(view, text, ground, default):
    """Gives the editor the colours the settings TEXT and GROUND name, or
    the theme's back (DEFAULT). Tells whether its background is now a dark
    one; None: as the theme has it."""
    display = view.get_display()
    provider = PROVIDERS.get(display)
    if provider is None:
        provider = PROVIDERS[display] = Gtk.CssProvider()
        # above the sheet of the sepia theme, which Apostrophe gives the
        # user's rank
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1
        )
    text, ground = chosen(text), chosen(ground)
    provider.load_from_string(editor_css(text, ground, default))
    view.markup.on_style_updated()  # Apostrophe's tags of code and of markup
    return None if ground is None else is_dark(ground)


def use_in_terminal(terminal, text, ground, dark):
    """Gives the terminal the colours the settings name, or those of the
    theme (DARK or not); its other colours are read on its background."""
    text, ground = chosen(text), chosen(ground)
    own_text, own_ground, _palette = TERMINAL_COLORS[dark]
    palette = TERMINAL_COLORS[dark if ground is None else is_dark(ground)][2]
    terminal.set_colors(
        rgba(text or own_text),
        rgba(ground or own_ground),
        [rgba(color) for color in palette],
    )


class ColorRow:
    """A row of Preferences: the colour of one settings key, and a button
    that gives the theme's back. It listens, to its setting and to the
    theme, while it is on the screen."""

    def __init__(self, settings, style, key, title, default):
        self.settings = settings
        self.style = style
        self.key = key
        self.default = default  # tells the theme's colour
        self.shown = ""
        self.handlers: list = []  # what it listens to, and by which number
        chooser = Gtk.ColorDialog(title=title, modal=True, with_alpha=False)
        self.button = Gtk.ColorDialogButton(dialog=chooser, valign=Gtk.Align.CENTER)
        self.reset = Gtk.Button(
            icon_name="edit-undo-symbolic",
            tooltip_text="Default",
            valign=Gtk.Align.CENTER,
        )
        self.reset.add_css_class("flat")
        self.row = Adw.ActionRow(title=title)
        self.row.add_suffix(self.button)
        self.row.add_suffix(self.reset)
        self.row.set_activatable_widget(self.button)
        self.button.connect("notify::rgba", self._on_chosen)
        self.reset.connect("clicked", lambda _button: settings.reset(key))
        self.button.connect("realize", self.open)
        self.button.connect("unrealize", self.close)
        self._show()

    def open(self, *_given):
        if not self.handlers:
            self.handlers = [
                (self.settings, self.settings.connect("changed::" + key, self._show))
                for key in (self.key, THEME_KEY)
            ]
            dark = self.style.connect("notify::dark", self._show)
            self.handlers.append((self.style, dark))
            self._show()  # what changed while it did not listen

    def _show(self, *_args):
        color = chosen(self.settings.get_string(self.key))
        self.shown = color or self.default()  # before the button tells of it
        self.button.set_rgba(rgba(self.shown))
        self.reset.set_sensitive(color is not None)

    def _on_chosen(self, button, _spec):
        color = chosen(button.get_rgba().to_string())
        if color is not None and color != self.shown:
            self.shown = color
            self.settings.set_string(self.key, color)

    def close(self, *_given):
        for source, handler in self.handlers:
            source.disconnect(handler)
        self.handlers = []


def add_rows(dialog, settings, large_text_row, style=None):
    """Puts the four colours into Apostrophe's Preferences, under the
    fonts. Called by the patched dialog."""
    style = style or Adw.StyleManager.get_default()
    rows = {
        key: ColorRow(settings, style, key, title, default)
        for key, title, default in (
            (
                EDITOR_TEXT_KEY,
                "Editor text",
                lambda: EDITOR_COLORS[theme(settings, style)][0],
            ),
            (
                EDITOR_GROUND_KEY,
                "Editor background",
                lambda: EDITOR_COLORS[theme(settings, style)][1],
            ),
            (
                TERMINAL_TEXT_KEY,
                "Agent text",
                lambda: TERMINAL_COLORS[style.get_dark()][0],
            ),
            (
                TERMINAL_GROUND_KEY,
                "Agent background",
                lambda: TERMINAL_COLORS[style.get_dark()][1],
            ),
        )
    }
    group = Adw.PreferencesGroup(title="Colors")
    for row in rows.values():
        group.add(row.row)
    large_text_row.get_ancestor(Adw.PreferencesPage).add(group)

    def on_closed(_dialog):
        for row in rows.values():
            row.close()  # at once, not when the closed dialog has gone

    dialog.connect("closed", on_closed)
    dialog.color_rows = rows
    return rows
