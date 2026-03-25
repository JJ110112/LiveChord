@echo off
REM ============================================
REM  LiveChord — 背景掃描音樂庫
REM  用法:
REM    scan.bat              增量掃描（預設）
REM    scan.bat full         全部重掃
REM ============================================
cd /d "%~dp0"
python scan.py %*
pause
