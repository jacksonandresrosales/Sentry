"""Conexión SFTP mediante la biblioteca .NET instalada con WinSCP."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


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


_POWERSHELL = r'''
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
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
        $session.Open($options)
        if ($env:SENTRY_SFTP_MODE -eq "search" -or $env:SENTRY_SFTP_MODE -eq "match_download") {
            $needle = $env:SENTRY_SFTP_QUERY
            $phonePattern = $env:SENTRY_SFTP_PHONE_PATTERN
            $phoneDates = if ($env:SENTRY_SFTP_MODE -eq "match_download") {
                $env:SENTRY_SFTP_PHONE_DATES | ConvertFrom-Json
            } else { $null }
            $limit = [Math]::Max(1, [Math]::Min(5000, [int]$env:SENTRY_SFTP_LIMIT))
            $comparison = [System.StringComparison]::OrdinalIgnoreCase
            $parsedPaths = $env:SENTRY_SFTP_PATHS | ConvertFrom-Json
            $paths = New-Object System.Collections.Generic.List[string]
            foreach ($parsedPath in $parsedPaths) { $paths.Add([string]$parsedPath) }
            if ($paths.Count -eq 0) { $paths.Add($env:SENTRY_SFTP_REMOTE_PATH) }
            $enumeration = if ($env:SENTRY_SFTP_RECURSIVE -eq "1") {
                [WinSCP.EnumerationOptions]::AllDirectories
            } else { [WinSCP.EnumerationOptions]0 }
            $found = New-Object System.Collections.Generic.List[object]
            $accessiblePathCount = 0
            $candidateCount = 0
            :pathLoop foreach ($path in $paths) {
                try {
                    $files = $session.EnumerateRemoteFiles($path, "*", $enumeration)
                    $accessiblePathCount++
                }
                catch { continue }
                foreach ($file in $files) {
                    $phoneMatch = [string]::IsNullOrWhiteSpace($phonePattern) -or
                        [regex]::IsMatch($file.Name, "-(?:$phonePattern)-")
                    $basicMatch = (
                        -not $file.IsDirectory -and
                        ($file.Name.EndsWith(".wav", $comparison) -or $file.Name.EndsWith(".mp3", $comparison)) -and
                        $phoneMatch -and
                        ([string]::IsNullOrWhiteSpace($needle) -or $file.Name.IndexOf($needle, $comparison) -ge 0)
                    )
                    if ($basicMatch) {
                        $candidateCount++
                        $baseMatch = $true
                        if ($env:SENTRY_SFTP_MODE -eq "match_download") {
                            $audioKey = [regex]::Match($file.Name, '^q-\d{3}-(\d+)-(20\d{6})-')
                            $property = if ($audioKey.Success) {
                                $phoneDates.PSObject.Properties[$audioKey.Groups[1].Value]
                            } else { $null }
                            $allowedDates = if ($null -ne $property) { @($property.Value) } else { @() }
                            $baseMatch = $audioKey.Success -and $null -ne $property -and (
                                $allowedDates.Count -eq 0 -or $allowedDates -contains $audioKey.Groups[2].Value
                            )
                        }
                        if (-not $baseMatch) { continue }
                        $found.Add([PSCustomObject]@{
                            path = $file.FullName
                            name = $file.Name
                            size = [long]$file.Length
                            modified = $file.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
                        })
                        if ($found.Count -ge $limit) { break pathLoop }
                    }
                }
            }
            if ($accessiblePathCount -eq 0 -and $paths.Count -gt 0) {
                throw "No se pudo acceder a ninguna carpeta de fecha en Issabel. Revisa la carpeta remota configurada."
            }
            if ($env:SENTRY_SFTP_MODE -eq "match_download") {
                $localDirectory = $env:SENTRY_SFTP_LOCAL_DIRECTORY
                [System.IO.Directory]::CreateDirectory($localDirectory) | Out-Null
                $downloaded = New-Object System.Collections.Generic.List[string]
                foreach ($remoteFile in $found) {
                    $dateMatch = [regex]::Match($remoteFile.name, '^q-\d{3}-\d+-(20\d{6})-')
                    $targetDirectory = if ($dateMatch.Success) {
                        Join-Path $localDirectory $dateMatch.Groups[1].Value
                    } else { $localDirectory }
                    [System.IO.Directory]::CreateDirectory($targetDirectory) | Out-Null
                    $target = Join-Path $targetDirectory $remoteFile.name
                    if (-not (Test-Path -LiteralPath $target)) {
                        $transfer = $session.GetFiles([string]$remoteFile.path, $target, $false)
                        $transfer.Check()
                    }
                    $downloaded.Add($target)
                }
                ConvertTo-Json -InputObject ([PSCustomObject]@{
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


def _run(mode: str, config: dict) -> str:
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
        "SENTRY_SFTP_PHONE_PATTERN": "|".join(
            re.escape(str(phone)) for phone in (config.get("phones") or [])
        ),
        "SENTRY_SFTP_PHONE_DATES": json.dumps(config.get("phone_dates") or {}),
        "SENTRY_SFTP_LOCAL_DIRECTORY": str(config.get("local_directory", "")),
    })
    encoded = base64.b64encode(_POWERSHELL.encode("utf-16-le")).decode("ascii")
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=environment,
            timeout=600 if mode == "match_download" else 120 if mode == "search" else 45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        seconds = 600 if mode == "match_download" else 120 if mode == "search" else 45
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
    limit: int = 5000,
) -> dict:
    """Busca coincidencias teléfono-fecha y las descarga reutilizando una sola sesión SFTP."""
    destination = Path(local_directory).resolve()
    request = dict(config)
    request["phone_dates"] = {
        str(phone): sorted(str(day) for day in dates)
        for phone, dates in phone_dates.items()
    }
    request["phones"] = sorted(request["phone_dates"])
    request["local_directory"] = str(destination)
    request["limit"] = max(1, min(5000, int(limit)))
    output = _run("match_download", request)
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
    }
