@echo off
REM LiveChord health monitor — Windows Task Scheduler entry point.
REM Schedule: every 10 min. The script itself is fast (<5s normal tick,
REM <40s when Hermes fires). Always exits 0; check run.log for results.
cd /d "%~dp0"
python check.py --target both >> run.scheduler.log 2>&1
exit /b 0
