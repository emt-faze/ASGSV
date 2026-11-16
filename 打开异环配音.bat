@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0nte-dialogue-ocr\launch.ps1" -Generate -Game nte
if errorlevel 1 pause
