@echo off
rem Builds client\launcher.py + client\ui into a single dist\sharepack.exe
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv
    .venv\Scripts\pip install minecraft-launcher-lib requests pywebview pyinstaller
)
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed --name sharepack ^
    --icon client\ui\assets\icon.ico --add-data "client\ui;ui" client\launcher.py
rmdir /s /q build
del sharepack.spec
echo.
echo Done: dist\sharepack.exe
