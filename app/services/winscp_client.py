"""Conexión SFTP mediante la biblioteca .NET instalada con WinSCP."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from app.services.base_conversion import LUCID_AUDIO_SINCE


MATCH_DOWNLOAD_TIMEOUT_SECONDS = 600


class WinSCPError(RuntimeError):
    pass


def winscp_dll() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    candidates = (
        bundle_root / "WinSCP" / "WinSCPnet.dll",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "WinSCP" / "WinSCPnet.dll",
        Path(os.environ.get("ProgramFiles", "")) / "WinSCP" / "WinSCPnet.dll",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "WinSCP" / "WinSCPnet.dll",
    )
    found = next((path for path in candidates if path.is_file()), None)
    if found is None:
        raise WinSCPError("WinSCP no está instalado o no incluye WinSCPnet.dll.")
    return found


def _validated(config: dict, require_authentication: bool, require_fingerprint: bool) -> dict[str, str]:
    values = {key: str(config.get(key, "")).strip() for key in
              ("host", "port", "username", "password", "remote_path", "fingerprint")}
    if not values["host"]:
        raise WinSCPError("Introduce la IP o el nombre del servidor.")
    if require_authentication and not values["username"]:
        raise WinSCPError("Introduce el usuario de WinSCP.")
    if require_authentication and not values["password"]:
        raise WinSCPError("Introduce la contraseña de WinSCP.")
    try:
        port = int(values["port"] or "22")
    except ValueError as exc:
        raise WinSCPError("El puerto debe ser un número entre 1 y 65535.") from exc
    if not 1 <= port <= 65535:
        raise WinSCPError("El puerto debe estar entre 1 y 65535.")
    values["port"] = str(port)
    values["remote_path"] = values["remote_path"] or "/"
    if require_fingerprint and not values["fingerprint"]:
        raise WinSCPError("Obtén y verifica primero la huella SSH del servidor.")
    return values


_POWERSHELL_FILTERS = r'''
$ErrorActionPreference = 'Stop'
# Se compila una vez por búsqueda: el filtrado de miles de nombres no ejecuta
# funciones/regex/parseos de PowerShell por cada archivo remoto.
Add-Type -TypeDefinition @'
using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Text.RegularExpressions;

public sealed class SentryAudioMatcher {
    private readonly Regex audio;
    private readonly Regex directory = new Regex(@"/(20\d{2})(?:/(0[1-9]|1[0-2]))?(?:/(0[1-9]|[12]\d|3[01]))?(?:/|$)", RegexOptions.Compiled);
    private readonly Dictionary<string, HashSet<string>> dates = new Dictionary<string, HashSet<string>>(StringComparer.Ordinal);
    private readonly HashSet<string> phones = new HashSet<string>(StringComparer.Ordinal);
    private readonly Dictionary<string, bool> validDays = new Dictionary<string, bool>(StringComparer.Ordinal);
    private readonly bool lucid;
    private readonly string since;
    public string LastMatchDay { get; private set; }

    public SentryAudioMatcher(bool isLucid, string audioSince, IDictionary phoneDates, IDictionary phoneSet) {
        lucid = isLucid;
        since = audioSince;
        audio = new Regex(lucid ? @"^out-(\d+)-\d+-(20\d{6})-" : @"^q-\d{3}-(\d+)-(20\d{6})-",
            RegexOptions.Compiled | RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        foreach (DictionaryEntry entry in phoneDates) {
            HashSet<string> days = null;
            if (entry.Value != null) {
                days = new HashSet<string>(StringComparer.Ordinal);
                foreach (object day in ((IDictionary)entry.Value).Keys) { days.Add((string)day); }
            }
            dates[(string)entry.Key] = days;
        }
        foreach (object phone in phoneSet.Keys) { phones.Add((string)phone); }
    }

    public static string NormalizePhone(string value) {
        string digits = Regex.Replace(value, @"\D", "");
        string national = digits;
        if (national.StartsWith("00593", StringComparison.Ordinal)) { national = national.Substring(5); }
        else if (national.Length > 10 && national.StartsWith("593", StringComparison.Ordinal)) { national = national.Substring(3); }
        if (national.StartsWith("0", StringComparison.Ordinal)) { national = national.Substring(1); }
        if (national.Length >= 8 && national.Length <= 9 && national[0] >= '1' && national[0] <= '9') {
            bool valid = true;
            for (int i = 1; i < national.Length; i++) { if (national[i] < '0' || national[i] > '9') { valid = false; break; } }
            if (valid) { return "0" + national; }
        }
        return digits;
    }

    public bool Matches(string name, bool matchDates) {
        LastMatchDay = null;
        if (!matchDates && phones.Count == 0) { return true; }
        Match key = audio.Match(name);
        if (!key.Success) { return false; }
        string day = key.Groups[2].Value;
        if (lucid && String.CompareOrdinal(day, since) < 0) { return false; }
        string phone = NormalizePhone(key.Groups[1].Value);
        if (!matchDates) { return phones.Contains(phone); }
        HashSet<string> days;
        if (!dates.TryGetValue(phone, out days)) { return false; }
        bool valid;
        if (!validDays.TryGetValue(day, out valid)) {
            DateTime parsed;
            valid = DateTime.TryParseExact(day, "yyyyMMdd", CultureInfo.InvariantCulture, DateTimeStyles.None, out parsed);
            validDays[day] = valid;
        }
        if (!valid || (days != null && !days.Contains(day))) { return false; }
        LastMatchDay = day;
        return true;
    }

    public bool CanSearchDirectory(string path) {
        if (!lucid) { return true; }
        Match key = directory.Match(path.TrimEnd('/'));
        if (!key.Success) { return true; }
        string lastDay = key.Groups[1].Value + (key.Groups[2].Success ? key.Groups[2].Value : "12") +
            (key.Groups[3].Success ? key.Groups[3].Value : "31");
        return String.CompareOrdinal(lastDay, since) >= 0;
    }
}
'@
function ConvertTo-SentryPhone([string]$value) {
    return [SentryAudioMatcher]::NormalizePhone($value)
}
# Los filtros viajan por stdin: 50.000 teléfonos exceden el límite del entorno de Windows.
$filters = [Console]::In.ReadToEnd() | ConvertFrom-Json
$phoneDates = @{}
foreach ($entry in $filters.phone_dates) {
    $phone = [string]$entry[0]
    # El índice de Python ya envía números normalizados; evita una llamada de
    # función PowerShell por cada fila cuando no requiere conversión adicional.
    if ($phone -notmatch '^0[1-9][0-9]{7,8}$') { $phone = ConvertTo-SentryPhone $phone }
    if ($entry[1].Count -eq 0) {
        # null representa cualquier fecha, sin construir 50.000 colecciones vacías.
        $phoneDates[$phone] = $null
        continue
    }
    if (-not $phoneDates.ContainsKey($phone)) {
        $phoneDates[$phone] = @{}
    }
    $days = $phoneDates[$phone]
    if ($null -ne $days) {
        foreach ($day in $entry[1]) { $days[[string]$day] = $true }
    }
}
$phones = @{}
foreach ($value in $filters.phones) {
    $phone = [string]$value
    if ($phone -notmatch '^0[1-9][0-9]{7,8}$') { $phone = ConvertTo-SentryPhone $phone }
    $phones[$phone] = $true
}
$audioMatcher = New-Object SentryAudioMatcher(($filters.source_system -eq 'lucid'),
    [string]$filters.audio_since, $phoneDates, $phones)
function Test-SentryAudioMatch([string]$name, [bool]$matchDates) {
    return $audioMatcher.Matches($name, $matchDates)
}
function Test-SentryDirectory([string]$path) {
    return $audioMatcher.CanSearchDirectory($path)
}
'''


_POWERSHELL_SESSION = r'''
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Send-SentryEvent($value) {
    if ($env:SENTRY_SFTP_STREAM -eq '1') {
        [Console]::Out.WriteLine((ConvertTo-Json -InputObject $value -Compress -Depth 5))
        [Console]::Out.Flush()
    }
}
Add-Type -Path $env:SENTRY_WINSCP_DLL
$options = New-Object WinSCP.SessionOptions
$options.Protocol = [WinSCP.Protocol]::Sftp
$options.HostName = $env:SENTRY_SFTP_HOST
$options.PortNumber = [int]$env:SENTRY_SFTP_PORT
$session = New-Object WinSCP.Session
try {
    if ($env:SENTRY_SFTP_MODE -eq "fingerprint") {
        $session.ScanFingerprint($options, "SHA-256")
    }
    else {
        $options.UserName = $env:SENTRY_SFTP_USER
        $options.Password = $env:SENTRY_SFTP_PASSWORD
        $options.SshHostKeyFingerprint = $env:SENTRY_SFTP_FINGERPRINT
        Send-SentryEvent @{event='progress'; phase='connecting'; candidate_count=0; match_count=0; paths=@()}
        $session.Open($options)
        if ($env:SENTRY_SFTP_MODE -eq "search" -or $env:SENTRY_SFTP_MODE -eq "match_download") {
            $needle = $env:SENTRY_SFTP_QUERY
            $matchingBase = $env:SENTRY_SFTP_MODE -eq "match_download"
            $limit = [int]$env:SENTRY_SFTP_LIMIT
            $comparison = [System.StringComparison]::OrdinalIgnoreCase
            $parsedPaths = $env:SENTRY_SFTP_PATHS | ConvertFrom-Json
            $paths = New-Object System.Collections.Generic.List[string]
            foreach ($parsedPath in $parsedPaths) { $paths.Add([string]$parsedPath) }
            if ($paths.Count -eq 0) { $paths.Add($env:SENTRY_SFTP_REMOTE_PATH) }
            $recursive = $env:SENTRY_SFTP_RECURSIVE -eq "1"
            $found = New-Object System.Collections.Generic.List[object]
            $downloaded = New-Object System.Collections.Generic.List[string]
            $localDirectory = $env:SENTRY_SFTP_LOCAL_DIRECTORY
            $progressClock = [Diagnostics.Stopwatch]::StartNew()
            $accessiblePathCount = 0
            $candidateCount = 0
            $pendingDirectories = New-Object System.Collections.Generic.Queue[string]
            $paths.Sort([StringComparer]::OrdinalIgnoreCase)
            for ($i = $paths.Count - 1; $i -ge 0; $i--) { $pendingDirectories.Enqueue($paths[$i]) }
            $visitedDirectories = @{}
            :pathLoop while ($pendingDirectories.Count -gt 0) {
                $path = $pendingDirectories.Dequeue()
                if ($visitedDirectories.ContainsKey($path)) { continue }
                $visitedDirectories[$path] = $true
                Send-SentryEvent @{event='progress'; phase='search'; candidate_count=$candidateCount;
                    match_count=$found.Count; paths=@(); directory=$path}
                try {
                    $listing = $session.ListDirectory($path)
                    $files = $listing.Files
                    $accessiblePathCount++
                }
                catch { continue }
                if (-not $audioMatcher.CanSearchDirectory($path)) { continue }
                $childDirectories = New-Object System.Collections.Generic.List[string]
                foreach ($file in $files) {
                    if ($file.IsDirectory) {
                        if ($recursive -and $file.Name -notin @('.', '..') -and
                            -not $file.IsSymbolicLink -and $audioMatcher.CanSearchDirectory($file.FullName)) {
                            $childDirectories.Add($file.FullName)
                        }
                        continue
                    }
                    $basicMatch = (
                        -not $file.IsDirectory -and
                        ($file.Name.EndsWith(".wav", $comparison) -or $file.Name.EndsWith(".mp3", $comparison)) -and
                        ([string]::IsNullOrWhiteSpace($needle) -or $file.Name.IndexOf($needle, $comparison) -ge 0)
                    )
                    if ($basicMatch) {
                        $candidateCount++
                        if ($progressClock.ElapsedMilliseconds -ge 1000) {
                            Send-SentryEvent @{event='progress'; phase='search'; candidate_count=$candidateCount;
                                match_count=$found.Count; paths=@(); directory=$path}
                            $progressClock.Restart()
                        }
                        $baseMatch = $audioMatcher.Matches($file.Name, $matchingBase)
                        if (-not $baseMatch) { continue }
                        $found.Add([PSCustomObject]@{
                            path = $file.FullName
                            name = $file.Name
                            size = [long]$file.Length
                            modified = $file.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
                        })
                        if ($matchingBase) {
                            $targetDirectory = if ($audioMatcher.LastMatchDay) {
                                Join-Path $localDirectory $audioMatcher.LastMatchDay
                            } else { $localDirectory }
                            [System.IO.Directory]::CreateDirectory($targetDirectory) | Out-Null
                            $target = Join-Path $targetDirectory $file.Name
                            $cached = if (Test-Path -LiteralPath $target) { Get-Item -LiteralPath $target } else { $null }
                            if ($null -eq $cached -or $cached.Length -ne $file.Length -or
                                $cached.LastWriteTimeUtc -ne $file.LastWriteTime.ToUniversalTime()) {
                                $partial = $target + '.' + [guid]::NewGuid().ToString('N') + '.sentry-part'
                                try {
                                    $transfer = $session.GetFiles(
                                        [WinSCP.RemotePath]::EscapeFileMask([string]$file.FullName), $partial, $false)
                                    $transfer.Check()
                                    Move-Item -LiteralPath $partial -Destination $target -Force
                                    [IO.File]::SetLastWriteTimeUtc($target, $file.LastWriteTime.ToUniversalTime())
                                } finally {
                                    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
                                }
                            }
                            $downloaded.Add($target)
                            Send-SentryEvent @{event='progress'; phase='download'; candidate_count=$candidateCount;
                                match_count=$found.Count; paths=@($target); directory=$path}
                            $progressClock.Restart()
                        }
                        if ($limit -gt 0 -and $found.Count -ge $limit) { break pathLoop }
                    }
                }
                # Las carpetas fechadas recientes se revisan antes, pero ninguna
                # carpeta válida se omite ni se reutilizan listados obsoletos.
                $childDirectories.Sort([StringComparer]::OrdinalIgnoreCase)
                for ($i = $childDirectories.Count - 1; $i -ge 0; $i--) {
                    $pendingDirectories.Enqueue($childDirectories[$i])
                }
            }
            if ($accessiblePathCount -eq 0 -and $paths.Count -gt 0) {
                throw "No se pudo acceder a las carpetas de grabaciones en Issabel. Revisa la carpeta remota configurada."
            }
            if ($env:SENTRY_SFTP_MODE -eq "match_download") {
                ConvertTo-Json -InputObject ([PSCustomObject]@{
                    event = 'result'
                    paths = [string[]]$downloaded
                    candidate_count = $candidateCount
                    match_count = $found.Count
                }) -Compress -Depth 3
            }
            else {
                $resultArray = [object[]]$found
                ConvertTo-Json -InputObject $resultArray -Compress
            }
        }
        else {
            $session.ListDirectory($env:SENTRY_SFTP_REMOTE_PATH) | Out-Null
            "CONEXION_OK"
        }
    }
}
finally {
    $session.Dispose()
}
'''

_POWERSHELL = _POWERSHELL_FILTERS + _POWERSHELL_SESSION


def _powershell_command(powershell: str, script: str) -> list[str]:
    """Pasa código fijo directamente, sin la expansión 8/3 de EncodedCommand.

    subprocess escapa un único argumento Unicode; nunca se interpola aquí una
    ruta remota, filtro, usuario o contraseña proporcionados por el cliente.
    """
    return [powershell, "-NoProfile", "-NonInteractive", "-Command", script]


def _stop_process(process: subprocess.Popen) -> None:
    """Finaliza únicamente nuestro PowerShell y su hijo WinSCP, sin ventanas."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def _run_streaming(command, *, input: str, env: dict, timeout: float,
                   progress_callback: Callable[[dict], None] | None,
                   should_cancel: Callable[[], bool] | None) -> subprocess.CompletedProcess:
    """Lee progreso sin bloquear cancelación ni esperar el cierre de stdout."""
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    messages: queue.Queue = queue.Queue()
    errors: deque[str] = deque(maxlen=30)

    def read_output():
        try:
            for line in process.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    def read_errors():
        for line in process.stderr:
            errors.append(line)

    def send_input():
        try:
            process.stdin.write(input)
            process.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    readers = [threading.Thread(target=target, daemon=True)
               for target in (read_output, read_errors, send_input)]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    paths: dict[str, None] = {}
    pending: list[str] = []
    latest = {"phase": "connecting", "candidate_count": 0, "match_count": 0}
    last_emit = 0.0
    emitted_files = False
    result = None
    canceled = False

    def flush(force=False):
        nonlocal last_emit, emitted_files
        now = time.monotonic()
        if progress_callback and (force or now - last_emit >= .3 or (pending and not emitted_files)):
            progress_callback({**latest, "paths": list(pending)})
            emitted_files = emitted_files or bool(pending)
            pending.clear()
            last_emit = now

    try:
        while True:
            if should_cancel and should_cancel():
                canceled = True
                _stop_process(process)
                break
            if time.monotonic() - started > timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                line = messages.get(timeout=.1)
            except queue.Empty:
                flush()
                continue
            if line is None:
                break
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise WinSCPError("WinSCP devolvió un mensaje de progreso no válido.") from exc
            if not isinstance(payload, dict):
                raise WinSCPError("WinSCP devolvió un mensaje de progreso no válido.")
            if payload.get("event") == "progress":
                latest = {key: payload[key] for key in
                          ("phase", "candidate_count", "match_count", "directory") if key in payload}
                for path in payload.get("paths") or []:
                    if isinstance(path, str) and path not in paths and Path(path).is_file():
                        paths[path] = None
                        pending.append(path)
                flush()
            elif payload.get("event") == "result" or "paths" in payload:
                result = payload
        flush(force=True)
        if canceled:
            result = {**latest, "paths": list(paths), "canceled": True}
        while process.poll() is None:
            if should_cancel and should_cancel():
                _stop_process(process)
                result = {**latest, "paths": list(paths), "canceled": True}
                canceled = True
                break
            if time.monotonic() - started > timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(.05)
        readers[1].join(timeout=1)
        returncode = 0 if canceled else process.returncode
        if not returncode and result is None:
            raise WinSCPError("WinSCP terminó sin confirmar el resultado de la búsqueda.")
        return subprocess.CompletedProcess(command, returncode, json.dumps(result or {}), "".join(errors))
    finally:
        _stop_process(process)
        for reader in readers:
            reader.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream and not stream.closed:
                stream.close()


