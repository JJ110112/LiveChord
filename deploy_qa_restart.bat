@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy_qa_restart.ps1" %*
exit /b %errorlevel%
