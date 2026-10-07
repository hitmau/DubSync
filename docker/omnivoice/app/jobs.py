from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

from .audio import safe_data_path, safe_component


log = logging.getLogger("dubsync.omnivoice.jobs")


class JobManager:
    def __init__(self, model_service):
        self.model_service = model_service
        self.pending = queue.Queue()
        self.records = {}
        self.lock = threading.RLock()
        self.worker_id = f"omnivoice-{uuid.uuid4().hex[:8]}"
        self.started = False

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def start(self):
        if self.started:
            return
        self.started = True
        threading.Thread(target=self._run, name="omnivoice-single-worker", daemon=True).start()

    def submit(self, payload: dict) -> dict:
        if not self.model_service.loaded.is_set():
            raise RuntimeError("O modelo OmniVoice ainda está carregando.")
        voice_profile_id = safe_component(payload["voice_profile_id"])
        take_id = safe_component(payload["take_id"])
        safe_data_path(payload["output_relative_path"])
        if payload.get("voice_mode") == "clone" and voice_profile_id not in self.model_service.prompts:
            raise RuntimeError("Prepare a referência vocal deste perfil antes de gerar.")
        job_id = f"JOB_{uuid.uuid4().hex[:12].upper()}"
        record = {
            "job_id": job_id, "take_id": take_id, "voice_profile_id": voice_profile_id,
            "status": "queued", "created_at": self._now(), "started_at": None,
            "finished_at": None, "result": None, "error": None,
        }
        with self.lock:
            self.records[job_id] = record
        self.pending.put((job_id, dict(payload)))
        return {"status": "queued", "job_id": job_id}

    def get(self, job_id: str):
        with self.lock:
            row = self.records.get(job_id)
            return dict(row) if row else None

    def snapshot(self):
        with self.lock:
            counts = {status: 0 for status in ("queued", "generating", "completed", "error")}
            for row in self.records.values():
                if row["status"] in counts:
                    counts[row["status"]] += 1
            return counts

    def audio_for_take(self, take_id: str):
        with self.lock:
            job = next((item for item in reversed(list(self.records.values()))
                        if item["take_id"] == take_id and item["status"] == "completed"), None)
            if not job:
                return None
            return safe_data_path(job["result"]["audio_relative_path"], must_exist=True)

    def _set(self, job_id: str, **changes):
        with self.lock:
            if job_id in self.records:
                self.records[job_id].update(changes)

    def _run(self):
        while True:
            item = self.pending.get()
            if item is None:
                self.pending.task_done()
                return
            job_id, payload = item
            self._set(job_id, status="generating", started_at=self._now())
            try:
                result = self.model_service.generate(payload)
                self._set(job_id, status="completed", finished_at=self._now(), result=result)
                log.info("TTS job %s completed for take %s.", job_id, payload["take_id"])
            except Exception as error:
                error_code = type(error).__name__
                self._set(job_id, status="error", finished_at=self._now(),
                          error="Falha na síntese. Revise a referência ou a instrução e tente novamente.")
                log.error("TTS job %s failed (%s).", job_id, error_code)
            finally:
                self.pending.task_done()