def _run(mode: str, config: dict, *, progress_callback=None, should_cancel=None) -> str:
    values = _validated(
        config,
        require_authentication=mode in {"test", "search", "match_download"},
        require_fingerprint=mode in {"test", "search", "match_download"},
    )
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        raise WinSCPError("No se encontró Windows PowerShell para ejecutar WinSCP.")
    environment = os.environ.copy()
    environment.update({
        "SENTRY_WINSCP_DLL": str(winscp_dll()),
        "SENTRY_SFTP_MODE": mode,
        "SENTRY_SFTP_HOST": values["host"],
        "SENTRY_SFTP_PORT": values["port"],
        "SENTRY_SFTP_USER": values["username"],
        "SENTRY_SFTP_PASSWORD": values["password"],
        "SENTRY_SFTP_REMOTE_PATH": values["remote_path"],
        "SENTRY_SFTP_FINGERPRINT": values["fingerprint"],
        "SENTRY_SFTP_QUERY": str(config.get("query", "")).strip(),
        "SENTRY_SFTP_LIMIT": str(config.get("limit", "250")),
        "SENTRY_SFTP_PATHS": json.dumps(config.get("remote_paths") or [values["remote_path"]]),
        "SENTRY_SFTP_RECURSIVE": "1" if config.get("recursive", True) else "0",
        "SENTRY_SFTP_LOCAL_DIRECTORY": str(config.get("local_directory", "")),
        "SENTRY_SFTP_STREAM": "1" if progress_callback or should_cancel else "0",
    })
    filters = json.dumps({
        "phones": list(config.get("phones") or []),
        "phone_dates": list((config.get("phone_dates") or {}).items()),
        "source_system": str(config.get("source_system", "issabel")),
        "audio_since": str(config.get("audio_since") or LUCID_AUDIO_SINCE),
    })
    try:
        script = _POWERSHELL if mode in {"search", "match_download"} else _POWERSHELL_SESSION
        command = _powershell_command(powershell, script)
        seconds = MATCH_DOWNLOAD_TIMEOUT_SECONDS if mode == "match_download" else 120 if mode == "search" else 45
        if progress_callback or should_cancel:
            completed = _run_streaming(command, input=filters, env=environment, timeout=seconds,
                                       progress_callback=progress_callback, should_cancel=should_cancel)
        else:
            completed = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment,
                input=filters, timeout=seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    except subprocess.TimeoutExpired as exc:
        raise WinSCPError(f"La operación tardó más de {seconds} segundos y fue cancelada.") from exc
    except OSError as exc:
        raise WinSCPError("Windows no pudo iniciar el componente seguro de WinSCP.") from exc
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        for secret in (values["password"], values["username"]):
            if secret:
                detail = detail.replace(secret, "***")
        raise WinSCPError(detail.splitlines()[-1] if detail else "WinSCP no pudo conectar con el servidor.")
    return completed.stdout.strip()


