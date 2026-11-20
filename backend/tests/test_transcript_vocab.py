"""
For software roles the transcript should show the technical term the interviewer meant
("OOPS", not "hoops"); for other roles nothing is rewritten.
"""
import asyncio
import json
import urllib.parse

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.websockets as ws_mod
from app.services.deepgram_service import DeepgramService
from app.services.transcript_vocab import correct_transcript, is_software_role, keyterms


def test_software_role_detection():
    for role in ("sde", "Software Engineer", "Full Stack Developer", "Data Analyst", "QA Automation",
                 "Microsoft (Interview + Coding)"):
        assert is_software_role(role), role
    for role in ("Mechanical Engineer", "Security Guard", "Sales Manager", "HR Executive", "Architect", "", None):
        assert not is_software_role(role), role


def test_corrections_for_software_roles():
    cases = {
        "Can you explain hoops concepts?": "Can you explain OOPS concepts?",
        "What are the four pillars of oops?": "What are the four pillars of OOPS?",
        "Write a sequel query to find the second highest salary": "Write a SQL query to find the second highest salary",
        "Have you used no sequel databases like mongo db?": "Have you used NoSQL databases like MongoDB?",
        "How do you deploy on cuber netties with CI CD?": "How do you deploy on Kubernetes with CI/CD?",
        "Parse the jason response from the rest full API": "Parse the JSON response from the RESTful API",
        "Explain a sink and await in node js": "Explain async and await in Node.js",
    }
    for heard, expected in cases.items():
        assert correct_transcript(heard, True) == expected


def test_corrections_leave_ordinary_speech_alone():
    for text in ("Oops, sorry, let me repeat that.", "My friend Jason joined", "Is this a sequel to your last project?"):
        assert correct_transcript(text, True) == text
    assert correct_transcript("He shot hoops after work", False) == "He shot hoops after work"


def test_keyterms_only_for_english_nova3_software_sessions():
    url = DeepgramService.get_websocket_url("en", keyterms=keyterms(True))
    terms = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("keyterm", [])
    assert "OOPS" in terms and "Kubernetes" in terms
    assert "keyterm" not in DeepgramService.get_websocket_url("en", keyterms=keyterms(False))
    assert "keyterm" not in DeepgramService.get_websocket_url("hi", keyterms=keyterms(True))


class _DeepgramSaying:
    def __init__(self, text):
        self.sent = []
        self.closed = asyncio.Event()
        self._msgs = [json.dumps({"type": "Results", "is_final": False, "speech_final": False,
                                  "channel": {"alternatives": [{"transcript": text}]}})]

    async def send(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed.set()

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._msgs:
            return self._msgs.pop(0)
        await self.closed.wait()
        raise StopAsyncIteration


def _first_transcript(monkeypatch, software: bool) -> str:
    fake = _DeepgramSaying("Can you explain hoops concepts?")

    class _Connect:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return fake

        async def __aexit__(self, *exc):
            return False

    async def _is_software(_sid):
        return software

    monkeypatch.setattr(ws_mod.websockets, "connect", _Connect)
    monkeypatch.setattr(ws_mod, "_session_is_software", _is_software)
    app = FastAPI()
    app.include_router(ws_mod.router)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/audio/s1?provider=deepgram&language=en") as ws:
            while True:
                msg = ws.receive_json()
                if msg.get("type") == "transcript":
                    return msg["text"]


def test_proxy_shows_oops_for_software_sessions(monkeypatch):
    assert _first_transcript(monkeypatch, software=True) == "Can you explain OOPS concepts?"


def test_proxy_leaves_other_sessions_alone(monkeypatch):
    assert _first_transcript(monkeypatch, software=False) == "Can you explain hoops concepts?"
