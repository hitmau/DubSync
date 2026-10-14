from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class _FixedFileInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "dwSignature", "dwStrucVersion", "dwFileVersionMS", "dwFileVersionLS",
        "dwProductVersionMS", "dwProductVersionLS", "dwFileFlagsMask", "dwFileFlags",
        "dwFileOS", "dwFileType", "dwFileSubtype", "dwFileDateMS", "dwFileDateLS",
    )]


def read_file_versions(path: str | Path | None) -> dict[str, str | None]:
    """Lê os rótulos FileVersion/ProductVersion do PE sem iniciar o executável."""
    if os.name != "nt" or not path:
        return {"file_version": None, "product_version": None}
    try:
        target = str(Path(path))
        version_api = ctypes.WinDLL("version", use_last_error=True)
        size_fn = version_api.GetFileVersionInfoSizeW
        size_fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        size_fn.restype = wintypes.DWORD
        ignored = wintypes.DWORD()
        size = size_fn(target, ctypes.byref(ignored))
        if not size:
            return {"file_version": None, "product_version": None}
        buffer = ctypes.create_string_buffer(size)
        get_fn = version_api.GetFileVersionInfoW
        get_fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        get_fn.restype = wintypes.BOOL
        if not get_fn(target, 0, size, buffer):
            return {"file_version": None, "product_version": None}
        query_fn = version_api.VerQueryValueW
        query_fn.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR,
                             ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        query_fn.restype = wintypes.BOOL
        def query(sub_block: str):
            value = ctypes.c_void_p()
            length = wintypes.UINT()
            if not query_fn(buffer, sub_block, ctypes.byref(value), ctypes.byref(length)) or not value.value:
                return None
            return value, length.value

        translations = query("\\VarFileInfo\\Translation")
        language_codes = []
        if translations and translations[1] >= 4:
            pair = ctypes.cast(translations[0], ctypes.POINTER(wintypes.WORD * 2)).contents
            language_codes.append(f"{pair[0]:04X}{pair[1]:04X}")
        language_codes.append("040904B0")

        def read_string(key: str):
            for language_code in language_codes:
                result = query(f"\\StringFileInfo\\{language_code}\\{key}")
                if result:
                    value, length = result
                    return ctypes.wstring_at(value, max(0, length - 1))
            return None

        file_version = read_string("FileVersion")
        product_version = read_string("ProductVersion")
        fixed = query("\\")
        if not file_version and fixed and fixed[1] >= ctypes.sizeof(_FixedFileInfo):
            info = ctypes.cast(fixed[0], ctypes.POINTER(_FixedFileInfo)).contents
            if info.dwSignature == 0xFEEF04BD:
                parts = (info.dwFileVersionMS >> 16, info.dwFileVersionMS & 0xFFFF,
                         info.dwFileVersionLS >> 16, info.dwFileVersionLS & 0xFFFF)
                file_version = ".".join(str(part) for part in parts)
        return {"file_version": file_version, "product_version": product_version}
    except (AttributeError, OSError, TypeError, ValueError):
        return {"file_version": None, "product_version": None}
