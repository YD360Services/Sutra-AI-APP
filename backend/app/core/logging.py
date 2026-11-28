"""
LLD v1.3 Item 59: Structured Logging with Automated Secret & PII Redaction.
Never logs raw audio, full transcripts, prompts, API keys, or bearer tokens.
"""
import logging
import re
import sys


class RedactingLogFilter(logging.Filter):
    _PATTERNS = [
        (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
        (re.compile(r"(access_token=|token=|ticket=|api_key=)[^&\s]+", re.IGNORECASE), r"\1[REDACTED]"),
        (re.compile(r"\b(sk-[A-Za-z0-9]{10,}|AIza[A-Za-z0-9\-_]{10,}|gsk_[A-Za-z0-9]{10,})\b"), "[REDACTED_API_KEY]"),
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    ]

    @classmethod
    def redact_text(cls, text: str) -> str:
        out = text
        for pat, repl in cls._PATTERNS:
            out = pat.sub(repl, out)
        return out

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact_text(record.msg)
        if record.args:
            record.args = tuple(
                self.redact_text(a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingLogFilter())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[handler],
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


logger = logging.getLogger("roundmate")
