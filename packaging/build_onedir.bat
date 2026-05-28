@echo off
pushd %~dp0\..
pyinstaller --noconfirm --clean packaging\onedir.spec
popd
echo.
echo Done. Output: dist\WorkingorFishing\
