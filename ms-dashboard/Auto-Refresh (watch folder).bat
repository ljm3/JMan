@echo off
title Managed Services Dashboard - watching for new files
cd /d "%~dp0"
py -3.12 -P -E -W ignore "%~dp0engine\refresh.py" --watch
pause
