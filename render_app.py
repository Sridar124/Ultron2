"""Small, authenticated text-chat service for Render.

This is intentionally separate from main.py: the desktop assistant imports
Windows and hardware-specific modules that do not belong in a Linux web worker.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
COOKIE_NAME = "ultron_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_HISTORY_TURNS = 10
MAX_MESSAGE_CHARS = 2_000
CHAT_RATE_LIMIT = (20, 60)
LOGIN_RATE_LIMIT = (5, 15 * 60)
_rate_lock = threading.Lock()
_rate_events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_client = None
_client_lock = threading.Lock()

app = FastAPI(title="Ultron Web", docs_url=None, redoc_url=None, openapi_url=None)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[ChatTurn] = Field(default_factory=list)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def _secret(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _session_cookie_value() -> str | None:
    secret = _secret("ULTRON_SESSION_SECRET")
    return secret or None


def _make_session() -> str:
    issued = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    payload = f"{issued}:{nonce}"
    signature = hmac.new(
        _session_cookie_value().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def _valid_session(value: str | None) -> bool:
    secret = _session_cookie_value()
    if not value or not secret:
        return False
    try:
        issued_text, nonce, signature = value.split(":", 2)
        issued = int(issued_text)
    except (ValueError, TypeError):
        return False
    now = int(time.time())
    if not nonce or issued > now or now - issued > SESSION_TTL_SECONDS:
        return False
    payload = f"{issued_text}:{nonce}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _allow_request(kind: str, request: Request, maximum: int, window: int) -> bool:
    now = time.monotonic()
    key = (kind, _client_ip(request))
    with _rate_lock:
        events = _rate_events[key]
        while events and now - events[0] >= window:
            events.popleft()
        if len(events) >= maximum:
            return False
        events.append(now)
        # Bound the in-memory limiter if the public service sees many clients.
        if len(_rate_events) > 2_000:
            expired = [k for k, q in _rate_events.items() if not q or now - q[-1] >= window]
            for old_key in expired:
                _rate_events.pop(old_key, None)
    return True


def _require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if origin and host:
        from urllib.parse import urlsplit

        if urlsplit(origin).netloc.lower() != host.lower():
            raise HTTPException(status_code=403, detail="Cross-origin request blocked.")


def _get_gemini_client():
    global _client
    with _client_lock:
        if _client is None:
            api_key = _secret("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY is not configured.")
            from google import genai

            _client = genai.Client(api_key=api_key)
    return _client


def _generate_reply(message: str, history: list[ChatTurn]) -> str:
    from google.genai import types

    contents = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        contents.append(types.Content(
            role="user" if turn.role == "user" else "model",
            parts=[types.Part(text=turn.content)],
        ))
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))

    response = _get_gemini_client().models.generate_content(
        model=_secret("GEMINI_MODEL") or "gemini-3.1-flash-lite",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are Ultron, a clear, capable, concise AI assistant. Answer the user's "
                "actual question directly. Be transparent about uncertainty and never claim "
                "to have performed actions you cannot perform. This hosted web version can "
                "chat, but it cannot control the user's local computer. Use complete, natural "
                "sentences; give more detail when the user asks."
            ),
            temperature=0.5,
            max_output_tokens=700,
        ),
    )
    answer = (getattr(response, "text", None) or "").strip()
    if not answer:
        raise RuntimeError("The AI provider returned an empty response.")
    return answer


@app.get("/health")
async def health():
    return {"ok": True, "configured": bool(_secret("GEMINI_API_KEY") and _secret("ULTRON_WEB_PASSWORD"))}


@app.get("/")
async def home():
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")


@app.get("/api/session")
async def session_status(request: Request):
    return {"authenticated": _valid_session(request.cookies.get(COOKIE_NAME))}


@app.post("/api/login")
async def login(body: LoginRequest, request: Request, response: Response):
    _require_same_origin(request)
    if not _allow_request("login", request, *LOGIN_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    password = _secret("ULTRON_WEB_PASSWORD")
    secret = _session_cookie_value()
    if not password or not secret:
        raise HTTPException(status_code=503, detail="Web login is not configured by the service owner.")
    if not hmac.compare_digest(body.password, password):
        raise HTTPException(status_code=401, detail="Incorrect password.")
    response.set_cookie(
        COOKIE_NAME,
        _make_session(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.environ.get("RENDER") == "true",
        samesite="strict",
        path="/",
    )
    return {"ok": True}


@app.post("/api/logout")
async def logout(request: Request, response: Response):
    _require_same_origin(request)
    response.delete_cookie(
        COOKIE_NAME,
        httponly=True,
        secure=os.environ.get("RENDER") == "true",
        samesite="strict",
        path="/",
    )
    return {"ok": True}


@app.post("/api/chat")
async def chat(body: ChatRequest, request: Request):
    _require_same_origin(request)
    if not _valid_session(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Please sign in again.")
    if not _allow_request("chat", request, *CHAT_RATE_LIMIT):
        raise HTTPException(status_code=429, detail="Please wait a moment before sending another message.")
    if len(body.history) > MAX_HISTORY_TURNS:
        raise HTTPException(status_code=422, detail=f"Keep conversation history to {MAX_HISTORY_TURNS} turns or fewer.")
    if not _secret("GEMINI_API_KEY"):
        raise HTTPException(status_code=503, detail="The Gemini API key has not been configured on Render.")
    try:
        answer = await asyncio.to_thread(_generate_reply, body.message.strip(), body.history)
    except Exception as exc:
        print(f"[Ultron Web] Chat request failed: {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="Ultron could not get a reply from the AI service. Check the Render service logs and Gemini API configuration.") from None
    return {"reply": answer}
