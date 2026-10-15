from __future__ import annotations

import ctypes
import math
import os
import threading
import time
from ctypes import wintypes


class _CounterValueData(ctypes.Union):
    _fields_ = [
        ("long_value", wintypes.LONG),
        ("double_value", ctypes.c_double),
        ("large_value", ctypes.c_longlong),
        ("ansi_string_value", ctypes.c_char_p),
        ("wide_string_value", wintypes.LPWSTR),
    ]


class _FormattedCounterValue(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", _CounterValueData)]


class _FormattedCounterItem(ctypes.Structure):
    _fields_ = [("instance_name", wintypes.LPWSTR), ("formatted_value", _FormattedCounterValue)]


class WindowsGpuUsageSampler:
    """Reads the Windows per-process GPU Engine counters without spawning a process."""

    _COUNTER_PATH = r"\GPU Engine(*)\Utilization Percentage"
    _PDH_MORE_DATA = 0x800007D2
    _PDH_FMT_DOUBLE = 0x00000200

    def __init__(self, process_id: int, refresh_seconds: float = 1.0) -> None:
        self.process_id = int(process_id)
        self.refresh_seconds = max(0.25, float(refresh_seconds))
        self._lock = threading.Lock()
        self._last_read = 0.0
        self._last_value: float | None = None
        self._pdh = None
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        if os.name != "nt":
            return
        try:
            self._pdh = ctypes.WinDLL("pdh", use_last_error=True)
            self._configure_api()
            if self._pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query)) != 0:
                self._pdh = None
                return
            status = self._pdh.PdhAddEnglishCounterW(
                self._query, self._COUNTER_PATH, 0, ctypes.byref(self._counter),
            )
            if status != 0:
                self.close()
                return
            # PDH rate counters need an initial sample before they can report a value.
            self._pdh.PdhCollectQueryData(self._query)
        except (AttributeError, OSError, TypeError):
            self.close()

    def _configure_api(self) -> None:
        self._pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t,
                                            ctypes.POINTER(ctypes.c_void_p)]
        self._pdh.PdhOpenQueryW.restype = wintypes.LONG
        self._pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        self._pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
        self._pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self._pdh.PdhCollectQueryData.restype = wintypes.LONG
        self._pdh.PdhGetFormattedCounterArrayW.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_FormattedCounterItem),
        ]
        self._pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
        self._pdh.PdhCloseQuery.argtypes = [ctypes.c_void_p]
        self._pdh.PdhCloseQuery.restype = wintypes.LONG

    @staticmethod
    def aggregate_usage(process_id: int, samples: list[tuple[str, float]]) -> float:
        prefix = f"pid_{int(process_id)}_"
        total = 0.0
        for name, raw_value in samples:
            if not name.casefold().startswith(prefix):
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                total += value
        return max(0.0, min(100.0, total))

    def read(self) -> float | None:
        with self._lock:
            if not self._pdh or not self._query or not self._counter:
                return None
            now = time.monotonic()
            if now - self._last_read < self.refresh_seconds:
                return self._last_value
            self._last_read = now
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                self._last_value = None
                return None

            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter, self._PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None,
            )
            if status & 0xFFFFFFFF != self._PDH_MORE_DATA or size.value <= 0:
                self._last_value = None
                return None
            buffer = ctypes.create_string_buffer(size.value)
            status = self._pdh.PdhGetFormattedCounterArrayW(
                self._counter, self._PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count),
                ctypes.cast(buffer, ctypes.POINTER(_FormattedCounterItem)),
            )
            if status != 0:
                self._last_value = None
                return None
            items = ctypes.cast(buffer, ctypes.POINTER(_FormattedCounterItem))
            samples = []
            for index in range(count.value):
                item = items[index]
                if item.formatted_value.status in (0, 1) and item.instance_name:
                    samples.append((item.instance_name, item.formatted_value.value.double_value))
            self._last_value = self.aggregate_usage(self.process_id, samples)
            return self._last_value

    def close(self) -> None:
        with self._lock:
            if self._pdh and self._query:
                try:
                    self._pdh.PdhCloseQuery(self._query)
                except (AttributeError, OSError):
                    pass
            self._query = ctypes.c_void_p()
            self._counter = ctypes.c_void_p()
            self._pdh = None

