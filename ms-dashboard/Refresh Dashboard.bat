@echo off
title Managed Services Dashboard - refresh
cd /d "%~dp0"
py -3.12 -P -E -W ignore "%~dp0engine\refresh.py"
if errorlevel 1 (
  echo.
  echo The refresh did not finish. See "last refresh log.txt" in this folder.
  pause
) else (
  timeout /t 6
)
