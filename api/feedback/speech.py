"""Spoken live-session tips through ElevenLabs text-to-speech."""

from urllib.parse import quote

import httpx
from config import Settings

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class SpeechError(RuntimeError):
    """ElevenLabs could not read the text aloud. The message never contains the API key."""


class SpeechClient:
    def __init__(self, settings: Settings, timeout_s: float = 20.0):
        self._api_key = settings.elevenlabs_api_key.get_secret_value() if settings.elevenlabs_api_key else None
        self.voice_id = settings.elevenlabs_voice_id
        self.model = settings.elevenlabs_model
        self.timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self.voice_id)

    def _mask(self, message: str) -> str:
        return message.replace(self._api_key, "***") if self._api_key else message

    def synthesize(self, text: str) -> bytes:
        """MP3 audio of the text. Raises SpeechError on any failure."""
        if not self.configured:
            raise SpeechError("ElevenLabs is not configured.")
        try:
            response = httpx.post(
                ELEVENLABS_TTS_URL.format(voice_id=quote(self.voice_id or "", safe="")),
                headers={"xi-api-key": self._api_key or "", "Accept": "audio/mpeg"},
                json={"text": text, "model_id": self.model},
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as exc:
            raise SpeechError(self._mask(f"{type(exc).__name__}: {exc}")[:300]) from None
        if response.status_code != 200:
            raise SpeechError(f"ElevenLabs returned HTTP {response.status_code}: {self._mask(response.text)[:200]}")
        if not response.content:
            raise SpeechError("ElevenLabs returned no audio.")
        return response.content
