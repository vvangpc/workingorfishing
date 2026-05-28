# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：单文件便携版。
# 用法：在项目根目录执行  pyinstaller packaging/onefile.spec

from PyInstaller.utils.hooks import collect_submodules
import os

block_cipher = None
project_root = os.path.abspath(os.path.join(os.path.dirname(SPEC), '..'))

hiddenimports = ['uiautomation'] + collect_submodules('PySide6.QtCharts')

icon_path = os.path.join(project_root, 'assets', 'icon.ico')
icon_arg = icon_path if os.path.exists(icon_path) else None

datas = [
    (os.path.join(project_root, 'src', 'default_rules.yaml'), '.'),
]
assets_dir = os.path.join(project_root, 'assets')
if os.path.isdir(assets_dir) and any(
    f.lower().endswith('.ico') for f in os.listdir(assets_dir)
):
    datas.append((assets_dir, 'assets'))

a = Analysis(
    [os.path.join(project_root, 'run.py')],
    pathex=[project_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='WorkingorFishing-portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                       # 无控制台窗口
    icon=icon_arg,
    onefile=True,
)
