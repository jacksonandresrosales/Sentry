"""Una instancia de Sentry por sesión Windows, antes de abrir la base local.

El instalador utiliza el mismo mutex durante el reemplazo de archivos. Se conserva
un handle sin tomar propiedad: su existencia es la señal compartida y Windows lo
libera incluso si el proceso termina inesperadamente.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


INSTANCE_MUTEX = "Ecuaconexion.Sentry"
ERROR_ALREADY_EXISTS = 183


class ApplicationInstanceLock:
    def __init__(self, name: str = INSTANCE_MUTEX):
        self.name = name
        self._handle = None
        self._kernel32 = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        if sys.platform != "win32":
            # El instalador distribuido es exclusivamente para Windows.
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle, self._kernel32 = handle, kernel32
        return True

    def release(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
            self._kernel32 = None
