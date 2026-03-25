@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel% equ 0 (
  py -3 "%~dp0scripts\export_portable.py"
  exit /b %errorlevel%
)

where python >nul 2>&1
if %errorlevel% equ 0 (
  python "%~dp0scripts\export_portable.py"
  exit /b %errorlevel%
)

echo [export][error] 未找到 Python。
exit /b 1
