from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..app.settings import load_settings
from ..providers.omnivoice import OmniVoiceError, OmniVoiceProvider
from ..projects.context import reset_project_context, set_project_context
from .audio import wav_duration_ms


class DubbingWorker:
    """Persistent SQLite queue with one local worker and bounded retries."""

    def __init__(self, service):
        self.service = service
        self.thread = None
        self.stop_event = threading.Event()
        self.paused = False
        self.pause_lock = threading.Lock()
        self.worker_id = f"dub-worker-{os.getpid()}"
        self.log_lock = threading.Lock()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.service.interrupt_jobs()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="dubsync-tts-worker", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def pause(self):
        with self.pause_lock:
            self.paused = True

    def resume(self):
        with self.pause_lock:
            self.paused = False

    def is_paused(self):
        with self.pause_lock:
            return self.paused

    def _log(self, **record):
        log_path = self.service.project_data_root / "logs" / "jobs.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"at": datetime.now(timezone.utc).isoformat(), **record}
        with self.log_lock, log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _run(self):
        while not self.stop_event.is_set():
            if self.is_paused():
                self.stop_event.wait(0.25)
                continue
            if not self.service.has_queued_jobs():
                self.stop_event.wait(0.7)
                continue
            settings = load_settings()["dubbing"]
            provider = OmniVoiceProvider(settings["base_url"], settings["timeout_seconds"])
            try:
                if not provider.model_status().get("model_loaded"):
                    self.stop_event.wait(2)
                    continue
            except OmniVoiceError:
                self.stop_event.wait(2)
                continue
            with self.pause_lock:
                if self.paused or self.stop_event.is_set():
                    continue
                context = self.service.next_queued_job(self.worker_id)
            if not context:
                self.stop_event.wait(0.7)
                continue
            token = set_project_context(context.get("project_id") or "replaced")
            try:
                self._process(context)
            finally:
                reset_project_context(token)

    def _process(self, context: dict):
        job, take, line, profile, reference = (
            context["job"], context["take"], context["line"], context["profile"], context["reference"],
        )
        settings = load_settings()["dubbing"]
        provider = OmniVoiceProvider(settings["base_url"], settings["timeout_seconds"])
        try:
            status = provider.model_status()
            if not status.get("model_loaded"):
                raise OmniVoiceError("O modelo OmniVoice ainda está carregando.", 503, "model_loading")
            if not profile or not line:
                raise RuntimeError("Dados do job incompletos.")
            container_reference_path = None
            if profile["voice_mode"] == "clone":
                if not reference or not context.get("reference_path"):
                    raise RuntimeError("Referência vocal não encontrada.")
                ref_path = Path(context["reference_path"]).resolve()
                container_reference_path = ref_path.relative_to(self.service.data_root.resolve()).as_posix()
                provider.prepare_voice(profile["voice_profile_id"], container_reference_path,
                                       reference.get("reference_text"), profile["language"])
            project_relative = self.service.project_data_root.resolve().relative_to(
                self.service.data_root.resolve()).as_posix()
            output_relative = f"{project_relative}/temp/omnivoice/{take['take_id']}.wav"
            response = provider.generate({
                "take_id": take["take_id"], "voice_profile_id": profile["voice_profile_id"],
                "voice_mode": profile["voice_mode"], "text": take["text_used"],
                "language": (line["dub_language"] or "pt").split("-")[0].lower(),
                "speed": take["speed_used"], "instruct": take["instruct_used"],
                "generation_settings": take.get("generation_settings_json") or {},
                "output_relative_path": output_relative,
            })
            provider_job_id = response["job_id"]
            self.service.set_provider_job(job["job_id"], provider_job_id)
            deadline = time.monotonic() + max(60, int(settings["timeout_seconds"]))
            provider_job = None
            while time.monotonic() < deadline and not self.stop_event.wait(2):
                provider_job = provider.job(provider_job_id)
                if provider_job.get("status") in {"completed", "error"}:
                    break
            else:
                raise OmniVoiceError("Tempo limite da geração OmniVoice excedido.", None, "provider_timeout")
            if provider_job.get("status") != "completed":
                raise OmniVoiceError("A geração OmniVoice falhou; confira a referência e tente novamente.",
                                     None, "generation_failed")
            audio_bytes = provider.audio(take["take_id"])
            final_path = self.service.project_audio_path(take["occurrence_id"], take["take_id"])
            final_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = final_path.with_suffix(".wav.tmp")
            with temporary.open("wb") as stream:
                stream.write(audio_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            duration = wav_duration_ms(temporary)
            if duration <= 0:
                raise RuntimeError("O take retornado está vazio.")
            os.replace(temporary, final_path)
            temp_output = self.service.project_data_root / "temp" / "omnivoice" / f"{take['take_id']}.wav"
            try:
                temp_output.unlink(missing_ok=True)
            except OSError:
                pass
            result = self.service.complete_job(job["job_id"], final_path, duration)
            self._log(job_id=job["job_id"], take_id=take["take_id"], status="completed",
                      duration_ms=duration, provider=status.get("provider_version"), model=status.get("model_name"))
            logging.getLogger(__name__).info("Take %s pronto para revisão (%s ms).", take["take_id"], duration)
        except Exception as error:
            message = str(error)[:600] if isinstance(error, OmniVoiceError) else "Falha na geração; consulte o estado do serviço e tente novamente."
            result = self.service.fail_job(job["job_id"], message, retry=True)
            self._log(job_id=job["job_id"], take_id=take["take_id"], status=result["status"],
                      attempt=result.get("attempt"), max_attempts=result.get("max_attempts"),
                      error_type=type(error).__name__)
            if result["status"] == "queued":
                self.stop_event.wait(min(2 * int(result["attempt"]), 6))
