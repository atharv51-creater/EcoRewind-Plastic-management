#!/bin/sh
echo "Open http://localhost:8000 in two browser tabs"
if [ -x "ecorewind_ml/.venv/bin/python" ]; then
  exec ecorewind_ml/.venv/bin/python serve.py
else
  exec python3 serve.py
fi
