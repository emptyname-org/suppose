# suppose guidance

- `make build` puts the application into `dist/suppose`. `make test` runs
  the unit tests and the built editor on a hidden display, with and without
  folder watching; statements and branches of `src/book_panes` are covered to
  at least 85 %. `make lint` runs ruff, mypy, shellcheck and the advisory check
  of the pinned packages (needs the network). `make deb` packages the build.
  `make install` installs the package with sudo; agents and tests never run
  it. `make clean` removes the build.
- `src/book_panes/`: panes, folders, channel (editor side), client (agent
  side), agents (ending the agent process), engines (Claude Code and Codex,
  the folder's conversations), mcp (the channel as Codex's tools), choose
  (folder dialog of the launcher). `src/bin/`: launcher and agent tools.
  `assets/`: menu entry and icons. `tests/` mirrors `src/`; `tests/smoke/`
  drives the built editor. `tools/`: build and audit.
- Tests never run a real engine and never touch the engines' real homes.
- Apostrophe is changed only through `patches/apostrophe-3.2-book.patch`
  (`diff -u` against the files of the pinned package, paths relative to
  `usr/`), never in the build output.
- Tests never open a window on the real display; the smoke test brings its own.
- Temporary files go to `tmp/`.
