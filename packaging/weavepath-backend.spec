from pathlib import Path


ROOT = Path(SPECPATH).resolve().parent
BACKEND = ROOT / "backend"
WEB = ROOT / "apps" / "web" / "dist"

if not (WEB / "index.html").is_file():
    raise RuntimeError("Build apps/web before running PyInstaller")

a = Analysis(
    [str(BACKEND / "desktop.py")],
    pathex=[str(BACKEND), str(ROOT)],
    binaries=[],
    datas=[(str(WEB), "web")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WeavePathBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Electron starts the sidecar with windowsHide and captures stdout/stderr.
    # Keeping a console-capable bootloader makes startup failures diagnosable
    # instead of silently disappearing before the shell can report them.
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WeavePathBackend",
)
