from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .omnivoice import OmniVoiceError, OmniVoiceProvider


class OmniVoiceDocker:
    def __init__(self, project_root: Path, settings_provider,
                 project_data_root_provider: Callable[[], Path] | None = None):
        self.project_root = project_root.resolve()
        self.compose_dir = self.project_root / "docker" / "omnivoice"
        self.compose_file = self.compose_dir / "docker-compose.yml"
        self.settings_provider = settings_provider
        self.project_data_root_provider = project_data_root_provider
        self.data_root = self.project_root / ".data"
        self.process = None
        self.lock = threading.Lock()

    def _build_log(self) -> Path:
        project_data = self.project_data_root_provider() if self.project_data_root_provider else (
            self.data_root / "projects" / "replaced" / "dubbing")
        return project_data / "logs" / "omnivoice-compose.log"

    def _docker_executable(self) -> str | None:
        return shutil.which("docker")

    def _run(self, action: str) -> dict:
        docker = self._docker_executable()
        if not docker:
            raise OmniVoiceError("Docker CLI não está instalado ou não está no PATH.", None, "docker_missing")
        if action not in {"up", "stop", "restart"}:
            raise ValueError("Ação Docker inválida.")
        self.compose_dir.mkdir(parents=True, exist_ok=True)
        command = [docker, "compose", "-f", str(self.compose_file)]
        command += ["up", "-d", "--build"] if action == "up" else [action, "omnivoice"]
        with self.lock:
            if self.process and self.process.poll() is None:
                return {"started": True, "state": "starting", "message": "Comando Docker já em execução."}
            build_log = self._build_log()
            build_log.parent.mkdir(parents=True, exist_ok=True)
            stream = build_log.open("ab")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            environment = os.environ.copy()
            environment["DUBSYNC_SHARED_DATA_DIR"] = self.data_root.resolve().as_posix()
            self.process = subprocess.Popen(
                command, cwd=self.compose_dir, stdout=stream, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=flags, env=environment,
            )
        threading.Thread(target=self._watch_process, args=(self.process, stream),
                         name="dubsync-compose-control", daemon=True).start()
        return {"started": True, "state": "starting", "message": "Comando Docker iniciado em segundo plano."}

    @staticmethod
    def _watch_process(process, stream):
        try:
            process.wait()
        finally:
            stream.close()

    def start(self) -> dict:
        return self._run("up")

    def stop(self) -> dict:
        return self._run("stop")

    def restart(self) -> dict:
        return self._run("restart")

    def status(self) -> dict:
        docker_available = bool(self._docker_executable())
        build_running = bool(self.process and self.process.poll() is None)
        settings = self.settings_provider().get("dubbing", {})
        provider = OmniVoiceProvider(settings.get("base_url", "http://127.0.0.1:8015"),
                                     settings.get("timeout_seconds", 600))
        try:
            model = provider.model_status()
            return {"docker_available": docker_available, "api_online": True,
                    "state": model.get("status", "unknown"), "model_loaded": bool(model.get("model_loaded")),
                    "model_name": model.get("model_name"), "model_revision": model.get("model_revision"),
                    "provider_version": model.get("provider_version"), "device": model.get("device"),
                    "error": model.get("error"), "build_running": build_running,
                    "base_url": settings.get("base_url", "http://127.0.0.1:8015")}
        except OmniVoiceError as error:
            return {"docker_available": docker_available, "api_online": False,
                    "state": "starting" if build_running else "offline", "model_loaded": False,
                    "build_running": build_running, "message": str(error),
                    "base_url": settings.get("base_url", "http://127.0.0.1:8015")}
