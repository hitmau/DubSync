from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
MAX_REFERENCE_BYTES = 50 * 1024 * 1024


def data_path(root: Path, relative: str) -> Path:
    """Resolve a container/app relative path and reject traversal/absolute paths."""
    value = str(relative or "").strip()
    if not value or "\\" in value or ":" in value:
        raise ValueError("O caminho precisa ser relativo e usar barras '/'.")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError("O caminho relativo contém um segmento inválido.")
    base = root.resolve()
    result = (base / Path(*candidate.parts)).resolve()
    if not result.is_relative_to(base):
        raise ValueError("O caminho solicitado sai da pasta de dados do DubSync.")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as error:
        raise RuntimeError("FFmpeg/ffprobe não foi encontrado no PATH do DubSync.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("O processamento do áudio excedeu o tempo limite.") from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "").strip().splitlines()
        safe_detail = details[-1][:240] if details else "comando de áudio recusado"
        raise RuntimeError(f"Falha no processamento de áudio: {safe_detail}") from error


def normalize_reference(source: Path, destination: Path) -> dict:
    if not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("O arquivo de referência está vazio ou não existe.")
    if source.stat().st_size > MAX_REFERENCE_BYTES:
        raise ValueError("A referência ultrapassa o limite de 50 MiB.")
    if source.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
        raise ValueError("Use WAV, MP3, M4A, AAC, FLAC, OGG ou OPUS.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-vn", "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", "-c:a", "pcm_s16le",
        str(destination),
    ])
    probe = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
        "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", str(destination),
    ])
    info = json.loads(probe.stdout or "{}")
    streams = info.get("streams") or []
    if not streams:
        raise ValueError("O arquivo não contém uma faixa de áudio legível.")
    duration_ms = round(float((info.get("format") or {}).get("duration") or 0) * 1000)
    if duration_ms < 500 or duration_ms > 60_000:
        raise ValueError("A referência precisa ter entre 0,5 e 60 segundos; prefira 3–10 segundos.")
    stream = streams[0]
    return {
        "duration_ms": duration_ms,
        "sample_rate": int(stream.get("sample_rate") or 24000),
        "channels": int(stream.get("channels") or 1),
        "codec": str(stream.get("codec_name") or "pcm_s16le"),
        "quality_status": "ready" if 3000 <= duration_ms <= 10_000 else "needs_review",
        "sha256": sha256_file(destination),
    }


def extract_video_audio(video: Path, start: float, end: float, destination: Path) -> dict:
    if not video.is_file():
        raise ValueError("O vídeo de origem desta sessão não está disponível.")
    if start < 0 or end <= start or end - start > 60:
        raise ValueError("Informe um intervalo de áudio de até 60 segundos.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
        "-i", str(video), "-t", f"{end - start:.3f}", "-map", "0:a:0", "-vn", "-ac", "1",
        "-ar", "24000", "-sample_fmt", "s16", "-c:a", "pcm_s16le", str(destination),
    ])
    return normalize_reference(destination, destination.with_name(destination.stem + "_normalized.wav"))


def wav_duration_ms(path: Path) -> int:
    probe = _run([
        "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
        "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return round(float(probe.stdout.strip()) * 1000)


def apply_voice_effect(audio_bytes: bytes, effect: str | None) -> bytes:
    """Apply an optional local finishing effect to a generated WAV."""
    selected = str(effect or "none").strip().lower()
    if selected == "none":
        return audio_bytes
    filters = {
        # Mild saturation and a focused midrange suggest vocal rasp without
        # altering the clean, official reference stored on the profile.
        "raspy": "highpass=f=90,lowpass=f=7200,equalizer=f=1800:t=q:w=1:g=2,asoftclip=threshold=0.72:oversample=4",
        # Downsampled texture plus amplitude modulation gives a deliberately
        # synthetic, game-radio character.
        "robotic": "highpass=f=150,lowpass=f=4200,acrusher=bits=10:mode=log:aa=1:mix=0.28,tremolo=f=26:d=0.68",
    }
    audio_filter = filters.get(selected)
    if not audio_filter:
        raise ValueError("O acabamento vocal selecionado não é válido.")
    if not audio_bytes:
        raise ValueError("O áudio a processar está vazio.")
    with tempfile.TemporaryDirectory(prefix="dubsync-voice-effect-") as temp_dir:
        source = Path(temp_dir) / "source.wav"
        destination = Path(temp_dir) / "finished.wav"
        source.write_bytes(audio_bytes)
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vn", "-af", audio_filter, "-ac", "1", "-ar", "24000",
            "-c:a", "pcm_s16le", str(destination),
        ])
        result = destination.read_bytes()
        if len(result) <= 44:
            raise RuntimeError("O acabamento vocal produziu um áudio vazio.")
        return result
