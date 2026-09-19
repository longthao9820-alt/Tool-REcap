# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


repo = Path(SPECPATH)
desktop = repo / "apps" / "desktop"

a = Analysis(
    [str(desktop / "auto_main.py")],
    pathex=[str(desktop)],
    binaries=[],
    datas=[
        (str(desktop / "recap_tool" / "voice_worker.py"), "recap_tool"),
        (str(desktop / "recap_tool" / "stt_worker.py"), "recap_tool"),
        (str(repo / "PROMPT_RECAP_TVSHOW_ORIGINAL_FACEBOOK.md"), "."),
        (str(repo / "PROMPT_RECAP_GERMAN_SOAP_HOAN_CHINH.md"), "."),
        (str(repo / "schemas" / "recap-project-batch-v2.json"), "schemas"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="Toolrecap-Auto",
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

