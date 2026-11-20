from typing import Optional
from app.core.config import settings

class DeepgramService:
    @staticmethod
    def get_websocket_url(language: str = "en", model: Optional[str] = None) -> str:
        """
        Builds the ultra-low-latency multilingual Nova-3 WebSocket streaming URL.
        Supports 60+ global and Indian languages (e.g. te, hi, ta, es, fr, ja, etc.)
        Uses endpointing=10 for immediate speech turn finalization.
        """
        lang = (language or "en").strip().lower()
        if lang in ("multi", "auto"):
            lang_param = "&detect_language=true"
        elif lang in ("en", "en-us", "english"):
            lang_param = "&language=en"
        else:
            clean_code = lang.split("-")[0]
            lang_param = f"&language={clean_code}"

        model_param = model or settings.DEEPGRAM_MODEL or "nova-3"

        return (
            f"wss://api.deepgram.com/v1/listen"
            f"?endpointing=10"
            f"&interim_results=true"
            f"&smart_format=true"
            f"&model={model_param}"
            f"&encoding=linear16"
            f"&sample_rate=16000"
            f"&channels=1"
            f"{lang_param}"
        )

    @staticmethod
    def get_auth_headers() -> dict:
        key = settings.DEEPGRAM_API_KEY or "b8c9477ee765b08e755f320c169f3d6fed7b585d"
        return {
            "Authorization": f"Token {key}"
        }
