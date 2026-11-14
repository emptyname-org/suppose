SHELL := /bin/sh
ROOT := $(CURDIR)
VERSION := $(strip $(file <VERSION))
export TMPDIR := $(ROOT)/tmp
export PIP_CACHE_DIR := $(ROOT)/tmp/pip
export PYTHONDONTWRITEBYTECODE := 1
.NOTPARALLEL:
.PHONY: build deb install test lint audit dev clean

build:
	/usr/bin/python3 tools/build.py

deb: build
	/usr/bin/python3 tools/deb.py

install: deb
	sudo apt-get install --reinstall ./dist/suppose_$(VERSION)_amd64.deb

dev: .venv/.tools_ready

.venv/.tools_ready: requirements-dev.lock
	mkdir -p tmp
	/usr/bin/python3 -m venv .venv
	.venv/bin/python -m pip install --require-hashes --only-binary=:all: --no-deps -r requirements-dev.lock
	.venv/bin/python -m pip check
	touch $@

test: dev build
	.venv/bin/python -m unittest discover -s tools/tests
	sh tools/test.sh

lint: dev
	.venv/bin/ruff check src tests tools
	.venv/bin/ruff format --check src tests tools
	.venv/bin/mypy src/book_panes src/bin/book-agent src/bin/book-view src/bin/book-edit src/bin/book-mcp src/bin/book-session tools
	.venv/bin/shellcheck src/bin/suppose tests/smoke/run.sh tools/test.sh
	$(MAKE) audit

audit:
	/usr/bin/python3 tools/audit.py

clean:
	/usr/bin/python3 tools/build.py --clean
