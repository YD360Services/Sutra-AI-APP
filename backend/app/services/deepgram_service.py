from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote
import random
import time
from app.core.config import settings


@dataclass
class NormalizedTranscriptEvent:
    """Provider-neutral normalized ASR event (LLD v1.3 Item 19 & 21)."""
    provider: str
    raw_text: str
    confidence: float = 1.0
    is_final: bool = False
    speech_final: bool = False
    request_id: Optional[str] = None
    start_ms: Optional[float] = None
    duration_ms: Optional[float] = None
    is_gap: bool = False
    gap_reason: Optional[str] = None


class ASRReconnectManager:
    """
    LLD v1.3 Item 20: Bounded ASR reconnect backoff & discontinuity gap marker.
    Never invents missing words on disconnect; emits explicit gap markers.
    """

    def __init__(self, max_retries: int = 3, base_delay_s: float = 0.5, max_delay_s: float = 4.0):
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.attempts = 0
        self.last_disconnect_ts: Optional[float] = None

    def can_retry(self) -> bool:
        return self.attempts < self.max_retries

    def next_backoff_seconds(self) -> float:
        self.attempts += 1
        exp = min(self.max_delay_s, self.base_delay_s * (2 ** (self.attempts - 1)))
        jitter = random.uniform(0, 0.25 * exp)
        return round(exp + jitter, 3)

    def reset(self) -> None:
        self.attempts = 0
        self.last_disconnect_ts = None

    def record_disconnect(self) -> None:
        self.last_disconnect_ts = time.time()

    @staticmethod
    def create_gap_marker(provider: str, reason: str = "asr_disconnected") -> NormalizedTranscriptEvent:
        return NormalizedTranscriptEvent(
            provider=provider,
            raw_text="",
            confidence=0.0,
            is_final=True,
            speech_final=True,
            is_gap=True,
            gap_reason=reason,
        )


class DeepgramService:
    @staticmethod
    def get_websocket_url(language: str = "en", model: Optional[str] = None,
                          keyterms: Optional[List[str]] = None) -> str:
        """
        Deepgram streaming URL for the backend proxy (/ws/audio/...), which the WEB app uses.
        """
        lang = (language or "en").strip().lower()
        if lang in ("multi", "auto"):
            lang_param = "multi"
        elif lang in ("en", "en-us", "english"):
            lang_param = "en"
        else:
            lang_param = lang.split("-")[0]

        model_param = model or "nova-3"

        keyterm_params = ""
        if keyterms and lang_param == "en" and model_param.startswith("nova-3"):
            keyterm_params = "".join(f"&keyterm={quote(t)}" for t in keyterms[:100])

        return (
            "wss://api.deepgram.com/v1/listen"
            f"?model={model_param}"
            f"&language={lang_param}"
            "&smart_format=true"
            "&interim_results=true"
            "&punctuate=true"
            "&endpointing=300"
            "&utterance_end_ms=1500"
            "&vad_events=true"
            f"{keyterm_params}"
        )

    @staticmethod
    def get_auth_headers() -> dict:
        return {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}"
        }

    @staticmethod
    def normalize_event(data: Dict[str, Any]) -> Optional[NormalizedTranscriptEvent]:
        """Normalizes a Deepgram JSON payload into a provider-neutral NormalizedTranscriptEvent."""
        if not isinstance(data, dict):
            return None

        channel_data = data.get("channel")
        if not isinstance(channel_data, dict):
            channels_list = data.get("channels")
            if isinstance(channels_list, list) and channels_list and isinstance(channels_list[0], dict):
                channel_data = channels_list[0]
            elif isinstance(channel_data, list) and channel_data and isinstance(channel_data[0], dict):
                channel_data = channel_data[0]
            else:
                return None

        alternatives = channel_data.get("alternatives", [])
        if not isinstance(alternatives, list) or not alternatives or not isinstance(alternatives[0], dict):
            return None

        alt = alternatives[0]
        transcript = str(alt.get("transcript", "")).strip()
        if not transcript:
            return None

        confidence = float(alt.get("confidence", 1.0))
        metadata = data.get("metadata") or {}
        request_id = metadata.get("request_id") if isinstance(metadata, dict) else None
        start_s = data.get("start")
        dur_s = data.get("duration")

        return NormalizedTranscriptEvent(
            provider="deepgram",
            raw_text=transcript,
            confidence=confidence,
            is_final=bool(data.get("is_final")),
            speech_final=bool(data.get("speech_final")),
            request_id=request_id,
            start_ms=(float(start_s) * 1000.0) if start_s is not None else None,
            duration_ms=(float(dur_s) * 1000.0) if dur_s is not None else None,
        )
