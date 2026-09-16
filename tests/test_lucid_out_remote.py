"""Run the real remote-filter PowerShell without connecting to an SFTP server."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.winscp_client import _POWERSHELL_FILTERS, match_and_download_remote_audio


@unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell requerido")
class LucidOutRemoteFilterTests(unittest.TestCase):
    def run_filter(self, names, *, source="lucid", phone_dates=None, directory=False):
        command = "Test-SentryDirectory $name" if directory else "Test-SentryAudioMatch $name $true"
        script = _POWERSHELL_FILTERS + (
            "\n$results = @(foreach ($name in $filters.test_names) { " + command + " })\n"
            "ConvertTo-Json -InputObject $results -Compress\n"
        )
        payload = {
            "source_system": source,
            "audio_since": "20260901",
            "phone_dates": phone_dates if phone_dates is not None else [["0990000001", []]],
            "phones": [],
            "test_names": names,
        }
        completed = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
            input=json.dumps(payload), capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_lucid_out_uses_destination_and_normalizes_national_and_country_prefixes(self):
        names = [
            f"out-{phone}-1001-20260916-142637-1758031234.987.wav"
            for phone in ("0990000001", "990000001", "593990000001", "00593990000001")
        ] + [
            "OUT-990000001-1001-20260916-142637-1758031234.987.WAV",
            "q-001-0990000001-20260916-142637.wav",
            "in-990000001-1001-20260916-142637-1758031234.987.wav",
            "out-980000002-990000001-20260916-142637-1758031234.987.wav",
        ]
        self.assertEqual(self.run_filter(names), [True] * 5 + [False] * 3)

    def test_lucid_fixed_numbers_also_accept_zero_and_country_prefix(self):
        names = [
            f"out-{phone}-1001-20260916-142637-1758031234.987.wav"
            for phone in ("22345678", "022345678", "59322345678", "0059322345678")
        ]
        self.assertEqual(self.run_filter(names, phone_dates=[["022345678", []]]), [True] * 4)

    def test_lucid_rejects_august_and_invalid_dates_but_accepts_september_onward(self):
        days = [
            "20150901", "20250831", "20260831", "20260901", "20260916", "20260930",
            "20261001", "20261231", "20270101", "20260931", "20261131", "20261301",
            "20260900", "20260999", "20270229", "20280229",
        ]
        names = [f"out-990000001-1001-{day}-120000-1234.1.wav" for day in days]
        self.assertEqual(self.run_filter(names), [
            False, False, False, True, True, True, True, True, True,
            False, False, False, False, False, False, True,
        ])

    def test_explicit_phone_dates_do_not_reenable_recordings_before_september(self):
        names = [f"out-990000001-1001-{day}-120000-1234.1.wav"
                 for day in ("20260831", "20260901", "20260916")]
        dates = [["0990000001", ["20260831", "20260916"]]]
        self.assertEqual(self.run_filter(names, phone_dates=dates), [False, False, True])

    def test_issabel_preserves_q_pattern_and_dated_matching_without_lucid_cutoff(self):
        names = [
            "q-001-0990000001-20150901-120000.wav",
            "q-001-990000001-20260831-120000.wav",
            "q-001-593990000001-20260916-120000.wav",
            "q-001-0980000002-20260916-120000.wav",
            "q-001-0980000002-20260915-120000.wav",
            "q-001-0990000001-20260931-120000.wav",
            "out-990000001-1001-20260916-120000-1234.1.wav",
        ]
        dates = [["0990000001", []], ["0980000002", ["20260916"]]]
        self.assertEqual(self.run_filter(names, source="issabel", phone_dates=dates),
                         [True, True, True, True, False, False, False])

    def test_directory_pruning_skips_old_years_and_months_but_keeps_custom_folders(self):
        paths = [
            "/monitor/2015", "/monitor/2015/09/", "/monitor/2025/12/31/",
            "/monitor/2026", "/monitor/2026/01", "/monitor/2026/08/",
            "/monitor/2026/08/31", "/monitor/2026/09", "/monitor/2026/09/01/",
            "/monitor/2026/09/16", "/monitor/2026/10/", "/monitor/2026/11/",
            "/monitor/2026/12/31", "/monitor/2027", "/monitor/2027/01/01/",
            "/monitor", "/monitor/custom-folder", "/monitor/campaign/september",
        ]
        expected = [False, False, False, True, False, False, False] + [True] * 11
        self.assertEqual(self.run_filter(paths, directory=True), expected)
        self.assertEqual(self.run_filter(paths, directory=True, source="issabel"), [True] * len(paths))


class LucidOutRemoteRequestTests(unittest.TestCase):
    @patch("app.services.winscp_client.winscp_dll", return_value=Path("WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value="powershell.exe")
    @patch("app.services.winscp_client.subprocess.run")
    def test_lucid_request_sends_source_and_default_september_cutoff(self, run, _which, _dll):
        run.return_value = SimpleNamespace(returncode=0, stdout='{"paths":[]}', stderr="")
        match_and_download_remote_audio(
            {"host": "192.0.2.1", "username": "test", "password": "test-password",
             "fingerprint": "test-fingerprint", "remote_path": "/monitor", "source_system": "lucid"},
            {"0990000001": frozenset()}, Path(".tmp/lucid-out-test"),
        )
        filters = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(filters["source_system"], "lucid")
        self.assertEqual(filters["audio_since"], "20260901")
        self.assertEqual(filters["phone_dates"], [["0990000001", []]])


if __name__ == "__main__":
    unittest.main()
