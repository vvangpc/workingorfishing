@echo off
REM 在项目根目录运行
pushd %~dp0\..
pyinstaller --noconfirm --clean packaging\onefile.spec
popd
echo.
echo Done. Output: dist\WorkingorFishing-portable.exe
