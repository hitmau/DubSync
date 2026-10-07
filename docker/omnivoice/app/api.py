from __future__ import annotations

import logging
import re
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .audio import safe_component, safe_data_path
from .jobs import JobManager
from .model_service import ModelService
from .monitor import RuntimeMonitor
from .schemas import GenerateBody, PrepareVoiceBody


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
model_service = ModelService()
jobs = JobManager(model_service)
runtime_monitor = RuntimeMonitor()


@asynccontextmanager
async def lifespan(_app):
    jobs.start()
    runtime_monitor.start()
    model_service.start_loading()
    yield
    runtime_monitor.stop()


app = FastAPI(title="DubSync OmniVoice", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8766", "http://localhost:8766"],
    allow_methods=["GET"],
    allow_headers=[],
)


@app.get("/health")
def health():
    ready = model_service.loaded.is_set()
    data = {"status": "ok" if ready else "loading" if not model_service.error else "error",
            "model_loaded": ready}
    if ready:
        return data
    raise HTTPException(503, data)


@app.get("/v1/model/status")
def model_status():
    return model_service.status()


@app.get("/v1/monitoring")
def monitoring():
    snapshot = runtime_monitor.snapshot()
    snapshot["jobs"] = jobs.snapshot()
    return snapshot


@app.post("/v1/voices/prepare")
def prepare_voice(body: PrepareVoiceBody):
    try:
        return model_service.prepare_voice(body.voice_profile_id, body.reference_path,
                                           body.reference_text, body.language)
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except Exception as error:
        raise HTTPException(503, "A preparação da voz falhou ou o modelo ainda está carregando.") from error


@app.post("/v1/tts/generate", status_code=202)
def generate(body: GenerateBody):
    payload = body.model_dump()
    try:
        payload["voice_profile_id"] = safe_component(payload["voice_profile_id"])
        payload["take_id"] = safe_component(payload["take_id"])
        safe_data_path(payload["output_relative_path"])
        return jobs.submit(payload)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str):
    if not re.fullmatch(r"JOB_[A-F0-9]{12}", job_id):
        raise HTTPException(404, "Job não encontrado.")
    item = jobs.get(job_id)
    if item is None:
        raise HTTPException(404, "Job não encontrado nesta instância.")
    return item


@app.get("/v1/audio/{take_id}")
def get_audio(take_id: str):
    if not re.fullmatch(r"TAKE_[A-F0-9]{12}", take_id):
        raise HTTPException(404, "Áudio não encontrado.")
    path = jobs.audio_for_take(take_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "Áudio não encontrado.")
    return FileResponse(path, media_type="audio/wav", filename=f"{take_id}.wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, workers=1)
