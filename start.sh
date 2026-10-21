#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077
EMAKTAB_PORT="${EMAKTAB_PORT:-8766}"
export EMAKTAB_PORT
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port "$EMAKTAB_PORT" --no-access-log
