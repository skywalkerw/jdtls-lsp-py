@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel% equ 0 (
  py -3 "%~dp0scripts\setup_win.py"
  exit /b %errorlevel%
)

where python >nul 2>&1
if %errorlevel% equ 0 (
  python "%~dp0scripts\setup_win.py"
  exit /b %errorlevel%
)

echo [setup][error] 未找到 Python。请安装 Python 3.10+，并确保 py 或 python 在 PATH 中。
exit /b 1
