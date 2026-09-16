"""Ejercita el recorrido real de PowerShell con un servidor .NET en memoria."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app.services.winscp_client import (
    _POWERSHELL, _POWERSHELL_FILTERS, _POWERSHELL_SESSION, _powershell_command,
)


_FAKE_WINSCP = r'''
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
namespace WinSCP {
    public enum Protocol { Sftp }
    public sealed class SessionOptions {
        public Protocol Protocol; public string HostName; public int PortNumber;
        public string UserName; public string Password; public string SshHostKeyFingerprint;
    }
    public sealed class RemoteFileInfo {
        public string FullName; public string Name; public long Length;
        public DateTime LastWriteTime; public bool IsDirectory; public bool IsSymbolicLink;
        public byte Content;
    }
    public sealed class RemoteDirectoryInfo { public List<RemoteFileInfo> Files; }
    public sealed class TransferOperationResult { public void Check() {} }
    public static class RemotePath { public static string EscapeFileMask(string path) { return path; } }
    public sealed class Session {
        public static Dictionary<string, List<RemoteFileInfo>> Fixtures = new Dictionary<string, List<RemoteFileInfo>>();
        public static List<string> Visited = new List<string>();
        public static List<string> Downloads = new List<string>();
        public void Open(SessionOptions options) {}
        public RemoteDirectoryInfo ListDirectory(string path) {
            Visited.Add(path);
            return new RemoteDirectoryInfo { Files = Fixtures[path] };
        }
        public TransferOperationResult GetFiles(string source, string target, bool remove) {
            Downloads.Add(source);
            foreach (List<RemoteFileInfo> folder in Fixtures.Values) {
                foreach (RemoteFileInfo file in folder) {
                    if (file.FullName != source) { continue; }
                    byte[] data = new byte[file.Length];
                    for (int i = 0; i < data.Length; i++) { data[i] = file.Content; }
                    File.WriteAllBytes(target, data);
                    return new TransferOperationResult();
                }
            }
            throw new FileNotFoundException(source);
        }
        public void Dispose() {}
    }
}
'@
foreach ($folder in $filters.fixtures) {
    $files = New-Object 'System.Collections.Generic.List[WinSCP.RemoteFileInfo]'
    foreach ($row in $folder.files) {
        $file = New-Object WinSCP.RemoteFileInfo
        $file.FullName = $folder.path + '/' + $row.name
        $file.Name = $row.name
        $file.IsDirectory = [bool]$row.directory
        $file.Length = 8
        $file.LastWriteTime = [DateTime]::Parse([string]$row.modified).ToUniversalTime()
        $file.Content = [byte]$row.content
        $files.Add($file)
    }
    [WinSCP.Session]::Fixtures[$folder.path] = $files
}
'''


def _file(name: str, *, directory=False, modified="2026-09-16T12:00:00Z", content=1):
    return {"name": name, "directory": directory, "modified": modified, "content": content}


def _folder(path: str, *files):
    return {"path": path, "files": list(files)}


def _audio(phone="990000001", day="20260916", sequence=1):
    return f"out-{phone}-1001-{day}-120000-{sequence}.1.wav"


@unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell requerido")
class WinSCPDiscoveryPerformanceTests(unittest.TestCase):
    def run_discovery(self, directory: Path, fixtures):
        script = _POWERSHELL_FILTERS + _FAKE_WINSCP + _POWERSHELL_SESSION.replace(
            "Add-Type -Path $env:SENTRY_WINSCP_DLL", ""
        ) + r'''
ConvertTo-Json @{visited=[string[]][WinSCP.Session]::Visited;
    downloads=[string[]][WinSCP.Session]::Downloads} -Compress
'''
        environment = os.environ.copy()
        environment.update({
            "SENTRY_SFTP_MODE": "match_download", "SENTRY_SFTP_HOST": "test.invalid",
            "SENTRY_SFTP_PORT": "22", "SENTRY_SFTP_USER": "test", "SENTRY_SFTP_PASSWORD": "test",
            "SENTRY_SFTP_FINGERPRINT": "test", "SENTRY_SFTP_REMOTE_PATH": "/monitor",
            "SENTRY_SFTP_PATHS": '["/monitor"]', "SENTRY_SFTP_RECURSIVE": "1",
            "SENTRY_SFTP_LIMIT": "0", "SENTRY_SFTP_QUERY": "", "SENTRY_SFTP_STREAM": "0",
            "SENTRY_SFTP_LOCAL_DIRECTORY": str(directory),
        })
        payload = {"source_system": "lucid", "audio_since": "20260901",
                   "phone_dates": [["0990000001", []]], "phones": [], "fixtures": fixtures}
        completed = subprocess.run(
            _powershell_command(shutil.which("powershell.exe"), script),
            input=json.dumps(payload), capture_output=True, text=True, env=environment,
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result, activity = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        return result, activity

    def test_recent_folders_first_preserves_complete_results_and_lucid_rules(self):
        september = _audio()
        october = _audio(day="20261001")
        custom = _audio(sequence=2)
        fixtures = [
            _folder("/monitor", _file("2026", directory=True), _file("campaign", directory=True)),
            _folder("/monitor/campaign", _file(custom)),
            _folder("/monitor/2026", *[_file(month, directory=True) for month in ("08", "09", "10")]),
            _folder("/monitor/2026/08", _file(_audio(day="20260831"))),
            _folder("/monitor/2026/09", _file(september), _file(_audio(phone="980000002")),
                    _file("q-001-0990000001-20260916-120000.wav"),
                    _file(_audio(day="20260931"))),
            _folder("/monitor/2026/10", _file(october)),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            result, activity = self.run_discovery(Path(temporary), fixtures)
            self.assertEqual({Path(path).name for path in result["paths"]}, {september, october, custom})
            self.assertEqual(result["match_count"], 3)
            self.assertEqual(result["candidate_count"], 6)
            self.assertNotIn("/monitor/2026/08", activity["visited"])
            self.assertLess(activity["visited"].index("/monitor/2026/10"),
                            activity["visited"].index("/monitor/2026/09"))

    def test_repeated_search_refreshes_listing_and_only_downloads_new_recording(self):
        first, second = _audio(), _audio(sequence=2)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self.run_discovery(directory, [_folder("/monitor", _file(first))])
            result, activity = self.run_discovery(directory, [_folder("/monitor", _file(first), _file(second))])
            self.assertEqual({Path(path).name for path in result["paths"]}, {first, second})
            self.assertEqual(activity["visited"], ["/monitor"])
            self.assertEqual(activity["downloads"], ["/monitor/" + second])

    def test_same_size_changed_recording_is_not_reused_from_download_cache(self):
        name = _audio()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result, _ = self.run_discovery(directory, [_folder("/monitor", _file(name, content=1))])
            target = Path(result["paths"][0])
            self.assertEqual(target.read_bytes(), bytes([1]) * 8)
            result, activity = self.run_discovery(directory, [_folder(
                "/monitor", _file(name, content=2, modified="2026-09-16T12:00:01Z")
            )])
            self.assertEqual(activity["downloads"], ["/monitor/" + name])
            self.assertEqual(target.read_bytes(), bytes([2]) * 8)
            self.assertFalse(list(directory.rglob("*.sentry-part")))

    def test_command_remains_under_windows_limit_and_preserves_unicode_code(self):
        self.assertLess(len(subprocess.list2cmdline(_powershell_command("powershell.exe", _POWERSHELL))), 32000)
        completed = subprocess.run(
            _powershell_command(shutil.which("powershell.exe"), "[Console]::OutputEncoding=[Text.Encoding]::UTF8; 'Teléfono'"),
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "Teléfono")


if __name__ == "__main__":
    unittest.main()
