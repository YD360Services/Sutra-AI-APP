import sys
sys.path.insert(0, '.')
from app.core.config import settings
print('GEMINI_KEY:', repr(settings.GEMINI_API_KEY[:15] if settings.GEMINI_API_KEY else ''))
print('DEEPGRAM_KEY:', repr(settings.DEEPGRAM_API_KEY[:15] if settings.DEEPGRAM_API_KEY else ''))
print('GEMINI configured:', bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip()))
print('DEEPGRAM configured:', bool(settings.DEEPGRAM_API_KEY and settings.DEEPGRAM_API_KEY.strip()))
