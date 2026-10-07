from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import threading
from pathlib import Path

import soundfile as sf

from .audio import DATA_ROOT, safe_component, safe_data_path


log = logging.getLogger("dubsync.omnivoice")


class ModelService:
    def __init__(self):
        self.model = None
        self.device = "unloaded"
        self.error = None
        self.loaded = threading.Event()
        self.lock = threading.RLock()
        self.prompts = {}
        self.model_id = os.environ.get("OMNIVOICE_MODEL_ID", "k2-fsa/OmniVoice")
        self.revision = os.environ.get("OMNIVOICE_MODEL_REVISION", "main")
        self.package_version = "unknown"

    def start_loading(self):
        threading.Thread(target=self._load, name="omnivoice-model-loader", daemon=True).start()

    def _load(self):
        try:
            import torch
            from omnivoice import OmniVoice

            requested = os.environ.get("DUBSYNC_DEVICE", "auto").lower()
            if requested == "auto":
                if torch.cuda.is_available():
                    requested = "cuda:0"
                elif hasattr(torch, "xpu") and torch.xpu.is_available():
                    requested = "xpu"
                else:
                    requested = "cpu"
            if requested.startswith("cuda") or requested == "xpu":
                dtype = torch.float16
            else:
                dtype = torch.float32
                threads = int(os.environ.get("OMP_NUM_THREADS", "8"))
                torch.set_num_threads(max(1, min(32, threads)))
            kwargs = {"device_map": requested, "dtype": dtype, "revision": self.revision}
            model = OmniVoice.from_pretrained(self.model_id, **kwargs)
            model.eval()
            self.model = model
            self.device = requested
            self.package_version = importlib.metadata.version("omnivoice")
            self.loaded.set()
            log.info("OmniVoice loaded: package=%s device=%s", self.package_version, self.device)
        except Exception as error:
            self.error = type(error).__name__
            log.error("OmniVoice model load failed (%s).", self.error)

    def status(self):
        return {
            "status": "loaded" if self.loaded.is_set() else "error" if self.error else "loading",
            "model_loaded": self.loaded.is_set(), "model_name": self.model_id,
            "model_revision": self.revision, "provider_version": self.package_version,
            "device": self.device, "error": self.error,
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def prepare_voice(self, voice_profile_id: str, reference_path: str,
                      reference_text: str | None, language: str) -> dict:
        if not self.loaded.is_set():
            raise RuntimeError("O modelo OmniVoice ainda está carregando.")
        ref_path = safe_data_path(reference_path, must_exist=True)
        ref_digest = self._sha256(ref_path)
        text_digest = hashlib.sha256((reference_text or "").encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(f"{ref_digest}:{text_digest}:{self.revision}".encode()).hexdigest()
        profile_dir = DATA_ROOT / "voices" / safe_component(voice_profile_id) / "prompts"
        prompt_path = profile_dir / f"{cache_key}.pt"
        manifest_path = profile_dir / f"{cache_key}.json"
        with self.lock:
            if cache_key in self.prompts:
                return {"status": "ready", "voice_profile_id": voice_profile_id, "cached": True}
            if prompt_path.is_file():
                from omnivoice import VoiceClonePrompt
                prompt = VoiceClonePrompt.load(str(prompt_path), map_location="cpu")
            else:
                prompt = self.model.create_voice_clone_prompt(
                    ref_audio=str(ref_path), ref_text=reference_text or None,
                )
                profile_dir.mkdir(parents=True, exist_ok=True)
                prompt.save(str(prompt_path))
                manifest_path.write_text(json.dumps({
                    "reference_sha256": ref_digest, "reference_text_sha256": text_digest,
                    "model": self.model_id, "revision": self.revision,
                    "provider_version": self.package_version,
                }, indent=2), encoding="utf-8")
            self.prompts[voice_profile_id] = {"key": cache_key, "prompt": prompt}
        return {"status": "ready", "voice_profile_id": voice_profile_id, "cached": False}

    def generate(self, payload: dict) -> dict:
        if not self.loaded.is_set():
            raise RuntimeError("O modelo OmniVoice ainda está carregando.")
        params = {
            "text": payload["text"], "language": payload.get("language") or None,
            "speed": float(payload.get("speed", 1.0)),
        }
        instruct = str(payload.get("instruct") or "").strip()
        if instruct:
            params["instruct"] = instruct
        settings = payload.get("generation_settings") or {}
        if hasattr(settings, "model_dump"):
            settings = settings.model_dump(exclude_none=True)
        if isinstance(settings, dict):
            params.update({key: value for key, value in settings.items() if value is not None})
        if payload.get("voice_mode") == "clone":
            cached = self.prompts.get(payload["voice_profile_id"])
            if not cached:
                raise RuntimeError("O perfil de voz não foi preparado nesta instância OmniVoice.")
            params["voice_clone_prompt"] = cached["prompt"]
        audio = self.model.generate(**params)[0]
        sample_rate = int(self.model.sampling_rate or 24000)
        output = safe_data_path(payload["output_relative_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output), audio, samplerate=sample_rate, subtype="PCM_16", format="WAV")
        duration_ms = round(len(audio) * 1000 / sample_rate)
        return {"actual_duration_ms": duration_ms, "sample_rate": sample_rate,
                "provider_version": self.package_version, "model_name": self.model_id,
                "model_revision": self.revision, "audio_relative_path": payload["output_relative_path"]}
