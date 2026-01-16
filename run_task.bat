@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat 2>nul
if errorlevel 1 goto NoVenv
python main.py
goto End

:NoVenv
echo Virtual environment not found (or not named .venv). Trying global python...
python main.py

:End
