"""Windows server entry point shared by the tray launcher and run.ps1."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import os
import sys
import threading
from pathlib import Path

import uvicorn


PROJECT_DIR = Path(__file__).resolve().parents[1]
INSTANCE_ID = hashlib.sha256(str(PROJECT_DIR).lower().encode("utf-8")).hexdigest()[:12]


def _windows_handles():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool,
                                    ctypes.c_wchar_p]
    kernel.CreateEventW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]

    mutex = kernel.CreateMutexW(None, True, f"Local\\DubSyncServer-{INSTANCE_ID}")
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(mutex)
        return kernel, None, None
    stop_event = kernel.CreateEventW(None, False, False,
                                      f"Local\\DubSyncStop-{INSTANCE_ID}")
    if not stop_event:
        error = ctypes.get_last_error()
        kernel.ReleaseMutex(mutex)
        kernel.CloseHandle(mutex)
        raise ctypes.WinError(error)
    return kernel, mutex, stop_event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inicia uma instância do DubSync.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("A porta deve estar entre 1 e 65535.")

    kernel = mutex = stop_event = None
    if os.name == "nt":
        kernel, mutex, stop_event = _windows_handles()
        if mutex is None:
            print("DubSync já está em execução. Abra o ícone na bandeja do Windows.",
                  file=sys.stderr)
            return 3

    server = uvicorn.Server(uvicorn.Config("Dupsync.app.main:app", host=args.host,
                                          port=args.port, access_log=False))
    if stop_event:
        def await_stop():
            kernel.WaitForSingleObject(stop_event, 0xFFFFFFFF)
            server.should_exit = True

        threading.Thread(target=await_stop, name="DubSync launcher stop",
                         daemon=True).start()
    try:
        server.run()
        return 0 if server.started else 1
    finally:
        if mutex:
            kernel.ReleaseMutex(mutex)
            kernel.CloseHandle(mutex)
        if stop_event:
            kernel.CloseHandle(stop_event)


if __name__ == "__main__":
    raise SystemExit(main())
