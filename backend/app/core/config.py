from pydantic_settings import BaseSettings
from typing import List, Union
import json
import os
import logging

logger = logging.getLogger("roundmate.config")

class Settings(BaseSettings):
    # App Settings
    PROJECT_NAME: str = "Roundmate AI Backend"
    API_V1_STR: str = "/api"
    
    # Security
    JWT_SECRET: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    
    # CORS Origins (Explicit domains allowed)
    CORS_ORIGINS: Union[str, List[str]] = [
        "https://roundmateai.com",
        "https://www.roundmateai.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000"
    ]
    
    # External APIs
    GEMINI_API_KEY: str = ""
    DEEPGRAM_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    SPEECHMATICS_API_KEY: str = ""
    USE_SPEECHMATICS: bool = False
    
    # PayU Payment Gateway
    PAYU_KEY: str = ""
    PAYU_SALT: str = ""
    
    # Standard Production Real Model Identifiers
    GEMINI_MODEL: str = "gemini-3.8-flash-lite-tts"
    DEEPGRAM_MODEL: str = "nova-2"
    OPENAI_MODEL: str = "gpt-5.6-luna"
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    XAI_API_KEY: str = ""
    XAI_MODEL: str = "grok-3"
    
    # Admin & Security
    INITIAL_ADMIN_EMAILS: str = "kirankumar82054@gmail.com,omkarvenkat09@gmail.com,y.bhanuchandar360@gmail.com,omkarshendre999@gmail.com,admin@roundmate.ai"

    # Database
    DATABASE_URL: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore unknown fields

    def __init__(self, **values):
        super().__init__(**values)
        
        # Check OS environment variables first
        env = os.environ
        if not self.JWT_SECRET:
            self.JWT_SECRET = env.get("JWT_SECRET") or env.get("SECRET_KEY") or "roundmate-jwt-secret-secure-key-2026-production"
        if not self.GEMINI_API_KEY:
            self.GEMINI_API_KEY = env.get("GEMINI_API_KEY") or env.get("GEMINI_KEY") or env.get("GOOGLE_API_KEY") or ""
        if not self.DEEPGRAM_API_KEY:
            self.DEEPGRAM_API_KEY = env.get("DEEPGRAM_API_KEY") or env.get("DEEPGRAM_KEY") or ""
        if not self.OPENAI_API_KEY:
            self.OPENAI_API_KEY = env.get("OPENAI_API_KEY") or env.get("OPEN_API_KEY") or env.get("OPENAI_KEY") or ""
        if not self.GROQ_API_KEY:
            self.GROQ_API_KEY = env.get("GROQ_API_KEY") or env.get("groq") or env.get("GROQ_KEY") or ""
        if not getattr(self, "XAI_API_KEY", ""):
            self.XAI_API_KEY = env.get("XAI_API_KEY") or env.get("GROK_API_KEY") or ""
        if not self.ANTHROPIC_API_KEY:
            self.ANTHROPIC_API_KEY = env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_API_KEY") or ""
        if not self.SPEECHMATICS_API_KEY:
            self.SPEECHMATICS_API_KEY = env.get("SPEECHMATICS_API_KEY") or env.get("speechmatics_key") or ""
        if not self.PAYU_KEY:
            self.PAYU_KEY = env.get("PAYU_KEY") or env.get("PAYU_MERCHANT_KEY") or ""
        if not self.PAYU_SALT:
            self.PAYU_SALT = env.get("PAYU_SALT") or env.get("PAYU_MERCHANT_SALT") or ""
                
        # Candidate .env search paths
        paths = [
            os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
            os.path.join(os.path.dirname(__file__), "..", ".env"),
            os.path.join(os.path.dirname(__file__), ".env"),
            ".env",
            "backend/.env",
            "../backend/.env",
            "../.env"
        ]
        
        for p in paths:
            if os.path.exists(p):
                for enc in ("utf-8", "utf-8-sig", "utf-16", "cp1252"):
                    try:
                        with open(p, "r", encoding=enc) as f:
                            for line in f:
                                line = line.strip()
                                if line and not line.startswith("#") and "=" in line:
                                    parts = line.split("=", 1)
                                    k = parts[0].strip()
                                    v = parts[1].strip().strip("'\"")
                                    if not v:
                                        continue
                                    if k in ("JWT_SECRET", "SECRET_KEY") and (not self.JWT_SECRET or self.JWT_SECRET.startswith("roundmate-jwt")):
                                        self.JWT_SECRET = v
                                    elif k in ("GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY") and not self.GEMINI_API_KEY:
                                        self.GEMINI_API_KEY = v
                                    elif k in ("DEEPGRAM_API_KEY", "DEEPGRAM_KEY") and not self.DEEPGRAM_API_KEY:
                                        self.DEEPGRAM_API_KEY = v
                                    elif k in ("OPENAI_API_KEY", "OPEN_API_KEY", "OPENAI_KEY") and not self.OPENAI_API_KEY:
                                        self.OPENAI_API_KEY = v
                                    elif k in ("GROQ_API_KEY", "groq", "GROQ_KEY") and not self.GROQ_API_KEY:
                                        self.GROQ_API_KEY = v
                                    elif k in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY") and not self.ANTHROPIC_API_KEY:
                                        self.ANTHROPIC_API_KEY = v
                                    elif k in ("SPEECHMATICS_API_KEY", "speechmatics_key") and not self.SPEECHMATICS_API_KEY:
                                        self.SPEECHMATICS_API_KEY = v
                                    elif k in ("PAYU_KEY", "PAYU_MERCHANT_KEY") and not self.PAYU_KEY:
                                        self.PAYU_KEY = v
                                    elif k in ("PAYU_SALT", "PAYU_MERCHANT_SALT") and not self.PAYU_SALT:
                                        self.PAYU_SALT = v
                                    elif k == "DATABASE_URL" and not self.DATABASE_URL:
                                        self.DATABASE_URL = v
                                    elif k == "USE_SPEECHMATICS":
                                        self.USE_SPEECHMATICS = v.lower() in ("true", "1", "yes")
                    except Exception:
                        continue

    def get_cors_origins(self) -> List[str]:
        if isinstance(self.CORS_ORIGINS, list):
            return self.CORS_ORIGINS
        try:
            return json.loads(self.CORS_ORIGINS)
        except Exception:
            return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

settings = Settings()
