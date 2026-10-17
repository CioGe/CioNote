@echo off
rem Debug launcher: keeps the console open and shows every step + app errors
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" -Debug
pause
