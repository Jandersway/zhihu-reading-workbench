"""Persist local credentials under the current Windows account using DPAPI."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


class DataBlob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.c_void_p)]


class WindowsProtectedFile:
    def __init__(self, path):
        self.path = path
        crypt = ctypes.WinDLL('Crypt32.dll', use_last_error=True)
        blob = ctypes.POINTER(DataBlob)
        options = [blob, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, blob]
        self.protect = crypt.CryptProtectData
        self.protect.argtypes = [blob, wintypes.LPCWSTR, *options]
        self.protect.restype = wintypes.BOOL
        self.unprotect = crypt.CryptUnprotectData
        self.unprotect.argtypes = [blob, ctypes.POINTER(wintypes.LPWSTR), *options]
        self.unprotect.restype = wintypes.BOOL
        self.free = ctypes.WinDLL('Kernel32.dll').LocalFree
        self.free.argtypes = [ctypes.c_void_p]
        self.free.restype = ctypes.c_void_p

    def _transform(self, transform, payload):
        buffer = ctypes.create_string_buffer(payload)
        source = DataBlob(len(payload), ctypes.cast(buffer, ctypes.c_void_p))
        destination = DataBlob()
        try:
            # CRYPTPROTECT_UI_FORBIDDEN. The default scope is the current Windows user.
            if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
                raise ctypes.WinError(ctypes.get_last_error())
            return ctypes.string_at(destination.data, destination.size)
        finally:
            ctypes.memset(buffer, 0, len(buffer))
            if destination.data:
                ctypes.memset(destination.data, 0, destination.size)
                self.free(destination.data)

    def read(self):
        try:
            encrypted = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        return json.loads(self._transform(self.unprotect, encrypted))

    def write(self, data):
        payload = self._transform(self.protect, json.dumps(data, ensure_ascii=False).encode('utf-8'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with NamedTemporaryFile(dir=self.path.parent, prefix='credential-', suffix='.tmp', delete=False) as handle:
                temp = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(self.path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
