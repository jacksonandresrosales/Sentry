"""Construye Sentry.exe para Windows con una base nueva y vacía."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
STAGING = (ROOT / ".tmp" / "exe_build").resolve()
DIST = (ROOT / "dist").resolve()

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.about import APP_VERSION

INSTALLER_NAME = f"Sentry_Setup_{APP_VERSION}.exe"


def winscp_directory() -> Path:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", "")) / "WinSCP",
        Path(os.environ.get("ProgramFiles", "")) / "WinSCP",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "WinSCP",
    ]
    for folder in candidates:
        if all((folder / name).is_file() for name in ("WinSCP.exe", "WinSCPnet.dll", "license.txt")):
            return folder.resolve()
    raise RuntimeError("No se encontró una instalación completa de WinSCP para incluirla en Sentry.exe.")


def clean_database(destination: Path) -> None:
    """Crea el esquema vigente sin copiar llamadas, bases ni credenciales locales."""
    from app.database import Database

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    database = Database(destination)
    with database.connect() as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("La base inicial no superó la comprobación de integridad.")
        populated = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("calls", "base_jobs", "base_records", "call_bases", "app_settings", "api_credentials")
        }
    if any(populated.values()):
        raise RuntimeError(f"La base inicial contiene datos locales: {populated}")


def inno_compiler() -> Path:
    candidates = (
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    )
    found = next((path.resolve() for path in candidates if path.is_file()), None)
    if found is None:
        raise RuntimeError("No se encontró Inno Setup 6 para generar el instalador.")
    return found


def main() -> int:
    if STAGING.parent != (ROOT / ".tmp").resolve():
        raise RuntimeError("La carpeta temporal no pertenece al proyecto.")
    shutil.rmtree(STAGING, ignore_errors=True)
    STAGING.mkdir(parents=True)

    initial_database = STAGING / "sentry_audit.db"
    clean_database(initial_database)

    obsolete_folder = (DIST / "SentryPortable").resolve()
    if obsolete_folder.parent != DIST:
        raise RuntimeError("La salida antigua no pertenece a dist.")
    shutil.rmtree(obsolete_folder, ignore_errors=True)
    for obsolete_file in (DIST / "SentryPortable.zip", DIST / "SentryPortable.exe", DIST / "Sentry.exe"):
        obsolete_file.unlink(missing_ok=True)
    (DIST / "Instalar_Sentry.exe").unlink(missing_ok=True)
    for previous_installer in DIST.glob("Sentry_Setup_*.exe"):
        previous_installer.unlink()

    environment = os.environ.copy()
    environment["SENTRY_INITIAL_DB"] = str(initial_database)
    environment["SENTRY_WINSCP_DIR"] = str(winscp_directory())
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "Sentry.spec")],
        cwd=ROOT,
        env=environment,
        check=True,
    )

    application = DIST / "SentryApp" / "Sentry.exe"
    if not application.is_file():
        raise RuntimeError("No se generó la aplicación de Sentry.")
    unexpected_icu = [name for name in ("icuuc.dll", "icudt78.dll") if (application.parent / name).exists()]
    if unexpected_icu:
        raise RuntimeError(f"Se incluyeron DLL incompatibles con Qt: {unexpected_icu}")
    version_numbers = [int(value) for value in __import__("re").findall(r"\d+", APP_VERSION)]
    file_version = ".".join(str(value) for value in (version_numbers + [0, 0, 0, 0])[:4])
    subprocess.run(
        [str(inno_compiler()), f"/DMyAppVersion={APP_VERSION}",
         f"/DMyAppFileVersion={file_version}", str(ROOT / "SentryInstaller.iss")],
        cwd=ROOT,
        check=True,
    )
    installer = DIST / INSTALLER_NAME
    if not installer.is_file():
        raise RuntimeError(f"No se generó dist/{INSTALLER_NAME}.")
    bundle = application.parent.resolve()
    if bundle.parent != DIST:
        raise RuntimeError("La carpeta intermedia no pertenece a dist.")
    shutil.rmtree(bundle)
    print(f"Instalador listo: {installer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
