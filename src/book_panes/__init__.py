"""Book panes for Apostrophe: folder tree, agent terminal, editor channel."""

APP_ID = "io.github.emptyname_org.Suppose"  # of windows, desktop entry, icon


def install(window):
    """Wrap the window's editor with the tree and the agent terminal."""
    from .panes import BookPanes

    return BookPanes(window)
