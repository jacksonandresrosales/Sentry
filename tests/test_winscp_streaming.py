from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.services import winscp_client


def _config() -> dict[str, str]:
    return {
        "host": "192.0.2.10",
        "port": "22",
        "username": "usuario-streaming-prueba",
        "password": "secreto-streaming-prueba",
        "remote_path": "/grabaciones",
        "fingerprint": "ssh-ed25519 255 SHA256:huella-prueba",
    }


def _event(name: str, *, paths=(), candidates=1, matches=1, phase="download") -> str:
    return json.dumps({
        "event": name,
        "phase": phase,
        "paths": [str(path) for path in paths],
        "candidate_count": candidates,
        "match_count": matches,
    })


@contextmanager
def _python_transport(script: str):
    """Exercise real pipe/thread behavior without a server or WinSCP installation."""
    real_popen = subprocess.Popen
    processes = []
    calls = []

    def launch(command, *args, **kwargs):
        if command[0] != "powershell.exe":
            return real_popen(command, *args, **kwargs)
        calls.append((command, kwargs))
        process = real_popen([sys.executable, "-u", "-c", script], *args, **kwargs)
        processes.append(process)
        return process

    with (
        patch.object(winscp_client, "winscp_dll", return_value=Path("WinSCPnet.dll")),
        patch.object(winscp_client.shutil, "which", return_value="powershell.exe"),
        patch.object(winscp_client.subprocess, "Popen", side_effect=launch),
    ):
        try:
            yield calls, processes
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


