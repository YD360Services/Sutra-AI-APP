from typing import Optional
from app.core.config import settings

class DeepgramService:
    @staticmethod
    def get_websocket_url(language: str = "en", model: Optional[str] = None) -> str:
        lang = (language or "en").strip().lower()
        if lang in ("en", "en-us", "english"):
            lang_param = "en-US"
            model_param = model or settings.DEEPGRAM_MODEL or "nova-2"
        else:
            # Standardize language code (e.g. "hi-in" -> "hi", "te-in" -> "te")
            lang_param = lang.split("-")[0]
            # Nova-3 has native support for 60+ global languages including Hindi, Telugu, Tamil, etc.
            model_param = model or "nova-3"

        return (
            f"wss://api.deepgram.com/v1/listen"
            f"?model={model_param}"
            f"&language={lang_param}"
            f"&smart_format=true"
            f"&interim_results=true"
            f"&punctuate=true"
            f"&endpointing=300"
            f"&utterance_end_ms=1500"
            f"&vad_events=true"
        )

    @staticmethod
    def get_auth_headers() -> dict:
        return {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}"
        }
