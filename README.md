# book-editor

[Apostrophe](https://gitlab.gnome.org/World/apostrophe) 3.2 with two more panes
for writing a book with an AI agent: the book's folder tree on the left, the
agent's terminal on the right.

- **Tree**: the working folder. A click opens a text file; the folder button
  opens another folder.
- **Editor**: Apostrophe. Edits are saved after a second of rest, on switching
  files and on closing. A file the agent changes on disk is reloaded; when both
  sides changed it, a bar asks which version to keep.
- **Agent**: a terminal running Claude Code or Codex, chosen in the pane's
  header, in the working folder. Shift+Enter breaks a line, Ctrl+Shift+C and
  Ctrl+Shift+V copy and paste.
- **Conversations**: the folder keeps one for each engine in `.book-editor/`,
  transcripts included, and goes on with it when opened again, also after it
  was moved or copied. `/clear`, `/new` and `/resume` in the engine change the
  folder's conversation. `.book-editor/.gitignore` keeps them out of a
  repository.
- **Channel**: the agent reads and changes the text open in the editor, saved
  or not. Every change is one undo step in the editor. Codex has the same as
  the tools `view` and `edit` of its MCP server `book_editor`.

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
`dist/book-editor`. Nothing is installed into the system.

Needed on the host:

    sudo apt install make python3 python3-venv curl ca-certificates dpkg patch \
        libglib2.0-bin python3-gi python3-cairo python3-gi-cairo \
        python3-chardet gir1.2-adw-1 gir1.2-gtk-4.0 gir1.2-webkit-6.0 \
        gsettings-desktop-schemas dconf-gsettings-backend pandoc

and for the tests:

    sudo apt install xvfb xauth dbus-daemon imagemagick

Commands:

    make build    # dist/book-editor
    make deb      # dist/book-editor_VERSION_amd64.deb
    make test     # unit tests, then the built editor on a hidden display, with
                  # and without folder watching; statements and branches of
                  # src/book_panes are covered to at least 85 %
    make lint     # ruff, mypy, shellcheck and an advisory check of the pinned
                  # packages (needs the network)
    make clean    # removes dist/book-editor; tools and downloads stay

`make test` needs a checkout path of at most 51 bytes: the editor's socket
lives below it during the tests, and a socket path is short.

## Install

    sudo apt install ./dist/book-editor_VERSION_amd64.deb

puts the application into `/opt/book-editor`, with the command `book-editor`,
a menu entry and "Open with" for folders. `sudo apt remove book-editor`
removes it.

## Run

    book-editor [FOLDER | FILE...]

Without a folder the last one opens; the first time a dialog asks. A build
runs in place as `dist/book-editor/bin/book-editor`.

`~/.config/book-editor/environment`, lines of `NAME=value`, sets variables
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
that contains Apostrophe.
