@echo off
cd /d C:\Users\jaesa\mabc2026-mvp
setlocal

set "HERMES_BIN=C:\Users\jaesa\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes"

echo START: %DATE% %TIME%
echo CMD: "%HERMES_BIN%" chat --oneshot --skills context-pack -q "1+1은? 답만 줘."
echo CWD: %CD%

rem CliHermesRunner와 동일한 방식: stdout/stderr를 파일로 리디렉션
"%HERMES_BIN%" chat --oneshot --skills context-pack -q "1+1은? 답만 줘." > test_out.txt 2> test_err.txt
set EXIT_CODE=%ERRORLEVEL%

echo EXIT_CODE=%EXIT_CODE%
echo END: %DATE% %TIME%
echo === STDOUT ===
type test_out.txt
echo === STDERR ===
type test_err.txt
