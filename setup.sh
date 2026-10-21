#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
printf '\nTayyor. ./start.sh orqali ishga tushiring.\n'
