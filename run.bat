@echo off
rem Mitschrift Live (随堂字幕) command-line captions. Examples:
rem   run.bat                    live captions, NVIDIA GPU
rem   run.bat --engine npu       live captions, Intel NPU
rem   run.bat --engine cpu       live captions, CPU
rem   run.bat --list-devices     show audio devices
rem Python: the repo's .venv (uv sync), else %USERPROFILE%\.venvs\live-capture, else PATH.
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.venvs\live-capture\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m livecapture %*
