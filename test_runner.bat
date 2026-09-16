@echo off
cd /d C:\Users\jaesa\mabc2026-mvp
setlocal

set "HERMES_BIN=C:\Users\jaesa\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes"

echo START: %DATE% %TIME%
echo CMD: "%HERMES_BIN%" chat --oneshot --skills context-pack -q "1+1은? 답만 줘."
echo CWD: %CD%

"%HERMES_BIN%" chat --oneshot --skills context-pack -q "1+1은? 답만 줘." > test_out.txt 2> test_err.txt
set EXIT_CODE=%ERRORLEVEL%

echo EXIT_CODE=%EXIT_CODE%
echo END: %DATE% %TIME%
echo === STDOUT ===
type test_out.txt
echo === STDERR ===
type test_err.txt
