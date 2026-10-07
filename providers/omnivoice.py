from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class OmniVoiceError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, code: str = "provider_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class OmniVoiceProvider:
    """One HTTP adapter for the project-isolated local OmniVoice service."""

    def __init__(self, base_url: str = "http://127.0.0.1:8015", timeout: int = 20):
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1, min(1800, int(timeout)))

    def _request(self, method: str, path: str, payload: dict | None = None,
                 timeout: int | None = None, binary: bool = False):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Accept": "application/octet-stream" if binary else "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                data = response.read()
                if binary:
                    return data
                return json.loads(data.decode("utf-8") or "{}")
        except HTTPError as error:
            try:
                detail = json.loads(error.read().decode("utf-8"))
                message = detail.get("detail", detail.get("message", "O serviço OmniVoice recusou a solicitação."))
                if isinstance(message, dict):
                    message = message.get("message") or message.get("code") or "Falha no serviço OmniVoice."
            except Exception:
                message = "O serviço OmniVoice retornou uma resposta inválida."
            raise OmniVoiceError(str(message)[:600], error.code) from error
        except (URLError, TimeoutError, OSError) as error:
            raise OmniVoiceError(
                "OmniVoice offline ou inacessível em 127.0.0.1:8015. Inicie o serviço Docker local.",
                None, "provider_offline",
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OmniVoiceError("A resposta do OmniVoice não é JSON válido.", None, "invalid_response") from error

    def health(self) -> dict:
        return self._request("GET", "/health", timeout=min(self.timeout, 5))

    def model_status(self) -> dict:
        return self._request("GET", "/v1/model/status", timeout=min(self.timeout, 10))

    def prepare_voice(self, voice_profile_id: str, reference_path: str,
                      reference_text: str | None, language: str) -> dict:
        return self._request("POST", "/v1/voices/prepare", {
            "voice_profile_id": voice_profile_id, "reference_path": reference_path,
            "reference_text": reference_text, "language": language,
        }, timeout=self.timeout)

    def generate(self, payload: dict) -> dict:
        return self._request("POST", "/v1/tts/generate", payload, timeout=self.timeout)

    def job(self, job_id: str, timeout: int = 10) -> dict:
        return self._request("GET", f"/v1/jobs/{quote(job_id, safe='')}", timeout=timeout)

    def audio(self, take_id: str, timeout: int = 120) -> bytes:
        return self._request("GET", f"/v1/audio/{quote(take_id, safe='')}", timeout=timeout, binary=True)
