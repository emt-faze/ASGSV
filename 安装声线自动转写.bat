@echo off
chcp 65001 >nul
setlocal
if not exist "%~dp0nte-dialogue-ocr\.venv\Scripts\python.exe" (
  echo 请先运行“安装台词生成工具.bat”。
  pause
  exit /b 1
)
"%~dp0nte-dialogue-ocr\.venv\Scripts\python.exe" -X utf8 "%~dp0scripts\install_voice_transcription.py"
set "INSTALL_RESULT=%ERRORLEVEL%"
pause
exit /b %INSTALL_RESULT%
