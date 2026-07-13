# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['bleak.backends.winrt.client', 'bleak.backends.winrt.scanner']
hiddenimports += collect_submodules('winrt')


a = Analysis(
    ['main.py'],
    pathex=['C:\\Users\\16004\\Desktop\\bs1\\bs1_driver'],
    binaries=[('C:\\software\\anaconda3\\Library\\bin\\ffi.dll', '.'), ('C:\\software\\anaconda3\\Library\\bin\\libssl-3-x64.dll', '.'), ('C:\\software\\anaconda3\\Library\\bin\\libcrypto-3-x64.dll', '.'), ('C:\\software\\anaconda3\\Library\\bin\\liblzma.dll', '.'), ('C:\\software\\anaconda3\\Library\\bin\\libexpat.dll', '.'), ('C:\\software\\anaconda3\\Library\\bin\\libmpdec-4.dll', '.')],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['bleak.backends.corebluetooth', 'bleak.backends.bluezdbus', 'bleak.backends.p4android'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='BS1Controller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
