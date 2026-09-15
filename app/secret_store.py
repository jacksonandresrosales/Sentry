"""Protección de credenciales con DPAPI: solo el usuario actual de Windows puede leerlas."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os


class SecretStoreError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def protect(secret: str) -> str:
    if not secret:
        return ""
    if os.name != "nt":
        raise SecretStoreError("El guardado seguro de claves requiere Windows.")
    source, source_buffer = _blob(secret.encode("utf-8"))
    entropy, entropy_buffer = _blob(b"Sentry-Ecuaconexion-v1")
    output = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(ctypes.byref(source), "Sentry", ctypes.byref(entropy), None, None, 0,
                                    ctypes.byref(output)):
        raise SecretStoreError("Windows no pudo proteger la clave API.")
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect(encrypted: str) -> str:
    if not encrypted:
        return ""
    if os.name != "nt":
        raise SecretStoreError("El guardado seguro de claves requiere Windows.")
    try:
        raw = base64.b64decode(encrypted, validate=True)
    except ValueError as exc:
        raise SecretStoreError("La clave guardada está dañada.") from exc
    source, source_buffer = _blob(raw)
    entropy, entropy_buffer = _blob(b"Sentry-Ecuaconexion-v1")
    output = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy),
                                                   None, None, 0, ctypes.byref(output)):
        raise SecretStoreError("Windows no pudo recuperar la clave API guardada.")
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
