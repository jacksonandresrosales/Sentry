import os
from pathlib import Path


root = Path(SPECPATH)
database = Path(os.environ["SENTRY_INITIAL_DB"])
winscp = Path(os.environ["SENTRY_WINSCP_DIR"])

datas = [
    (str(root / "app" / "ui" / "assets"), "app/ui/assets"),
    (str(database), "data/db"),
    (str(winscp / "license.txt"), "WinSCP"),
]
binaries = [
    (str(winscp / "WinSCP.exe"), "WinSCP"),
    (str(winscp / "WinSCPnet.dll"), "WinSCP"),
]

a = Analysis(
    [str(root / "app" / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest", "unittest", "tkinter",
        "numpy", "PIL", "lxml",
        "setuptools", "wheel", "cryptography",
    ],
    noarchive=False,
    optimize=1,
)
# Qt para Windows usa la API ICU del sistema. El entorno de construcción también
# contiene las DLL privadas de Poppler; incluirlas provoca un fallo al cargar QtGui.
incompatible_icu = {"icuuc.dll", "icudt78.dll"}
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in incompatible_icu]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Sentry",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(root / "app" / "ui" / "assets" / "sentry-app-icon.ico"),
    contents_directory=".",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SentryApp",
)
