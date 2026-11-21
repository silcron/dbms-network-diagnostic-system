@echo off
chcp 65001 >nul
call "%~dp0..\01_프로그램_실행.cmd" --scenario warning --auto-run %*
exit /b %errorlevel%