class WinSCPStreamingTests(unittest.TestCase):
    def test_progress_arrives_while_search_is_still_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "q-000-0990000001-20260916-120000.wav"
            audio.touch()
            progress = _event("progress", paths=[audio], candidates=31)
            result = _event("result", paths=[audio], candidates=42)
            script = (
                "import sys, time\n"
                "sys.stdin.read()\n"
                f"print({progress!r}, flush=True)\n"
                "time.sleep(0.6)\n"
                f"print({result!r}, flush=True)\n"
            )
            observed = []
            with _python_transport(script) as (_, processes):
                def on_progress(event):
                    observed.append((event, processes[0].poll()))

                result = winscp_client.match_and_download_remote_audio(
                    _config(), {"0990000001": frozenset()}, Path(temporary),
                    progress_callback=on_progress,
                )

            self.assertTrue(observed, "The caller must receive incremental progress.")
            downloaded = [item for item in observed if item[0].get("paths")]
            self.assertTrue(downloaded)
            self.assertIsNone(downloaded[0][1], "Progress must not be buffered until process exit.")
            self.assertEqual(downloaded[0][0]["candidate_count"], 31)
            self.assertEqual(result["paths"], [audio.resolve()])
            self.assertEqual(result["candidate_count"], 42)
            self.assertEqual(result["match_count"], 1)

    def test_cancel_returns_already_downloaded_audio_without_waiting_for_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            audio = Path(temporary) / "q-000-0990000001-20260916-120000.wav"
            audio.touch()
            progress = _event("progress", paths=[audio], candidates=31)
            script = (
                "import sys, time\n"
                "sys.stdin.read()\n"
                f"print({progress!r}, flush=True)\n"
                "time.sleep(30)\n"
            )
            canceled = threading.Event()

            def on_progress(event):
                if event.get("paths"):
                    canceled.set()

            start = time.monotonic()
            with _python_transport(script) as (_, processes):
                result = winscp_client.match_and_download_remote_audio(
                    _config(), {"0990000001": frozenset()}, Path(temporary),
                    progress_callback=on_progress, should_cancel=canceled.is_set,
                )
                self.assertIsNotNone(processes[0].poll(), "Cancellation must stop its child process.")
            self.assertLess(time.monotonic() - start, 8)
            self.assertTrue(result["canceled"])
            self.assertEqual(result["paths"], [audio.resolve()])
            self.assertEqual(result["candidate_count"], 31)
            self.assertEqual(result["match_count"], 1)

    def test_cancel_callback_alone_selects_streaming_transport(self):
        final = _event("result", candidates=4, matches=0)
        script = f"import sys\nsys.stdin.read()\nprint({final!r}, flush=True)\n"
        with tempfile.TemporaryDirectory() as temporary:
            with _python_transport(script) as (calls, _):
                result = winscp_client.match_and_download_remote_audio(
                    _config(), {"0990000001": frozenset()}, Path(temporary),
                    should_cancel=lambda: False,
                )
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["paths"], [])
            self.assertEqual(result["candidate_count"], 4)

    def test_cancellation_accumulates_paths_from_incremental_batches(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "q-000-0990000001-20260916-120000.wav"
            second = Path(temporary) / "q-000-0990000001-20260916-120500.wav"
            first.touch()
            second.touch()
            script = (
                "import sys, time\n"
                "sys.stdin.read()\n"
                f"print({_event('progress', paths=[first], candidates=3)!r}, flush=True)\n"
                "time.sleep(0.1)\n"
                f"print({_event('progress', paths=[second], candidates=8, matches=2)!r}, flush=True)\n"
                "time.sleep(30)\n"
            )
            canceled = threading.Event()

            def on_progress(event):
                if event.get("match_count") == 2:
                    canceled.set()

            with _python_transport(script):
                result = winscp_client.match_and_download_remote_audio(
                    _config(), {"0990000001": frozenset()}, Path(temporary),
                    progress_callback=on_progress, should_cancel=canceled.is_set,
                )
            self.assertTrue(result["canceled"])
            self.assertEqual(result["paths"], [first.resolve(), second.resolve()])
            self.assertEqual(result["candidate_count"], 8)
            self.assertEqual(result["match_count"], 2)

    def test_stream_failure_is_visible_and_redacts_credentials(self):
        progress = _event("progress", candidates=4, matches=0, phase="search")
        script = (
            "import sys\n"
            "sys.stdin.read()\n"
            f"print({progress!r}, flush=True)\n"
            "print('Error SFTP para usuario-streaming-prueba con secreto-streaming-prueba', "
            "file=sys.stderr, flush=True)\n"
            "sys.exit(7)\n"
        )
        observed = []
        with tempfile.TemporaryDirectory() as temporary:
            with _python_transport(script):
                with self.assertRaises(winscp_client.WinSCPError) as raised:
                    winscp_client.match_and_download_remote_audio(
                        _config(), {"0990000001": frozenset()}, Path(temporary),
                        progress_callback=observed.append,
                    )
        detail = str(raised.exception)
        self.assertIn("Error SFTP", detail)
        self.assertNotIn(_config()["username"], detail)
        self.assertNotIn(_config()["password"], detail)
        self.assertTrue(observed)

    def test_transport_keeps_credentials_out_of_arguments_and_stdin(self):
        script = (
            "import json, os, sys\n"
            "filters = json.loads(sys.stdin.read())\n"
            "assert filters['phone_dates'] == [['0990000001', []]]\n"
            "assert os.environ['SENTRY_SFTP_PASSWORD'] == 'secreto-streaming-prueba'\n"
            "assert 'secreto-streaming-prueba' not in json.dumps(filters)\n"
            "assert 'usuario-streaming-prueba' not in json.dumps(filters)\n"
            f"print({_event('result', candidates=1, matches=0)!r}, flush=True)\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            with _python_transport(script) as (calls, _):
                result = winscp_client.match_and_download_remote_audio(
                    _config(), {"0990000001": frozenset()}, Path(temporary),
                    progress_callback=lambda event: None,
                )
            self.assertEqual(result["paths"], [])
            command, kwargs = calls[0]
            self.assertNotIn(_config()["password"], " ".join(command))
            self.assertNotIn(_config()["username"], " ".join(command))
            self.assertEqual(kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_process_exit_without_final_result_is_not_a_successful_empty_search(self):
        progress = _event("progress", candidates=120, matches=0, phase="search")
        script = f"import sys\nsys.stdin.read()\nprint({progress!r}, flush=True)\n"
        with tempfile.TemporaryDirectory() as temporary:
            with _python_transport(script):
                with self.assertRaises(winscp_client.WinSCPError):
                    winscp_client.match_and_download_remote_audio(
                        _config(), {"0990000001": frozenset()}, Path(temporary),
                        progress_callback=lambda event: None,
                    )

    def test_timeout_stops_process_and_explains_failure(self):
        script = "import sys, time\nsys.stdin.read()\ntime.sleep(30)\n"
        start = time.monotonic()
        with tempfile.TemporaryDirectory() as temporary:
            with (
                _python_transport(script) as (_, processes),
                patch.object(winscp_client, "MATCH_DOWNLOAD_TIMEOUT_SECONDS", 0.2),
            ):
                with self.assertRaises(winscp_client.WinSCPError) as raised:
                    winscp_client.match_and_download_remote_audio(
                        _config(), {"0990000001": frozenset()}, Path(temporary),
                        progress_callback=lambda event: None,
                    )
                self.assertIsNotNone(processes[0].poll())
        self.assertLess(time.monotonic() - start, 8)
        self.assertIn("segundos", str(raised.exception))
        self.assertIn("cancelada", str(raised.exception))

    def test_large_stderr_is_drained_without_blocking_stdout(self):
        script = (
            "import sys\n"
            "sys.stdin.read()\n"
            "sys.stderr.write('diagnostic line\\n' * 20000)\n"
            "sys.stderr.flush()\n"
            "print('Error SFTP diagnostic final', file=sys.stderr, flush=True)\n"
            "sys.exit(3)\n"
        )
        start = time.monotonic()
        with tempfile.TemporaryDirectory() as temporary:
            with (
                _python_transport(script),
                patch.object(winscp_client, "MATCH_DOWNLOAD_TIMEOUT_SECONDS", 5),
            ):
                with self.assertRaises(winscp_client.WinSCPError) as raised:
                    winscp_client.match_and_download_remote_audio(
                        _config(), {"0990000001": frozenset()}, Path(temporary),
                        progress_callback=lambda event: None,
                    )
        self.assertLess(time.monotonic() - start, 8)
        self.assertIn("Error SFTP diagnostic final", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
