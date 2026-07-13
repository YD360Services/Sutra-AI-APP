from app.core.config import settings

class DeepgramService:
    @staticmethod
    def get_websocket_url() -> str:
        return (
            f"wss://api.deepgram.com/v1/listen"
            f"?model={settings.DEEPGRAM_MODEL}"
            f"&language=en-US"
            f"&smart_format=true"
            f"&interim_results=true"
            f"&punctuate=true"
            f"&endpointing=200"
        )

    @staticmethod
    def get_auth_headers() -> dict:
        return {
            "Authorization": f"Token {settings.DEEPGRAM_API_KEY}"
        }
