from typing import Optional
from app.core.config import settings


class DeepgramService:
    @staticmethod
    def get_websocket_url(language: str = "en", model: Optional[str] = None) -> str:
        """
        Deepgram streaming URL for the backend proxy (/ws/audio/...), which the WEB app uses.

        The web app streams MediaRecorder output (audio/webm;codecs=opus), a containerised
        format Deepgram detects on its own. Do NOT add encoding/sample_rate/channels here:
        those tell Deepgram to treat the bytes as raw PCM, so WebM is decoded as noise and no
        transcript ever comes back. (The desktop app sends raw linear16, but it connects to
        Deepgram directly from main.js, not through this proxy.)
        """
        lang = (language or "en").strip().lower()
        if lang in ("multi", "auto"):
            # Streaming code-switching uses language=multi (Nova-3); detect_language is batch-only.
            lang_param = "multi"
        elif lang in ("en", "en-us", "english"):
            lang_param = "en"
        else:
            lang_param = lang.split("-")[0]

        model_param = model or "nova-3"

        return (
            "wss://api.deepgram.com/v1/listen"
            f"?model={model_param}"
            f"&language={lang_param}"
            "&smart_format=true"
            "&interim_results=true"
            "&punctuate=true"
            # 10ms finalised mid-sentence on every tiny pause; 300ms is still fast but keeps phrases whole.
            "&endpointing=300"
            # The web app's auto-answer listens for UtteranceEnd; it needs these two.
            "&utterance_end_ms=1500"
            "&vad_events=true"
        )

    @staticmethod
    def get_auth_headers() -> dict:
        return {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}"
        }
