@echo off
rem Mitschrift Live (随堂字幕) desktop app. Examples:
rem   ui.bat               live captions + translation (press 开始 in the window)
rem   ui.bat --autostart   start capturing right away
rem   ui.bat --demo        windows with scripted demo data (no audio / models)
rem Python: the repo's .venv (uv sync), else %USERPROFILE%\.venvs\live-capture, else PATH.
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.venvs\live-capture\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m livecapture.app %*
