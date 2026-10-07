from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .file_source import ffmpeg_executable


def validate_youtube_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    if host == "youtu.be":
        video_id = path_parts[0] if path_parts else ""
    elif path_parts and path_parts[0] in {"shorts", "embed", "live", "v"}:
        video_id = path_parts[1] if len(path_parts) > 1 else ""
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    if parsed.scheme != "https" or not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
        raise ValueError("Informe uma URL HTTPS de youtube.com ou youtu.be.")
    if not video_id or not all(c.isascii() and (c.isalnum() or c in "_-") for c in video_id):
        raise ValueError("A URL precisa identificar um vídeo do YouTube.")
    return video_id


def _library_filename(title: str, video_id: str, suffix: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title).strip(" ._")
    safe = re.sub(r"\s+", " ", safe)[:120].rstrip(" ._") or "video"
    return f"{safe} [{video_id}]{suffix}"


def download_best_video(url: str, destination: Path, progress_callback, stop_requested) -> dict:
    """Download the highest-quality video-only stream; audio and subtitle streams are never requested."""
    validate_youtube_url(url)
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError("yt-dlp não está instalado. Execute setup.ps1 para instalar o downloader.") from exc

    destination.mkdir(parents=True, exist_ok=True)

    def progress_hook(data: dict) -> None:
        if stop_requested.is_set():
            raise InterruptedError("Download cancelado pelo usuário.")
        progress_callback(data)

    options = {
        "format": "bestvideo",
        "noplaylist": True,
        "ignoreconfig": True,
        "quiet": True,
        "no_warnings": False,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 4,
        "continuedl": True,
        "paths": {"home": str(destination), "temp": str(destination / ".tmp")},
        "outtmpl": {"default": str(destination / "%(id)s.%(ext)s")},
        "progress_hooks": [progress_hook],
        "ffmpeg_location": ffmpeg_executable(),
        "js_runtimes": {"node": {}},
        "overwrites": False,
    }

    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        if not info:
            raise RuntimeError("yt-dlp não retornou informações do vídeo.")
        requested = info.get("requested_downloads") or []
        if any(item.get("acodec") not in (None, "none") for item in requested):
            raise RuntimeError("O formato selecionado contém áudio; o DubSync exige uma fonte somente de vídeo.")
        video_path = next((Path(item["filepath"]) for item in requested
                           if item.get("filepath") and Path(item["filepath"]).is_file()), None)
        if video_path is None:
            candidates = [Path(info.get("filepath", "")), Path(downloader.prepare_filename(info))]
            video_path = next((path.resolve() for path in candidates if path.is_file()), None)
        if video_path is None:
            raise RuntimeError("Download terminou sem deixar um arquivo de vídeo legível.")

    probe = subprocess.run(
        [shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", str(video_path)],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    metadata = json.loads(probe.stdout)
    video_streams = [stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in metadata.get("streams", []) if stream.get("codec_type") == "audio"]
    if not video_streams or audio_streams:
        raise RuntimeError("Validação do arquivo falhou: era esperado vídeo e zero faixas de áudio.")
    stream = video_streams[0]
    title = info.get("title", video_path.stem)
    friendly_path = video_path.with_name(_library_filename(title, info.get("id") or video_path.stem,
                                                           video_path.suffix))
    if friendly_path != video_path:
        if friendly_path.exists():
            raise RuntimeError(f"Já existe um arquivo na biblioteca para este vídeo: {friendly_path}")
        os.replace(video_path, friendly_path)
        video_path = friendly_path
    progress_callback({"status": "finished", "filename": str(video_path), "total_bytes": video_path.stat().st_size})
    return {"path": str(video_path.resolve()), "title": title,
            "video_id": info.get("id"), "duration": float(info.get("duration") or metadata.get("format", {}).get("duration") or 0),
            "format_id": (requested[0].get("format_id") if requested else info.get("format_id")),
            "video_codec": stream.get("codec_name"), "width": stream.get("width"), "height": stream.get("height"),
            "size_bytes": video_path.stat().st_size, "audio_streams": len(audio_streams)}
