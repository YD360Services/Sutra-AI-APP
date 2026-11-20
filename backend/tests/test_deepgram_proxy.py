"""
The web app streams audio/webm;codecs=opus through /ws/audio/{id}. Commit 1b0de43 copied the
desktop app's raw-PCM params (encoding=linear16&sample_rate=16000) into the proxy URL, so Deepgram
decoded WebM as noise and no transcript came back; it also dropped utterance_end_ms/vad_events,
which auto-answer relies on. And with no KeepAlive, any ~10s silent gap ended the stream (NET-0001).
"""
import asyncio
import json
import urllib.parse

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.websockets as ws_mod
from app.services.deepgram_service import DeepgramService


def _params(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def test_proxy_url_lets_deepgram_detect_webm_container():
    p = _params(DeepgramService.get_websocket_url("en"))
    for raw_pcm_param in ("encoding", "sample_rate", "channels"):
        assert raw_pcm_param not in p, f"{raw_pcm_param} forces raw PCM; the web app sends WebM/Opus"
    assert p["utterance_end_ms"] and p["vad_events"] == "true" and p["interim_results"] == "true"
    assert p["model"] == "nova-3" and p["language"] == "en"


def test_proxy_url_languages():
    assert _params(DeepgramService.get_websocket_url("hi-IN"))["language"] == "hi"
    multi = _params(DeepgramService.get_websocket_url("multi"))
    assert multi["language"] == "multi" and "detect_language" not in multi


class _FakeDeepgram:
    def __init__(self):
        self.sent = []
        self.closed = asyncio.Event()

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.closed.wait()
        raise StopAsyncIteration


def test_proxy_forwards_audio_and_closes_deepgram_when_browser_leaves(monkeypatch):
    fake = _FakeDeepgram()

    class _Connect:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return fake

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ws_mod.websockets, "connect", _Connect)
    app = FastAPI()
    app.include_router(ws_mod.router)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/audio/test-session?provider=deepgram&language=en") as ws:
            assert ws.receive_json()["message"] == "Listening"
            ws.send_bytes(b"\x1aE\xdf\xa3webm-bytes")
        # Leaving the `with` disconnects the browser; the proxy must end the Deepgram stream
        # (otherwise the keep-alive would hold it open forever).

    assert b"\x1aE\xdf\xa3webm-bytes" in fake.sent
    assert fake.closed.is_set()
    assert any(isinstance(m, str) and json.loads(m).get("type") == "CloseStream" for m in fake.sent)
