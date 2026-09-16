"""Matching real WAV fixtures and the PowerShell filter used for SFTP."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services.base_conversion import BaseAudioIndex
from app.services.winscp_client import _POWERSHELL_FILTERS, match_and_download_remote_audio
from app.ui.views.main_window import (
    IssabelMatchWorker, SentryWindow, audio_filename_metadata, audio_matches_index,
    audio_phone_from_filename, collect_audio_records,
)


class LucidAudioSearchTests(unittest.TestCase):
    def test_fixed_phone_variants_match_lucid_base(self):
        index = BaseAudioIndex({"022345678": frozenset()}, source_system="lucid")
        for phone in ("22345678", "022345678", "59322345678", "0059322345678"):
            with self.subTest(phone=phone):
                self.assertTrue(audio_matches_index(Path(f"out-{phone}-1001-20260916-120000-1234.1.wav"), index))

    def test_lucid_accepts_only_out_recordings_starting_in_september(self):
        lucid = BaseAudioIndex({"0990000001": frozenset()}, source_system="lucid")
        self.assertFalse(audio_matches_index(Path("q-001-0990000001-20260916-120000.wav"), lucid))
        self.assertFalse(audio_matches_index(Path("out-990000001-1001-20260831-120000-1.1.wav"), lucid))
        self.assertTrue(audio_matches_index(Path("out-990000001-1001-20260901-120000-1.1.wav"), lucid))
        self.assertTrue(audio_matches_index(Path("out-990000001-1001-20261001-120000-1.1.wav"), lucid))
        self.assertFalse(audio_matches_index(Path("out-990000001-1001-20260931-120000-1.1.wav"), lucid))
        legacy = BaseAudioIndex({"0990000001": frozenset()})
        self.assertTrue(audio_matches_index(Path("q-001-0990000001-20250831-120000.wav"), legacy))
        self.assertFalse(audio_matches_index(Path("out-990000001-1001-20260901-120000-1.1.wav"), legacy))

    def test_out_metadata_uses_destination_phone_not_extension(self):
        path = Path("out-990000001-1001-20260916-142637-1758031234.987.wav")
        self.assertEqual(audio_phone_from_filename(path), "0990000001")
        self.assertEqual(audio_filename_metadata(path), ("099***0001", "14:26:37"))

    def test_phone_only_search_finds_audio_on_multiple_dates_and_assigns_customer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wanted = []
            for day, phone in (("20260901", "0990000001"), ("20260916", "990000001"),
                               ("20260915", "593990000001"), ("20260916", "0980000002")):
                folder = root / day[:4] / day[4:6] / day[6:]
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"out-{phone}-1001-{day}-120000-1234.1.wav"
                with wave.open(str(path), "wb") as audio:
                    audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                    audio.writeframes(b"\x00\x00" * 8000)
                (folder / f"q-001-{phone}-{day}-120000.wav").write_bytes(path.read_bytes())
                if phone != "0980000002":
                    wanted.append(path)
            lucid = BaseAudioIndex({"0990000001": frozenset()}, {"0990000001": "CLIENTE PRUEBA"}, "lucid")
            paths, records = collect_audio_records(root, lucid)
            self.assertEqual(set(paths), set(wanted))
            self.assertEqual(len(records), 3)
            view = SimpleNamespace(active_base_index=lucid)
            self.assertTrue(all(SentryWindow._client_name_for_call(view, record) == "CLIENTE PRUEBA"
                                for record in records))
            dated = BaseAudioIndex({"0990000001": frozenset({"20260916"})})
            dated_paths, _ = collect_audio_records(root, dated)
            self.assertEqual(len(dated_paths), 1)

    @patch("app.ui.views.main_window.match_and_download_remote_audio")
    def test_lucid_remote_worker_searches_configured_root_without_a_date_filter(self, download):
        download.return_value = {"paths": [], "candidate_count": 1, "match_count": 0}
        worker = IssabelMatchWorker(
            {"remote_path": "/monitor/2026/09/"},
            BaseAudioIndex({"0990000001": frozenset()}, source_system="lucid"),
            Path(".tmp/lucid-audio-test"),
        )
        errors = []
        worker.failed.connect(errors.append)
        worker.run()
        self.assertEqual(errors, [])
        request, phones, _ = download.call_args.args
        self.assertEqual(request["remote_paths"], ["/monitor/2026/09/"])
        self.assertTrue(request["recursive"])
        self.assertEqual(request["source_system"], "lucid")
        self.assertEqual(request["audio_since"], "20260901")
        self.assertEqual(phones, {"0990000001": frozenset()})
        self.assertEqual(download.call_args.kwargs["limit"], 0)
        self.assertTrue(callable(download.call_args.kwargs["progress_callback"]))
        self.assertTrue(callable(download.call_args.kwargs["should_cancel"]))

    @patch("app.services.winscp_client.winscp_dll", return_value=Path("C:/WinSCP/WinSCPnet.dll"))
    @patch("app.services.winscp_client.shutil.which", return_value="powershell.exe")
    @patch("app.services.winscp_client.subprocess.run")
    def test_fifty_thousand_phones_use_stdin_and_no_silent_result_limit(self, run, _which, _dll):
        run.return_value = SimpleNamespace(returncode=0, stdout='{"paths":[]}', stderr="")
        phones = {f"09{number:08d}": frozenset() for number in range(50000)}
        match_and_download_remote_audio(
            {"host": "192.0.2.1", "username": "test", "password": "test-password",
             "fingerprint": "test-fingerprint", "remote_path": "/monitor"},
            phones, Path(".tmp/lucid-audio-test"),
        )
        arguments = run.call_args.kwargs
        self.assertEqual(len(json.loads(arguments["input"])["phone_dates"]), 50000)
        self.assertNotIn("SENTRY_SFTP_PHONE_DATES", arguments["env"])
        self.assertNotIn("SENTRY_SFTP_PHONE_PATTERN", arguments["env"])
        self.assertEqual(arguments["env"]["SENTRY_SFTP_LIMIT"], "0")
        self.assertNotIn("test-password", arguments["input"])

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell requerido")
    def test_actual_powershell_builds_large_phone_index_without_per_row_commands(self):
        script = _POWERSHELL_FILTERS + r'''
ConvertTo-Json -InputObject @(
    $phoneDates.Count,
    (Test-SentryAudioMatch 'q-001-0900000000-20260901-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-900049999-20260916-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0980000002-20260916-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0980000002-20260915-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0999999999-20260916-120000.wav' $true)
) -Compress
'''
        phone_dates = [[f"09{number:08d}", []] for number in range(50000)]
        phone_dates.extend([["900000000", ["20260916"]], ["0980000002", ["20260916"]]])
        completed = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
            input=json.dumps({"phone_dates": phone_dates, "phones": []}),
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [50001, True, True, True, False, False])

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell requerido")
    def test_actual_powershell_filter_accepts_phone_variants_and_preserves_date_constraints(self):
        script = _POWERSHELL_FILTERS + r'''
$results = @(
    (Test-SentryAudioMatch 'q-001-0990000001-20260916-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-990000001-20260901-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-593990000001-20260915-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0980000002-20260916-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0980000002-20260915-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0970000003-20260916-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0990000001-20260231-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-22345678-20260916-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-59322345678-20260916-120000.wav' $true),
    (Test-SentryAudioMatch 'q-001-0059322345678-20260916-120000.wav' $true)
)
ConvertTo-Json -InputObject $results -Compress
'''
        completed = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
            input=json.dumps({"phone_dates": [["0990000001", []], ["0980000002", ["20260916"]],
                                              ["022345678", []]], "phones": []}),
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [True, True, True, True, False, False, False, True, True, True])
