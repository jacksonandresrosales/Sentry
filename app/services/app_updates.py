"""Actualizaciones públicas de GitHub: selección de versión e integridad, sin secretos.

La interfaz decide cuándo preguntar, respaldar los datos y cerrar Sentry. Este módulo
no modifica la base de datos ni instala nada al consultar o descargar una versión.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import total_ordering
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.about import UPDATE_REPOSITORY


REPOSITORY = UPDATE_REPOSITORY
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
MANIFEST_NAME = "sentry-update.json"
MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 20 * 60
_ALLOWED_HOSTS = frozenset({
    "api.github.com", "github.com", "release-assets.githubusercontent.com",
    "objects.githubusercontent.com", "github-releases.githubusercontent.com",
})
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]


class UpdateError(RuntimeError):
    """Error seguro para mostrar al usuario, sin URL firmadas ni credenciales."""


class UpdateCancelled(UpdateError):
    pass


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    numbers: tuple[int, int, int]
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = _SEMVER.fullmatch(str(value))
        if not match:
            raise UpdateError(f"Versión no válida: {value!s}.")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
            raise UpdateError("La versión contiene un identificador numérico no válido.")
        return cls(tuple(int(match.group(i)) for i in (1, 2, 3)), prerelease)

    def _key(self) -> tuple:
        return self.numbers, not self.prerelease, tuple(
            (0, int(part)) if part.isdigit() else (1, part) for part in self.prerelease
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self._key() == other._key()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    title: str
    notes: str
    release_url: str
    asset_name: str
    download_url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ReleaseNotesInfo:
    version: str
    title: str
    notes: str
    published_at: str


def _check_cancel(cancel: CancelCheck | None) -> None:
    if cancel and cancel():
        raise UpdateCancelled("Actualización cancelada.")


def _validate_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        valid = (parsed.scheme == "https" and parsed.hostname in _ALLOWED_HOSTS
                 and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise UpdateError("La actualización contiene una dirección de descarga no permitida.")
    return url


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _open_url(url: str, *, json_response: bool = False):
    _validate_url(url)
    headers = {"User-Agent": "Sentry-Updater", "Accept-Encoding": "identity"}
    if json_response:
        headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    else:
        headers["Accept"] = "application/octet-stream"
    try:
        return build_opener(_SafeRedirectHandler()).open(Request(url, headers=headers), timeout=30)
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("No se encontró la publicación. Las actualizaciones requieren un repositorio público y un release publicado.") from None
        if exc.code in (403, 429):
            raise UpdateError("GitHub limitó temporalmente las consultas. Inténtalo más tarde.") from None
        raise UpdateError(f"GitHub no pudo entregar la actualización (HTTP {exc.code}).") from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise UpdateError("No se pudo conectar con GitHub. Revisa la conexión e inténtalo de nuevo.") from None


def _read_json(url: str, cancel: CancelCheck | None = None):
    _check_cancel(cancel)
    try:
        with _open_url(url, json_response=True) as response:
            payload = response.read(MAX_JSON_BYTES + 1)
        _check_cancel(cancel)
        if len(payload) > MAX_JSON_BYTES:
            raise UpdateError("La respuesta de actualización excede el tamaño permitido.")
        return json.loads(payload)
    except (ValueError, UnicodeError):
        raise UpdateError("GitHub devolvió información de actualización no válida.") from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise UpdateError("Se interrumpió la consulta de actualizaciones. Inténtalo de nuevo.") from None


def _asset_url(asset: dict, tag: str, name: str) -> str:
    expected = f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"
    value = str(asset.get("browser_download_url", ""))
    if value != expected:
        raise UpdateError("El instalador no pertenece al release esperado de Sentry.")
    return _validate_url(value)


def _release_info(release: dict, cancel: CancelCheck | None = None) -> ReleaseInfo:
    tag = str(release["tag_name"])
    version = tag.removeprefix("v")
    Version.parse(version)
    asset_name = f"Sentry_Setup_{version}.exe"
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("La publicación no contiene una lista válida de archivos.")
    installers = [a for a in assets if isinstance(a, dict) and a.get("name") == asset_name]
    if len(installers) != 1:
        raise UpdateError(f"El release {version} todavía no tiene su instalador {asset_name}.")
    asset = installers[0]
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_INSTALLER_BYTES:
        raise UpdateError("El instalador tiene un tamaño no permitido.")
    download_url = _asset_url(asset, tag, asset_name)
    digest = str(asset.get("digest") or "")
    sha256 = digest[7:].lower() if digest.startswith("sha256:") else ""
    if sha256 and not _SHA256.fullmatch(sha256):
        raise UpdateError("La huella SHA-256 del instalador no es válida.")
    manifests = [a for a in assets if isinstance(a, dict) and a.get("name") == MANIFEST_NAME]
    if len(manifests) > 1:
        raise UpdateError("La publicación contiene manifiestos de actualización duplicados.")
    if manifests:
        manifest = _read_json(_asset_url(manifests[0], tag, MANIFEST_NAME), cancel)
        details = manifest.get("asset", {}) if isinstance(manifest, dict) else {}
        if (not isinstance(manifest, dict) or manifest.get("schema") != 1
                or manifest.get("version") != version or not isinstance(details, dict)
                or details.get("name") != asset_name or details.get("size") != size):
            raise UpdateError("El manifiesto no corresponde al instalador de esta versión.")
        manifest_hash = str(details.get("sha256", "")).lower()
        if not _SHA256.fullmatch(manifest_hash) or (sha256 and sha256 != manifest_hash):
            raise UpdateError("La huella del manifiesto no coincide con la publicación de GitHub.")
        sha256 = manifest_hash
    if not sha256:
        raise UpdateError("La publicación no tiene una huella SHA-256 verificable. No se instalará.")
    return ReleaseInfo(
        version=version, tag=tag, title=str(release.get("name") or version),
        notes=str(release.get("body") or "")[:20000],
        release_url=f"https://github.com/{REPOSITORY}/releases/tag/{quote(tag, safe='')}",
        asset_name=asset_name, download_url=download_url, size=size, sha256=sha256,
    )


def check_for_update(
    current_version: str, *, include_prereleases: bool | None = None,
    cancel: CancelCheck | None = None,
) -> ReleaseInfo | None:
    """Consulta releases sin token. Una instalación estable no recibe betas por defecto."""
    current = Version.parse(current_version)
    if include_prereleases is None:
        include_prereleases = bool(current.prerelease)
    candidates: list[tuple[Version, dict]] = []
    for page in range(1, 6):
        releases = _read_json(f"{RELEASES_URL}?per_page=100&page={page}", cancel)
        if not isinstance(releases, list):
            raise UpdateError("GitHub devolvió una lista de versiones no válida.")
        for release in releases:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            try:
                version = Version.parse(str(release.get("tag_name", "")))
            except UpdateError:
                continue
            if not include_prereleases and (release.get("prerelease") or version.prerelease):
                continue
            if version > current:
                candidates.append((version, release))
        if len(releases) < 100:
            break
    _check_cancel(cancel)
    if not candidates:
        return None
    return _release_info(max(candidates, key=lambda item: item[0])[1], cancel)


def parse_release_history(
    releases: object, current_version: str, *, limit: int = 20,
) -> tuple[ReleaseNotesInfo, ...]:
    current = Version.parse(current_version)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("El límite del historial debe estar entre 1 y 50.")
    if not isinstance(releases, list):
        raise UpdateError("GitHub devolvió un historial de versiones no válido.")
    history: list[tuple[Version, ReleaseNotesInfo]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name", ""))
        try:
            version = Version.parse(tag)
        except UpdateError:
            continue
        if version > current or (not current.prerelease and version.prerelease):
            continue
        body = release.get("body")
        name = release.get("name")
        published = release.get("published_at")
        history.append((version, ReleaseNotesInfo(
            version=tag.removeprefix("v"),
            title=name.strip() if isinstance(name, str) and name.strip() else f"Sentry {tag.removeprefix('v')}",
            notes=body[:20000] if isinstance(body, str) else "",
            published_at=published if isinstance(published, str) else "",
        )))
    return tuple(item for _version, item in sorted(history, key=lambda value: value[0], reverse=True)[:limit])


def _validate_release(release: ReleaseInfo) -> None:
    Version.parse(release.version)
    if release.tag not in (release.version, "v" + release.version):
        raise UpdateError("La etiqueta no corresponde a la versión del instalador.")
    if release.asset_name != f"Sentry_Setup_{release.version}.exe":
        raise UpdateError("Nombre de instalador no válido.")
    _asset_url({"browser_download_url": release.download_url}, release.tag, release.asset_name)
    if type(release.size) is not int or not 0 < release.size <= MAX_INSTALLER_BYTES:
        raise UpdateError("El instalador tiene un tamaño no permitido.")
    if not _SHA256.fullmatch(release.sha256):
        raise UpdateError("La huella SHA-256 del instalador no es válida.")


def verify_installer(path: Path, release: ReleaseInfo, *, cancel: CancelCheck | None = None) -> None:
    """Revalida bytes y huella; se utiliza también inmediatamente antes de ejecutar."""
    _validate_release(release)
    path = Path(path)
    if not path.is_file() or path.stat().st_size != release.size:
        raise UpdateError("El instalador está incompleto o su tamaño ha cambiado.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise UpdateError("El archivo descargado no es un instalador de Windows.")
        stream.seek(0)
        while chunk := stream.read(1024 * 1024):
            _check_cancel(cancel)
            digest.update(chunk)
    if digest.hexdigest() != release.sha256.lower():
        raise UpdateError("La comprobación SHA-256 falló. No se ejecutará el instalador.")


def download_update(
    release: ReleaseInfo, cache_dir: Path, *, progress: ProgressCallback | None = None,
    cancel: CancelCheck | None = None,
) -> Path:
    """Descarga atómica y cancelable; un archivo parcial nunca sustituye al verificado."""
    _validate_release(release)
    _check_cancel(cancel)
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / release.asset_name
    if destination.is_file():
        try:
            verify_installer(destination, release, cancel=cancel)
        except UpdateCancelled:
            raise
        except UpdateError:
            pass
        else:
            if progress:
                progress(release.size, release.size)
            return destination
    if shutil.disk_usage(cache_dir).free < release.size + 16 * 1024 * 1024:
        raise UpdateError("No hay espacio suficiente para descargar la actualización.")
    descriptor, temporary = tempfile.mkstemp(prefix="sentry-download-", suffix=".part", dir=cache_dir)
    temporary_path = Path(temporary)
    started = time.monotonic()
    try:
        with os.fdopen(descriptor, "wb") as output:
            with _open_url(release.download_url) as response:
                header = response.headers.get("Content-Length")
                if header is not None and (not str(header).isdigit() or int(header) != release.size):
                    raise UpdateError("El tamaño de la descarga no coincide con el release.")
                downloaded = 0
                if progress:
                    progress(0, release.size)
                while True:
                    _check_cancel(cancel)
                    if time.monotonic() - started > DOWNLOAD_TIMEOUT_SECONDS:
                        raise UpdateError("La descarga excedió el tiempo permitido. Inténtalo de nuevo.")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > release.size:
                        raise UpdateError("La descarga excede el tamaño esperado.")
                    output.write(chunk)
                    if progress:
                        progress(downloaded, release.size)
            output.flush()
            os.fsync(output.fileno())
        verify_installer(temporary_path, release, cancel=cancel)
        _check_cancel(cancel)
        temporary_path.replace(destination)
        return destination
    except (URLError, TimeoutError, OSError, HTTPException):
        raise UpdateError("No se pudo completar la descarga. Tus datos no se han modificado.") from None
    finally:
        temporary_path.unlink(missing_ok=True)


def launch_installer(
    path: Path, release: ReleaseInfo, app_dir: Path, log_path: Path, *, parent_pid: int | None = None,
    current_version: str | None = None,
) -> subprocess.Popen:
    """Solo llamar tras confirmar y respaldar. Inno espera al PID antes de reemplazar."""
    if current_version is None:
        from app.about import APP_VERSION
        current_version = APP_VERSION
    if Version.parse(release.version) <= Version.parse(current_version):
        raise UpdateError("Solo se permite instalar una versión más reciente de Sentry.")
    verify_installer(path, release)
    path = Path(path).resolve()
    app_dir = Path(app_dir).resolve()
    log_path = Path(log_path).resolve()
    if path.name != release.asset_name or not (app_dir / "Sentry.exe").is_file():
        raise UpdateError("No se encontró la instalación de Sentry que debe actualizarse.")
    if parent_pid is not None and (type(parent_pid) is not int or parent_pid <= 0):
        raise UpdateError("El proceso de Sentry no es válido.")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    arguments = [
        str(path), "/SP-", "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
        "/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/SENTRYUPDATE=1",
        f"/DIR={app_dir}", f"/LOG={log_path}",
    ]
    if parent_pid is not None:
        arguments.append(f"/UPDATEFROMPID={parent_pid}")
    try:
        return subprocess.Popen(
            arguments, shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    except OSError:
        raise UpdateError("No se pudo iniciar el instalador. Sentry y tus datos siguen intactos.") from None
