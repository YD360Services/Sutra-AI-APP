from pydantic_settings import BaseSettings
from typing import List, Union
import json

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
    GEMINI_MODEL: str = "gemini-3.5-flash"
    DEEPGRAM_MODEL: str = "nova-2"
    OPENAI_MODEL: str = "gpt-5.5"
    GROQ_MODEL: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    
    # Admin & Security
    INITIAL_ADMIN_EMAILS: str = "kirankumar82054@gmail.com,omkarvenkat09@gmail.com,y.bhanuchandar360@gmail.com"

    # Databases
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgrespassword@localhost:5432/copilotx"
    REDIS_URL: str = "redis://localhost:6379/0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"  # Ignore unknown fields like OPEN_API_KEY

    def __init__(self, **values):
        import os
        super().__init__(**values)
        if not self.OPENAI_API_KEY:
            self.OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEN_API_KEY") or ""
        if not self.GROQ_API_KEY:
            self.GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or os.environ.get("groq") or ""
        if not self.ANTHROPIC_API_KEY:
            self.ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or ""
            
        if not self.OPENAI_API_KEY or not self.GROQ_API_KEY:
            try:
                # Look in same folder or parent directories for .env
                paths = [
                    os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
                    os.path.join(os.path.dirname(__file__), "..", ".env"),
                    ".env"
                ]
                for p in paths:
                    if os.path.exists(p):
                        # Try both utf-8 and utf-16/cp1252
                        for enc in ("utf-8", "utf-16", "cp1252"):
                            try:
                                with open(p, "r", encoding=enc) as f:
                                    for line in f:
                                        if "=" in line:
                                            parts = line.strip().split("=", 1)
                                            if len(parts) == 2:
                                                k, v = parts[0].strip(), parts[1].strip().strip("'\"")
                                                if k in ("OPEN_API_KEY", "OPENAI_API_KEY") and not self.OPENAI_API_KEY:
                                                    self.OPENAI_API_KEY = v
                                                if k in ("groq", "GROQ_API_KEY") and not self.GROQ_API_KEY:
                                                    self.GROQ_API_KEY = v
                                                if k == "ANTHROPIC_API_KEY" and not self.ANTHROPIC_API_KEY:
                                                    self.ANTHROPIC_API_KEY = v
                                                if k in ("SPEECHMATICS_API_KEY", "speechmatics_key") and (not self.SPEECHMATICS_API_KEY or self.SPEECHMATICS_API_KEY == "8Pi1PZqclJLK3TVXcESDI4qO6I9SC8OI"):
                                                    self.SPEECHMATICS_API_KEY = v
                            except Exception:
                                continue
            except Exception:
                pass

    def get_cors_origins(self) -> List[str]:
        if isinstance(self.CORS_ORIGINS, list):
            return self.CORS_ORIGINS
        try:
            return json.loads(self.CORS_ORIGINS)
        except Exception:
            return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

settings = Settings()
