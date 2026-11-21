# Suppose

[Apostrophe](https://gitlab.gnome.org/World/apostrophe) 3.2 with two more panes
for writing a book with an AI agent: the book's folder tree on the left, the
agent's terminal on the right.

- **Tree**: the working folder. A click opens a text file; the folder button
  opens another folder. The open file is highlighted.
- **Editor**: Apostrophe. Edits are saved after a second of rest, on switching
  files and on closing. A file the agent changes on disk is reloaded; when both
  sides changed it, a bar asks which version to keep. A file opened again
  shows where it was left, cursor and scrolling, while the window is open.
  Ctrl+Shift+V, or "Paste HTML as Markdown" in the editor's menu, pastes
  formatted text of the clipboard (from a browser, a word processor) as
  Markdown, converted by pandoc; images stay links, nothing is fetched.
  Without formatted text it pastes the plain text. **Mark**, beside Save,
  marks the selected text yellow, green, blue, pink or orange; with nothing
  selected it recolours or removes the mark at the cursor. A mark is in the
  file as `<mark class="yellow">…</mark>`, one to a line, none in code; the
  editor hides the tags, the preview shows the colours, and the tags are not
  counted as words. A link, `[words](address)` in one line, shows its words
  in blue; the rest shows while the cursor is in the link or at an end of
  it. With "Auto-Hide Header Bar" the editor's bars hide while
  typing, to the agent too; the pointer at the top, a menu or the search
  shows them, going to the tree or the agent does not.
- **Preferences**: font and size of the editor and of the agent's terminal,
  each with a button for the default (Fira Mono, which comes with the
  application, fitted to the width; the system's monospace font). Their
  text and background colours: the theme's until one is chosen, and a chosen
  one stays in every theme.
- **Agent**: a terminal running Claude Code or Codex, chosen in the pane's
  header, in the working folder. Shift+Enter breaks a line, Ctrl+Shift+C and
  Ctrl+Shift+V copy and paste.
- **Conversations**: the folder keeps one for each engine in `.suppose/`,
  transcripts included, and goes on with it when opened again, also after it
  was moved or copied. `/clear`, `/new` and `/resume` in the engine change the
  folder's conversation. Codex's is read from the files it has open, about
  four times a second: one it opens just before it ends can be missed; the
  folder then keeps the one before, and the missed one stays in Codex's home.
  `.suppose/.gitignore` keeps them out of a repository.
- **Channel**: the agent reads and changes the text open in the editor, saved
  or not. Every change is one undo step in the editor. Codex has the same as
  the tools `view` and `edit` of its MCP server `suppose`.

      book-view                    the text
      book-view --status           file, saved state, cursor, selection
      book-view --selection        the selected passage
      book-edit replace OLD NEW    OLD must occur exactly once
      book-edit selection NEW
      book-edit insert NEW         at the cursor

## Build

Debian 13 (Python 3.13), amd64. `make build` downloads Apostrophe and the libraries pinned in
`packages.lock` from Debian, verifies their checksums, applies
`patches/apostrophe-3.2-book.patch` and puts the application into
`dist/suppose`. Nothing is installed into the system.

Needed on the host:

    sudo apt install make python3 python3-venv curl ca-certificates dpkg patch \
        libglib2.0-bin python3-gi python3-cairo python3-gi-cairo \
        python3-chardet gir1.2-adw-1 gir1.2-gtk-4.0 gir1.2-webkit-6.0 \
        gsettings-desktop-schemas dconf-gsettings-backend pandoc

and for the tests:

    sudo apt install xvfb xauth dbus-daemon imagemagick

Commands:

    make build    # dist/suppose
    make deb      # dist/suppose_VERSION_amd64.deb
    make install  # make deb, then installs the package with sudo
    make test     # unit tests, then the built editor on a hidden display, with
                  # and without folder watching; statements and branches of
                  # src/book_panes are covered to at least 85 %
    make lint     # ruff, mypy, shellcheck and an advisory check of the pinned
                  # packages (needs the network)
    make clean    # removes dist/suppose; tools and downloads stay

`make test` needs a checkout path of at most 55 bytes: the editor's socket
lives below it during the tests, and a socket path is short.

## Install

    make install

builds the package and installs it with `sudo apt-get install`, which asks for
the password: the application in `/opt/suppose`, the command
`suppose`, a menu entry and "Open with" for folders. Run again, it
installs the new build. `sudo apt remove suppose` removes it.

## Run

    suppose [FOLDER | FILE...]

Without a folder the last one opens; the first time a dialog asks. A build
runs in place as `dist/suppose/bin/suppose`.

`~/.config/suppose/environment`, lines of `NAME=value`, sets variables
for a start from the menu:

| Variable | Meaning | Default |
| --- | --- | --- |
| `BOOK_CLAUDE` | the Claude Code command | `claude`, also in `~/.local/bin` |
| `BOOK_CODEX` | the Codex command | `codex`, also in `~/.local/bin` |
| `BOOK_AGENT_CMD` | command run in the terminal in place of the engines | none |
| `BOOK_AGENT_TITLE` | title of the terminal pane with `BOOK_AGENT_CMD` | `Agent` |
| `BOOK_ROOT` | working folder when none is named | the last one |

The engines read their own variables as usual (`CLAUDE_CONFIG_DIR`,
`CODEX_HOME`).

## Licence

The project's own files are under CC0 1.0 (`LICENSE`).
`patches/apostrophe-3.2-book.patch` changes Apostrophe and is under
Apostrophe's licence, the GNU GPL version 3 (`patches/COPYING`), as is a build
that contains Apostrophe. Fira Mono (`src/book_panes/fira/`, from
https://github.com/mozilla/Fira, tag 4.202) is under the SIL Open Font
License 1.1 (`src/book_panes/fira/OFL.txt`).
