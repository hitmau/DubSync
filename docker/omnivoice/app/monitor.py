from __future__ import annotations

import os
import platform
import threading
import time
from collections import deque
from datetime import datetime, timezone

import psutil


def _read_int(path: str) -> int | None:
    try:
        value = open(path, encoding="ascii").read().strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


class RuntimeMonitor:
    """Low-cost container metrics and a short in-memory history for the UI."""

    def __init__(self, interval_seconds: float = 2.0, history_size: int = 120):
        self.interval_seconds = max(1.0, interval_seconds)
        self.history: deque[dict] = deque(maxlen=history_size)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._processes: dict[int, psutil.Process] = {}
        self._cpu_by_pid: dict[int, float] = {}
        self._available_cpus = max(1, len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (psutil.cpu_count() or 1))
        self._started_at = time.monotonic()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._sample_loop, name="omnivoice-runtime-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _memory(self) -> tuple[int, int]:
        used = _read_int("/sys/fs/cgroup/memory.current")
        limit = _read_int("/sys/fs/cgroup/memory.max")
        if used is None:
            used = _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
            limit = _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        if used is None:
            used = psutil.Process().memory_info().rss
        if limit is None or limit > (1 << 60):
            limit = psutil.virtual_memory().total
        return max(0, used), max(1, limit)

    def _sample(self) -> dict:
        processes = []
        total_cpu_raw = 0.0
        active: dict[int, psutil.Process] = {}
        cpu_by_pid: dict[int, float] = {}
        for process in psutil.process_iter():
            try:
                cpu = max(0.0, process.cpu_percent(interval=None))
                info = process.as_dict(attrs=["pid", "ppid", "name", "status", "cmdline", "memory_info", "num_threads"])
                pid = int(info["pid"])
                active[pid] = process
                cpu_by_pid[pid] = cpu
                total_cpu_raw += cpu
                cmdline = info.get("cmdline") or []
                command = " ".join(cmdline[:3]).strip() or str(info.get("name") or "process")
                processes.append({
                    "pid": pid,
                    "ppid": int(info.get("ppid") or 0),
                    "name": str(info.get("name") or "process"),
                    "command": command[:120],
                    "status": str(info.get("status") or "unknown"),
                    "cpu_percent": round(cpu / self._available_cpus, 2),
                    "cpu_cores": round(cpu / 100, 3),
                    "rss_bytes": int(info["memory_info"].rss) if info.get("memory_info") else 0,
                    "threads": int(info.get("num_threads") or 0),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue

        memory_used, memory_limit = self._memory()
        sample = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cpu_percent": round(min(100.0, total_cpu_raw / self._available_cpus), 2),
            "cpu_cores_used": round(total_cpu_raw / 100, 3),
            "cpu_available_cores": self._available_cpus,
            "memory_used_bytes": memory_used,
            "memory_limit_bytes": memory_limit,
            "memory_percent": round(min(100.0, memory_used / memory_limit * 100), 2),
            "uptime_seconds": round(time.monotonic() - self._started_at),
            "container_id": os.environ.get("HOSTNAME", "unknown")[:24],
            "platform": platform.platform(terse=True),
            "process_count": len(processes),
            "processes": sorted(processes, key=lambda item: (-item["cpu_percent"], item["pid"])),
        }
        with self._lock:
            self._processes = active
            self._cpu_by_pid = cpu_by_pid
            self.history.append(sample)
        return sample

    def _sample_loop(self) -> None:
        self._sample()
        while not self._stop.wait(self.interval_seconds):
            self._sample()

    def snapshot(self) -> dict:
        with self._lock:
            history = list(self.history)
        current = history[-1] if history else self._sample()
        return {"container_state": "running", "history": history, "current": current}
