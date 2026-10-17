@echo off
rem CioNote launcher: all logic lives in launch.ps1 (interpreter autodetect + ollama probe)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
exit
