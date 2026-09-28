@echo off
setlocal
title Build .yazn-Dos33 Backup

echo ============================================================
echo              .yazn-Dos33 Backup Builder
echo ============================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found.
    echo Install Python 3 and enable "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

echo [1/3] Installing / updating PyInstaller...
py -m pip install --upgrade pyinstaller
if errorlevel 1 goto :error

echo.
echo [2/3] Cleaning old build files...
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
  "yazn-Dos33_backup.py"

if errorlevel 1 goto :error

echo.
echo ============================================================
echo BUILD SUCCESS
echo.
echo EXE created here:
echo %CD%\dist\yazn-Dos33-Backup.exe
echo ============================================================
echo.
pause
exit /b 0

:error
echo.
echo ============================================================
echo BUILD FAILED
echo Check the error message above.
echo ============================================================
echo.
pause
exit /b 1
