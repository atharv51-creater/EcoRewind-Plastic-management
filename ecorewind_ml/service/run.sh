#!/bin/sh
set -eu
cd "$(dirname "$0")"
VENV="../.venv/bin/python"
if [ -x "$VENV" ]; then
  PYTHON="$VENV"
else
  PYTHON="python3"
fi
exec "$PYTHON" -m uvicorn app:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8100}"
