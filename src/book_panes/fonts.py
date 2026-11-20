"""Fonts of the text and of the agent's terminal, chosen in Preferences.

Each is a settings key holding a font as Pango describes one ("Fira Sans
13"). Empty means the default: Apostrophe's Fira Mono in a size it fits to
the width, the system's monospace font in the terminal.

Fira Mono comes with the application (fira/, with its licence): Debian
has no package of it. It is a font of this process alone, to draw the
text with and to choose.
"""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, GLib, Gtk, Pango, PangoCairo

EDITOR_KEY = "editor-font"
TERMINAL_KEY = "terminal-font"
EDITOR_SELECTOR = "textview.apostrophe-editor"
# shown while none is chosen; as Apostrophe's style sheet names it
EDITOR_DEFAULT = "Fira Mono,monospace 12"
SMALLEST = 6
LARGEST = 72
LOOK = 100  # ms between two looks at an open chooser
LOOKS = 100  # looks at one chooser; then its list has no such font
FACES = Path(__file__).with_name("fira")

PROVIDERS: dict = {}  # display: the provider of the editor's font
BROUGHT: set = set()  # the font files this process has taken in


def bring(font_map=None, faces=FACES):
    """Take the fonts that come with the application into the fonts of
    this process; each once. A Pango that cannot (before 1.56) and a file
    that is no font leave the machine's fonts."""
    font_map = font_map or PangoCairo.FontMap.get_default()
    add = getattr(font_map, "add_font_file", None)
    if add is None:
        return
    for face in sorted(faces.glob("*.ttf")):
        if str(face) not in BROUGHT:
            try:
                add(str(face))
            except GLib.Error:
                continue
            BROUGHT.add(str(face))


def named(context, description):
    """The description under the name of the font that is drawn for it:
    a font the machine lacks, and a kind of fonts ("Monospace"), stand for
    another, and a chooser's button names only fonts there are."""
    font = context.load_font(description)
    if font is None:
        return description
    named_one = description.copy()
    named_one.set_family(font.describe().get_family())
    return named_one


def described(text):
    """The font a setting names: a family in a size, the size within
    bounds. None for the default and for what names no such font."""
    if not text.strip():
        return None
    description = Pango.FontDescription.from_string(text)
    if not (description.get_family() or "").strip():
        return None
    size = description.get_size()
    if size <= 0:
        return None
    bounded = min(max(size, SMALLEST * Pango.SCALE), LARGEST * Pango.SCALE)
    if description.get_size_is_absolute():
        description.set_absolute_size(bounded)
    else:
        description.set_size(bounded)
    return description


def quoted(name):
    """NAME as a CSS string."""
    kept = "".join(char for char in name if char >= " " and char != "\x7f")
    return '"' + kept.replace("\\", "\\\\").replace('"', '\\"') + '"'


def css(selector, description):
    """The style sheet that gives SELECTOR the font DESCRIPTION."""
    families = ", ".join(
        quoted(name.strip())
        for name in description.get_family().split(",")
        if name.strip()
    )
    unit = "px" if description.get_size_is_absolute() else "pt"
    return (
        f"{selector} {{ font-family: {families};"
        f" font-size: {description.get_size() / Pango.SCALE:g}{unit};"
        f" font-weight: {int(description.get_weight())};"
        f" font-style: {description.get_style().value_nick};"
        f" font-stretch: {description.get_stretch().value_nick}; }}\n"
    )


def use_in_editor(view, text):
    """Gives the editor the font the setting TEXT names, or its own back."""
    display = view.get_display()
    provider = PROVIDERS.get(display)
    if provider is None:
        provider = PROVIDERS[display] = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )
    description = described(text)
    provider.load_from_string(
        css(EDITOR_SELECTOR, description) if description is not None else ""
    )
    view.choose_font(description)


def use_in_terminal(terminal, text):
    terminal.set_font(described(text))  # None: the terminal's own


def terminal_default():
    """What the terminal uses while none is chosen, near enough to show."""
    settings = Gtk.Settings.get_default()
    name = settings.props.gtk_font_name if settings is not None else None
    size = Pango.FontDescription.from_string(name or "").get_size() / Pango.SCALE
    return f"Monospace {size or 10:g}"


def is_monospace(item):
    """The chooser asks for families and for their faces."""
    family = item.get_family() if hasattr(item, "get_family") else item
    return family.is_monospace()


