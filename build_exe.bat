@echo off
setlocal
title Build .yazn-Dos33 Backup

echo ================================================
echo       Building .yazn-Dos33 Backup
echo ================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo Python 3 was not found.
    echo Install Python and enable Add Python to PATH.
    pause
    exit /b 1
)

echo [1/3] Installing PyInstaller...
py -m pip install --upgrade pyinstaller
if errorlevel 1 goto :error

echo.
echo [2/3] Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist yazn-Dos33-Backup.spec del /q yazn-Dos33-Backup.spec

echo.
echo [3/3] Building EXE...
py -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --uac-admin ^
  --name "yazn-Dos33-Backup" ^
  "yazn-Dos33_backup_clean_multi.py"

if errorlevel 1 goto :error

echo.
echo ================================================
echo SUCCESS
echo dist\yazn-Dos33-Backup.exe
echo ================================================
pause
exit /b 0

:error
echo.
echo BUILD FAILED
pause
exit /b 1
