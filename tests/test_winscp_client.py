from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.services.winscp_client import (
    WinSCPError, download_remote_audio, scan_host_fingerprint, search_remote_audio, test_connection,
)


def config(**changes):
    values = {
        "host": "192.0.2.10",
        "port": "22",
        "username": "usuario-prueba",
        "password": "secreto-prueba",
        "remote_path": "/grabaciones",
        "fingerprint": "ssh-ed25519 255 SHA256:huella-prueba",
    }
    values.update(changes)
    return values


class WinSCPClientTests(unittest.TestCase):
    @patch("app.services.winscp_client.winscp_dll", return_value=Path(r"C:\WinSCP\WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value=r"C:\Windows\powershell.exe")
    @patch("app.services.winscp_client.subprocess.run")
    def test_fingerprint_uses_environment_not_command_arguments(self, run, _which, _dll):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout="ssh-ed25519 255 SHA256:huella-prueba\n",
            stderr="",
        )

        fingerprint = scan_host_fingerprint(config(username="", password="", fingerprint=""))

        self.assertEqual(fingerprint, "ssh-ed25519 255 SHA256:huella-prueba")
        arguments = run.call_args.args[0]
        self.assertNotIn("secreto-prueba", " ".join(arguments))
        self.assertNotIn("usuario-prueba", " ".join(arguments))
        self.assertEqual(run.call_args.kwargs["env"]["SENTRY_SFTP_PASSWORD"], "")

    def test_connection_requires_verified_fingerprint(self):
        with self.assertRaisesRegex(WinSCPError, "huella SSH"):
            test_connection(config(fingerprint=""))

    @patch("app.services.winscp_client.winscp_dll", return_value=Path(r"C:\WinSCP\WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value=r"C:\Windows\powershell.exe")
    @patch("app.services.winscp_client.subprocess.run")
    def test_remote_audio_search_parses_files(self, run, _which, _dll):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout='[{"path":"/monitor/2026/09/q-000-0990000001.wav","name":"q-000-0990000001.wav",'
                   '"size":16000,"modified":"2026-09-15 10:30:00"}]',
            stderr="",
        )

        files = search_remote_audio(config(), "0990000001")

        self.assertEqual(files[0]["name"], "q-000-0990000001.wav")
        self.assertEqual(run.call_args.kwargs["env"]["SENTRY_SFTP_QUERY"], "0990000001")

        search_remote_audio(
            {**config(), "remote_paths": ["/monitor/2026/07/06/"], "recursive": False,
             "phones": ["0990000001", "0980000002"]},
            "",
        )
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["SENTRY_SFTP_PATHS"], '["/monitor/2026/07/06/"]')
        self.assertEqual(environment["SENTRY_SFTP_RECURSIVE"], "0")
        self.assertEqual(environment["SENTRY_SFTP_PHONE_PATTERN"], "0990000001|0980000002")

    @patch("app.services.winscp_client.winscp_dll", return_value=Path(r"C:\WinSCP\WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value=r"C:\Windows\powershell.exe")
    @patch("app.services.winscp_client.subprocess.run")
    def test_legacy_winscp_fingerprint_format_is_accepted(self, run, _which, _dll):
        legacy = "ssh-ed25519 255 ZNdBeIQT9ANspDxRdKYicFjOzkW6iKyyr+XMb/zVZuw="
        run.return_value = SimpleNamespace(returncode=0, stdout=legacy, stderr="")

        self.assertEqual(scan_host_fingerprint(config(username="", password="", fingerprint="")), legacy)

    @patch("app.services.winscp_client.winscp_dll", return_value=Path(r"C:\WinSCP\WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value=r"C:\Windows\powershell.exe")
    @patch("app.services.winscp_client.subprocess.run")
    def test_download_keeps_remote_file_and_reuses_local_target(self, run, _which, _dll):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "q-000-0990000001-20260701-120000.wav"
            target.touch()
            run.return_value = SimpleNamespace(returncode=0, stdout=f'"{str(target).replace(chr(92), chr(92)*2)}"', stderr="")

            downloaded = download_remote_audio(
                config(), ["/monitor/2026/07/01/q-000-0990000001-20260701-120000.wav"], Path(temp)
            )

            self.assertEqual(downloaded, [target.resolve()])
            self.assertIn("q-000-0990000001", run.call_args.kwargs["env"]["SENTRY_SFTP_FILES"])

    @patch("app.services.winscp_client.winscp_dll", return_value=Path(r"C:\WinSCP\WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value=r"C:\Windows\powershell.exe")
    @patch("app.services.winscp_client.subprocess.run")
    def test_errors_do_not_expose_username_or_password(self, run, _which, _dll):
        run.return_value = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Fallo para usuario-prueba con secreto-prueba",
        )

        with self.assertRaises(WinSCPError) as raised:
            test_connection(config())

        self.assertNotIn("usuario-prueba", str(raised.exception))
        self.assertNotIn("secreto-prueba", str(raised.exception))

    @patch("app.services.winscp_client.winscp_dll", return_value=Path(r"C:\WinSCP\WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value=r"C:\Windows\powershell.exe")
    @patch("app.services.winscp_client.subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 45))
    def test_timeout_has_clear_message(self, _run, _which, _dll):
        with self.assertRaisesRegex(WinSCPError, "45 segundos"):
            test_connection(config())


if __name__ == "__main__":
    unittest.main()