def scan_host_fingerprint(config: dict) -> str:
    output = _run("fingerprint", config)
    fingerprint = ""
    for raw_line in reversed(output.splitlines()):
        line = raw_line.strip()
        parts = line.split()
        if "SHA256:" in line or (
            len(parts) >= 3
            and (parts[0].startswith("ssh-") or parts[0].startswith("ecdsa-"))
            and parts[1].isdigit()
        ):
            fingerprint = line
            break
    if not fingerprint:
        raise WinSCPError("WinSCP no devolvió una huella SSH compatible.")
    return fingerprint


def test_connection(config: dict) -> None:
    output = _run("test", config)
    if "CONEXION_OK" not in output:
        raise WinSCPError("WinSCP no confirmó la conexión ni la carpeta remota.")


def search_remote_audio(config: dict, query: str = "", limit: int = 250) -> list[dict]:
    """Busca WAV/MP3 en las rutas y con la profundidad indicadas por el flujo activo."""
    request = dict(config)
    request["query"] = query.strip()
    request["limit"] = max(1, min(5000, int(limit)))
    output = _run("search", request)
    if not output:
        return []
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise WinSCPError("WinSCP devolvió una lista de archivos no válida.") from exc
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise WinSCPError("WinSCP devolvió una lista de archivos no válida.")
    return [item for item in payload if isinstance(item, dict) and item.get("path")]


