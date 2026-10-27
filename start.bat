@echo off
echo Open http://localhost:8000 in two browser tabs
python serve.py
if errorlevel 1 py serve.py
