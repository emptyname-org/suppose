# book-editor

[Apostrophe](https://gitlab.gnome.org/World/apostrophe) 3.2 with two more panes
for writing a book with an AI agent: the book's folder tree on the left, the
agent's terminal on the right.

- **Tree**: the folder the editor was started in. A click opens a text file.
- **Editor**: Apostrophe. Edits are saved after a second of rest, on switching
  files and on closing. A file the agent changes on disk is reloaded; when both
  sides changed it, a bar asks which version to keep.
- **Agent**: a terminal running Claude Code, or any other command, in the book
  folder. Each folder keeps its own conversation. Shift+Enter breaks a line,
  Ctrl+Shift+C and Ctrl+Shift+V copy and paste.
- **Channel**: the agent reads and changes the text open in the editor, saved
  or not. Every change is one undo step in the editor.

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
    make test     # unit tests, then the built editor on a hidden display, with
                  # and without folder watching; statements and branches of
                  # src/book_panes are covered to at least 85 %
    make lint     # ruff, mypy, shellcheck and an advisory check of the pinned
                  # packages (needs the network)
    make clean    # removes dist/book-editor; tools and downloads stay

`make test` needs a checkout path of at most 51 bytes: the editor's socket
lives below it during the tests, and a socket path is short.

## Run

    dist/book-editor/bin/book-editor [FOLDER]

| Variable | Meaning | Default |
| --- | --- | --- |
| `BOOK_AGENT_CMD` | command run in the terminal | `book-agent`: Claude Code, one conversation per folder |
| `BOOK_AGENT_TITLE` | title of the terminal pane | `Agent` |
| `BOOK_CLAUDE` | the Claude Code command `book-agent` runs | `claude` |
| `BOOK_ROOT` | folder of the tree and the agent | `FOLDER`, else the current folder |

## Licence

The project's own files are under CC0 1.0 (`LICENSE`).
`patches/apostrophe-3.2-book.patch` changes Apostrophe and is under
Apostrophe's licence, the GNU GPL version 3 (`patches/COPYING`), as is a build
that contains Apostrophe.
