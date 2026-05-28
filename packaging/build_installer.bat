@echo off
REM 1) 先用 PyInstaller 打 onedir
REM 2) 再用 Inno Setup 6 的 ISCC.exe 编译安装包
REM 默认 ISCC 路径为 %ProgramFiles(x86)%\Inno Setup 6\ISCC.exe，可按需修改

pushd %~dp0\..
pyinstaller --noconfirm --clean packaging\onedir.spec
if errorlevel 1 (
    echo PyInstaller failed.
    popd
    exit /b 1
)

set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo ISCC.exe not found at %ISCC%
    echo Please install Inno Setup 6 or edit this script.
    popd
    exit /b 1
)

%ISCC% packaging\installer.iss
popd
echo.
echo Done. Installer at: dist\installer\