def match_and_download_remote_audio(
    config: dict,
    phone_dates: dict[str, frozenset[str]],
    local_directory: Path,
    limit: int = 0,
    *,
    progress_callback: Callable[[dict], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Descarga coincidencias; fechas vacías buscan cualquier día y límite 0 incluye todas."""
    destination = Path(local_directory).resolve()
    request = dict(config)
    request["phone_dates"] = {
        str(phone): sorted(str(day) for day in dates)
        for phone, dates in phone_dates.items()
    }
    request["local_directory"] = str(destination)
    request["limit"] = max(0, int(limit))
    output = _run("match_download", request, progress_callback=progress_callback, should_cancel=should_cancel)
    try:
        payload = json.loads(output) if output else {}
    except json.JSONDecodeError as exc:
        raise WinSCPError("WinSCP no confirmó la búsqueda y descarga de los audios.") from exc
    if not isinstance(payload, dict):
        raise WinSCPError("WinSCP devolvió un resultado de búsqueda no válido.")
    raw_paths = payload.get("paths") or []
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    paths = [Path(value).resolve() for value in raw_paths if isinstance(value, str)]
    if any(not path.is_file() for path in paths):
        raise WinSCPError("Uno o más audios no quedaron disponibles localmente.")
    return {
        "paths": paths,
        "candidate_count": int(payload.get("candidate_count", 0) or 0),
        "match_count": int(payload.get("match_count", len(paths)) or 0),
        "canceled": bool(payload.get("canceled", False)),
    }
