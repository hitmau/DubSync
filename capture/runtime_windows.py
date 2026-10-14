from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

import psutil
import numpy as np


def _user32():
    if os.name != "nt":
        return None
    return ctypes.windll.user32


def _ensure_dpi_awareness(user32) -> None:
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def client_window(hwnd: int) -> dict | None:
    """Return the client-area rectangle in physical screen pixels."""
    user32 = _user32()
    if not user32 or not user32.IsWindow(wintypes.HWND(hwnd)):
        return None
    _ensure_dpi_awareness(user32)
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None
    point = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(point)):
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    width, height = int(rect.right - rect.left), int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None
    title_len = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    title = ctypes.create_unicode_buffer(max(2, title_len + 1))
    if title_len:
        user32.GetWindowTextW(wintypes.HWND(hwnd), title, len(title))
    try:
        process_name = psutil.Process(pid.value).name()
    except psutil.Error:
        process_name = ""
    foreground = user32.GetForegroundWindow()
    foreground_pid = wintypes.DWORD()
    if foreground:
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
    return {"hwnd": int(hwnd), "pid": int(pid.value), "title": title.value,
            "process_name": process_name, "left": int(point.x), "top": int(point.y),
            "width": width, "height": height, "foreground": foreground_pid.value == pid.value}


def visible_game_windows(excluded_pids: set[int] | None = None) -> list[dict]:
    user32 = _user32()
    if not user32:
        return []
    _ensure_dpi_awareness(user32)
    excluded = set(excluded_pids or ())
    windows: list[dict] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _param):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value in excluded:
            return True
        title_len = user32.GetWindowTextLengthW(hwnd)
        if title_len <= 0:
            return True
        title = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, title, title_len + 1)
        if not title.value.strip() or "dubsync" in title.value.casefold():
            return True
        window = client_window(int(hwnd))
        if window and window["width"] >= 240 and window["height"] >= 160:
            windows.append(window)
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    windows.sort(key=lambda item: (bool(item.get("foreground")), item["width"] * item["height"]), reverse=True)
    # Keep only one top-level game window per PID, preferring the largest visible window.
    seen: set[int] = set()
    unique = []
    for item in windows:
        if item["pid"] in seen:
            continue
        seen.add(item["pid"])
        unique.append(item)
    return unique


def capture_window_client(hwnd: int) -> np.ndarray | None:
    """Ask the game window to paint its client area, bypassing the ROI overlay."""
    user32 = _user32()
    if not user32:
        return None
    window = client_window(hwnd)
    if not window:
        return None
    width, height = int(window["width"]), int(window["height"])
    gdi32 = ctypes.windll.gdi32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    user32.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    hdc = user32.GetDC(wintypes.HWND(hwnd))
    if not hdc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(hdc)
    bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
    if not memory_dc or not bitmap:
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(wintypes.HWND(hwnd), hdc)
        return None
    previous = gdi32.SelectObject(memory_dc, bitmap)

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]

    result = None
    try:
        painted = bool(user32.PrintWindow(wintypes.HWND(hwnd), memory_dc, 1))
        if painted:
            info = BitmapInfo()
            info.bmiHeader = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height,
                                              1, 32, 0, width * height * 4, 0, 0, 0, 0)
            buffer = ctypes.create_string_buffer(width * height * 4)
            rows = gdi32.GetDIBits(memory_dc, bitmap, 0, height, ctypes.byref(buffer),
                                   ctypes.byref(info), 0)
            if rows == height:
                image = np.frombuffer(buffer.raw, dtype=np.uint8).reshape(height, width, 4)[:, :, :3].copy()
                # Many DirectX games return a blank bitmap to PrintWindow; allow the caller to fall back to screen capture.
                if float(image.std()) > 2.0 or float(image.mean()) > 5.0:
                    result = image
    except (OSError, ValueError, ctypes.ArgumentError):
        result = None
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(wintypes.HWND(hwnd), hdc)
    return result
