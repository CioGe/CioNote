@echo off
cd /d "%~dp0"
echo Building portable EXE (no Python needed on the target machine)...
echo.
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
pyinstaller --onefile --windowed --name CioNote --icon "assets\logo.ico" --add-data "assets;assets" cionote.py
echo.
echo Done. The EXE is in the "dist" folder.
pause
