@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_generator.ps1" -SkipModels
if errorlevel 1 pause