class FontRow:
    """A row of Preferences: the font of one settings key, and a button that
    gives the default back."""

    def __init__(self, settings, key, title, default, monospace):
        self.settings = settings
        self.key = key
        self.title = title
        self.default = default
        self.shown = ""
        self.asked: dict = {}  # open choosers: the timer asking each, its hands
        chooser = Gtk.FontDialog(title=title, modal=True)
        if monospace:
            chooser.set_filter(Gtk.CustomFilter.new(is_monospace))
        self.button = Gtk.FontDialogButton(dialog=chooser, valign=Gtk.Align.CENTER)
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
        self.button.connect("notify::font-desc", self._on_chosen)
        self.reset.connect("clicked", lambda _button: settings.reset(key))
        self.handler = settings.connect("changed::" + key, self._show)
        self.windows = Gtk.Window.get_toplevels()
        self.watch = self.windows.connect("items-changed", self._on_windows)
        self._show()

    def _show(self, *_args):
        chosen = described(self.settings.get_string(self.key))
        description = named(
            self.button.get_pango_context(),
            chosen or Pango.FontDescription.from_string(self.default),
        )
        self.shown = description.to_string()  # before the button tells of it
        self.button.set_font_desc(description)
        self.reset.set_sensitive(chosen is not None)

    def _on_chosen(self, button, _spec):
        description = button.get_font_desc()
        text = description.to_string() if description is not None else ""
        if text and text != self.shown:
            self.shown = text
            self.settings.set_string(self.key, text)

    def _on_windows(self, windows, position, _removed, added):
        for index in range(position, position + added):
            window = windows.get_item(index)
            if isinstance(window, Gtk.FontChooserDialog):
                self._ask(window)  # whose it is shows once it has its title

    def _ask(self, chooser):
        """Has this row's chooser choose the font shown. GTK looks for it
        in the chooser's list once, as the chooser opens, and fills the
        list bit by bit after: a font far down is not found, and the list
        stays at its top with none chosen. So the chooser is asked again:
        until a font is chosen (this one, or another by hand), a key, a
        click or a scroll says the choosing has begun, the chooser closes,
        or LOOKS looks have passed."""
        left = [LOOKS]

        def begun(*_given):
            left[0] = 0
            return False  # the chooser's as before

        scrolls = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.BOTH_AXES
        )
        hands = (
            (Gtk.EventControllerKey(), "key-pressed"),
            (Gtk.GestureClick(button=0), "pressed"),
            (scrolls, "scroll"),
        )

        def look():
            left[0] -= 1
            wanted = self.button.get_font_desc()
            if (
                left[0] >= 0
                and wanted is not None
                and chooser.get_visible()
                and chooser.get_title() == self.title
                and chooser.get_font() is None
            ):
                chooser.set_font_desc(wanted)
                if chooser.get_font() is None:
                    return True  # its list has not come that far
            self._leave(chooser)
            return False

        for hand, signal in hands:
            hand.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            hand.connect(signal, begun)
            chooser.add_controller(hand)
        self.asked[chooser] = GLib.timeout_add(LOOK, look), hands

    def _leave(self, chooser):
        """Asks the chooser no more; the timer of it left to its end."""
        timer, hands = self.asked.pop(chooser)
        for hand, _signal in hands:
            if hand.get_widget() == chooser:  # a closed one has given it up
                chooser.remove_controller(hand)
        return timer

    def close(self):
        self.settings.disconnect(self.handler)
        self.windows.disconnect(self.watch)
        for chooser in list(self.asked):
            GLib.source_remove(self._leave(chooser))


def add_rows(dialog, settings, large_text_row):
    """Puts the two fonts into Apostrophe's Preferences, where Large Text
    was. Called by the patched dialog."""
    large_text_row.set_visible(False)
    rows = {
        EDITOR_KEY: FontRow(settings, EDITOR_KEY, "Editor", EDITOR_DEFAULT, False),
        TERMINAL_KEY: FontRow(
            settings, TERMINAL_KEY, "Agent", terminal_default(), True
        ),
    }
    group = Adw.PreferencesGroup(title="Fonts")
    for row in rows.values():
        group.add(row.row)
    large_text_row.get_ancestor(Adw.PreferencesPage).add(group)

    def on_closed(_dialog):
        for row in rows.values():
            row.close()

    dialog.connect("closed", on_closed)
    dialog.font_rows = rows
    return rows
