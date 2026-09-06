from pydantic_settings import BaseSettings
from typing import List, Union
import json
import os

class Settings(BaseSettings):
    # App Settings
    PROJECT_NAME: str = "CopilotX Backend"
    API_V1_STR: str = "/api"
    
    # Security
    JWT_SECRET: str = "supersecretkeychangeinproduction1234567890!"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    
    # CORS Origins (JSON list or comma separated)
    CORS_ORIGINS: Union[str, List[str]] = ["*"]
    
    # External APIs
    GEMINI_API_KEY: str = ""
    DEEPGRAM_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    SPEECHMATICS_API_KEY: str = "8Pi1PZqclJLK3TVXcESDI4qO6I9SC8OI"
    
    # Standard Production Real Model Identifiers
    GEMINI_MODEL: str = "gemini-3.6-flash"
    DEEPGRAM_MODEL: str = "nova-2"
    OPENAI_MODEL: str = "gpt-5.4-mini"
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    
    # Admin & Security
    INITIAL_ADMIN_EMAILS: str = "kirankumar82054@gmail.com,omkarvenkat09@gmail.com,y.bhanuchandar360@gmail.com,omkarshendre999@gmail.com,admin@roundmate.ai"

    # Databases
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgrespassword@localhost:5432/copilotx"
    REDIS_URL: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore unknown fields

    def __init__(self, **values):
        super().__init__(**values)
        
        # Check OS environment variables first
        env = os.environ
        if not self.GEMINI_API_KEY:
            self.GEMINI_API_KEY = env.get("GEMINI_API_KEY") or env.get("GEMINI_KEY") or env.get("GOOGLE_API_KEY") or ""
        if not self.DEEPGRAM_API_KEY:
            self.DEEPGRAM_API_KEY = env.get("DEEPGRAM_API_KEY") or env.get("DEEPGRAM_KEY") or ""
        if not self.OPENAI_API_KEY:
            self.OPENAI_API_KEY = env.get("OPENAI_API_KEY") or env.get("OPEN_API_KEY") or env.get("OPENAI_KEY") or ""
        if not self.GROQ_API_KEY:
            self.GROQ_API_KEY = env.get("GROQ_API_KEY") or env.get("groq") or env.get("GROQ_KEY") or ""
        if not self.ANTHROPIC_API_KEY:
            self.ANTHROPIC_API_KEY = env.get("ANTHROPIC_API_KEY") or env.get("CLAUDE_API_KEY") or ""
        if not self.SPEECHMATICS_API_KEY or self.SPEECHMATICS_API_KEY == "8Pi1PZqclJLK3TVXcESDI4qO6I9SC8OI":
            sm = env.get("SPEECHMATICS_API_KEY") or env.get("speechmatics_key")
            if sm:
                self.SPEECHMATICS_API_KEY = sm
                
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
                                    if k in ("GEMINI_API_KEY", "GEMINI_KEY", "GOOGLE_API_KEY") and not self.GEMINI_API_KEY:
                                        self.GEMINI_API_KEY = v
                                    elif k in ("DEEPGRAM_API_KEY", "DEEPGRAM_KEY") and not self.DEEPGRAM_API_KEY:
                                        self.DEEPGRAM_API_KEY = v
                                    elif k in ("OPENAI_API_KEY", "OPEN_API_KEY", "OPENAI_KEY") and not self.OPENAI_API_KEY:
                                        self.OPENAI_API_KEY = v
                                    elif k in ("GROQ_API_KEY", "groq", "GROQ_KEY") and not self.GROQ_API_KEY:
                                        self.GROQ_API_KEY = v
                                    elif k in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY") and not self.ANTHROPIC_API_KEY:
                                        self.ANTHROPIC_API_KEY = v
                                    elif k in ("SPEECHMATICS_API_KEY", "speechmatics_key") and (not self.SPEECHMATICS_API_KEY or self.SPEECHMATICS_API_KEY == "8Pi1PZqclJLK3TVXcESDI4qO6I9SC8OI"):
                                        self.SPEECHMATICS_API_KEY = v
                                    elif k == "DATABASE_URL" and ("localhost" in self.DATABASE_URL or not self.DATABASE_URL):
                                        self.DATABASE_URL = v
                                    elif k == "REDIS_URL" and ("localhost" in self.REDIS_URL or not self.REDIS_URL):
                                        self.REDIS_URL = v
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
