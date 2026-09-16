"""Integración Inno real con aplicación ficticia; nunca instala sobre Sentry."""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
ISCC = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inno Setup 6/ISCC.exe"
CSC = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
AVAILABLE = sys.platform == "win32" and ISCC.is_file() and CSC.is_file()


@unittest.skipUnless(AVAILABLE, "Requiere Windows, Inno Setup y compilador .NET Framework")
class InstallerUpdateTests(unittest.TestCase):
    def test_update_waits_for_parent_preserves_data_and_restarts(self):
        with tempfile.TemporaryDirectory(prefix="sentry-installer-test-") as temporary:
            folder = Path(temporary)
            payload = folder / "payload"
            payload.mkdir()
            destination = folder / "program"
            client_data = folder / "client-data"
            client_data.mkdir()
            database = client_data / "client.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE client (id TEXT, value TEXT)")
                connection.execute("INSERT INTO client VALUES ('00123', 'preserve')")
                connection.commit()
            expected_database = database.read_bytes()
            recording = client_data / "recording.wav"
            recording.write_bytes(b"original recording fixture")
            marker = folder / "restart.txt"

            # Un ejecutable GUI mínimo evita abrir consolas o ejecutar la app del cliente.
            source = folder / "Program.cs"
            source.write_text(
                'using System; using System.IO; using System.Diagnostics; class Program { static void Main() { '
                'var p = Environment.GetEnvironmentVariable("SENTRY_TEST_RESTART"); '
                'if (!String.IsNullOrEmpty(p)) File.WriteAllText(p, Process.GetCurrentProcess().Id.ToString()); } }',
                encoding="utf-8",
            )
            self.run_hidden([str(CSC), "/nologo", "/target:winexe",
                             f"/out:{payload / 'Sentry.exe'}", str(source)])
            version_file = payload / "payload.txt"
            version_file.write_text("old", encoding="utf-8")
            configuration = (ROOT / "SentryInstaller.iss").read_text(encoding="utf-8")
            configuration = re.sub(r"(?m)^AppId=.*$", f"AppId=SentryTest-{uuid.uuid4().hex}", configuration)
            configuration = re.sub(r"(?m)^DefaultDirName=.*$", lambda _: f"DefaultDirName={destination}", configuration)
            configuration = re.sub(r"(?m)^OutputDir=.*$", lambda _: f"OutputDir={folder}", configuration)
            configuration = re.sub(r"(?m)^OutputBaseFilename=.*$", "OutputBaseFilename=test-setup", configuration)
            configuration = re.sub(r"(?m)^SetupIconFile=.*$", lambda _: f"SetupIconFile={ROOT / 'app/ui/assets/sentry-installer-icon.ico'}", configuration)
            configuration = re.sub(r'(?m)^Source: .*$', lambda _: f'Source: "{payload}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs', configuration)
            configuration = re.sub(r"\[Icons\].*?(?=\[Run\])", "", configuration, flags=re.S)
            configuration = configuration.replace("Compression=lzma2/ultra64", "Compression=none")
            configuration = configuration.replace("[Setup]", "[Setup]\nUninstallable=no\nCreateUninstallRegKey=no")
            test_mutex = f"SentryTest-{uuid.uuid4().hex}"
            configuration = configuration.replace("Ecuaconexion.Sentry", test_mutex)
            script = folder / "test.iss"
            script.write_text(configuration, encoding="utf-8-sig")
            self.run_hidden([str(ISCC), "/Q", str(script)])
            installer = folder / "test-setup.exe"
            arguments = [str(installer), "/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                         "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", f"/DIR={destination}"]
            self.run_hidden(arguments)
            self.assertEqual((destination / "payload.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse(marker.exists())

            # Actualiza los archivos del programa, conservando la carpeta externa del cliente.
            version_file.write_text("new", encoding="utf-8")
            self.run_hidden([str(ISCC), "/Q", str(script)])
            environment = os.environ.copy()
            environment["SENTRY_TEST_RESTART"] = str(marker)
            parent_script = ("import ctypes,time; from ctypes import wintypes; "
                             "f=ctypes.windll.kernel32.CreateMutexW; f.restype=wintypes.HANDLE; "
                             f"handle=f(None,False,{test_mutex!r}); time.sleep(4)")
            with subprocess.Popen([sys.executable, "-c", parent_script],
                                  creationflags=subprocess.CREATE_NO_WINDOW) as parent:
                with subprocess.Popen(arguments + ["/SENTRYUPDATE=1", f"/UPDATEFROMPID={parent.pid}"],
                                      env=environment, creationflags=subprocess.CREATE_NO_WINDOW) as updater:
                    time.sleep(1)
                    self.assertIsNone(parent.poll())
                    self.assertEqual((destination / "payload.txt").read_text(encoding="utf-8"), "old")
                    self.assertEqual(updater.wait(timeout=45), 0)
                self.assertIsNotNone(parent.poll())
            self.wait_for_restarted_fixture(marker)
            self.assertEqual((destination / "payload.txt").read_text(encoding="utf-8"), "new")
            self.assertEqual(database.read_bytes(), expected_database)
            self.assertEqual(recording.read_bytes(), b"original recording fixture")
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT * FROM client").fetchall(), [("00123", "preserve")])

            # Caso habitual: Sentry ya terminó cuando el instalador llega a PrepareToInstall.
            marker.unlink()
            self.run_hidden(arguments + ["/SENTRYUPDATE=1", f"/UPDATEFROMPID={parent.pid}"], env=environment)
            self.wait_for_restarted_fixture(marker)
            self.assertEqual(database.read_bytes(), expected_database)

    def wait_for_restarted_fixture(self, marker: Path, timeout: float = 10):
        """Espera solo el PID del ejecutable ficticio antes de borrar su directorio."""
        import ctypes
        from ctypes import wintypes

        deadline = time.monotonic() + timeout
        process_id = None
        while time.monotonic() < deadline:
            try:
                value = marker.read_text(encoding="utf-8").strip()
            except (FileNotFoundError, PermissionError):
                value = ""
            if value.isdigit() and int(value) > 0:
                process_id = int(value)
                break
            time.sleep(0.05)
        self.assertIsNotNone(process_id, "El instalador no relanzó el ejecutable ficticio")

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL

        handle = kernel.OpenProcess(0x00100000, False, process_id)  # SYNCHRONIZE: solo esperar.
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: el PID ya terminó.
                return
            raise ctypes.WinError(error)
        try:
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            status = kernel.WaitForSingleObject(handle, remaining_ms)
            if status == 0xFFFFFFFF:  # WAIT_FAILED.
                raise ctypes.WinError(ctypes.get_last_error())
            self.assertEqual(status, 0, "El ejecutable ficticio relanzado no terminó dentro del plazo")
        finally:
            kernel.CloseHandle(handle)

    def run_hidden(self, command, **options):
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, **options,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
