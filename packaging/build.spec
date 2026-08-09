# -*- mode: python ; coding: utf-8 -*-

import os
import sys

spec_dir = SPECPATH
project_dir = os.path.abspath(os.path.join(spec_dir, ".."))

datas = [
    (os.path.join(project_dir, ".output", "public"), os.path.join(".output", "public")),
    (os.path.join(project_dir, "packaging", "ffmpeg"), "ffmpeg"),
]

hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtCore",
    "PySide6.QtWidgets",
    "yt_dlp",
    "core",
    "core.extractor",
    "core.downloader",
    "core.task_queue",
    "shell",
    "shell.qt_bridge",
]

a = Analysis(
    [os.path.join(project_dir, "shell", "main.py")],
    pathex=[project_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Origin",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Origin",
)
