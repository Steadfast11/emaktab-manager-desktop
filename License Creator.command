#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec .venv/bin/python tools/issuer_gui.py
