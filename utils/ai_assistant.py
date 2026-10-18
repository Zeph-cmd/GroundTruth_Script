# ---------------------------------------------------------------------------
# utils/ai_assistant.py - Botpress Webchat API client for the GroundTruth
# in-app AI assistant.
# ---------------------------------------------------------------------------
# SETUP (developer, once):
#   In Botpress (Vibe): open your agent -> Build -> Channels -> Webchat ->
#   Advanced -> copy the "Client ID" into BOTPRESS_CLIENT_ID below.
#
# The app calls the Webchat REST API directly (not the embeddable webchat
# iframe) so every question is counted and gated in licensing.py before it
# is sent. API shape (verified live against webchat.botpress.cloud):
#   POST /users                              -> {user:{id}, key}
#   POST /conversations   (x-user-key)       -> {conversation:{id}}
#   POST /messages        (x-user-key)       -> body {conversationId, payload:{type:"text", text}}
#   GET  /conversations/{id}/messages (x-user-key) -> {messages:[...]}
# ---------------------------------------------------------------------------
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

# --- CONFIG: your agent's Webchat Client ID (Channels -> Webchat -> Advanced)
BOTPRESS_CLIENT_ID = "c160be9a-284d-4e4d-b6ea-08fce096ff8e"
# ---------------------------------------------------------------------------

WEBCHAT_API_BASE = "https://webchat.botpress.cloud"
_REQUEST_TIMEOUT = 20
_REPLY_WAIT_SECONDS = 45
_POLL_INTERVAL = 1.5

TEXT_MESSAGE_TYPES = {"text", "markdown"}


class AIAssistantError(Exception):
    """User-displayable AI assistant failure."""


def is_configured() -> bool:
    return bool(BOTPRESS_CLIENT_ID.strip())


def _session_path() -> Path:
    import os

    base = os.environ.get("APPDATA", str(Path.home()))
    return Path(base) / "GroundTruth" / "ai_session.json"


def _base_url() -> str:
    return f"{WEBCHAT_API_BASE}/{BOTPRESS_CLIENT_ID.strip()}"


def _load_session() -> dict | None:
    try:
        path = _session_path()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("user_key") and data.get("conversation_id"):
                return data
    except Exception:
        pass
    return None


def _save_session(session: dict) -> None:
    try:
        path = _session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session), encoding="utf-8")
    except Exception:
        pass


def _ensure_session() -> dict:
    """Create the Botpress user + conversation once; reuse on later runs."""
    session = _load_session()
    if session:
        return session

    base = _base_url()
    try:
        resp = requests.post(f"{base}/users", json={}, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        user_id = (data.get("user") or {}).get("id")
        user_key = data.get("key")
        if not user_key or not user_id:
            raise AIAssistantError("The AI service did not return a user key. Contact the developer.")

        resp = requests.post(
            f"{base}/conversations",
            headers={"x-user-key": user_key},
            json={},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        conversation_id = resp.json().get("conversation", {}).get("id")
        if not conversation_id:
            raise AIAssistantError("The AI service did not return a conversation. Contact the developer.")
    except AIAssistantError:
        raise
    except requests.RequestException:
        raise AIAssistantError("Could not reach the AI service. Check your internet connection and try again.")
    except Exception:
        raise AIAssistantError("The AI service returned an unexpected response. Contact the developer.")

    session = {"user_key": user_key, "user_id": user_id, "conversation_id": conversation_id}
    _save_session(session)
    return session


def _message_text(message: dict) -> str | None:
    """Extract user-readable text from a Webchat message.

    Messages look like: {id, createdAt, userId, payload:{type, text, ...}}
    """
    payload = message.get("payload") or {}
    msg_type = payload.get("type")
    if msg_type in TEXT_MESSAGE_TYPES:
        return payload.get("text") or None
    if msg_type == "choice":
        choices = payload.get("choices") or []
        options = "\n".join(f"- {c.get('text', '')}" for c in choices if isinstance(c, dict))
        return (payload.get("text") or "Choose an option:") + (f"\n{options}" if options else "")
    return None


def send_question(question: str) -> list[str]:
    """Send a question to the bot and wait for its reply. Returns reply texts."""
    if not is_configured():
        raise AIAssistantError("The AI assistant is being configured. Contact the developer.")

    session = _ensure_session()
    base = _base_url()
    headers = {"x-user-key": session["user_key"]}

    try:
        resp = requests.post(
            f"{base}/messages",
            headers=headers,
            json={
                "conversationId": session["conversation_id"],
                "payload": {"type": "text", "text": question},
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except AIAssistantError:
        raise
    except requests.RequestException:
        raise AIAssistantError("Could not reach the AI service. Check your internet connection and try again.")

    replies = _wait_for_replies(session, headers)
    if not replies:
        raise AIAssistantError("The assistant did not respond in time. Try again in a moment.")
    return replies


def _wait_for_replies(session: dict, headers: dict) -> list[str]:
    """Poll the conversation until the bot replies after our question."""
    known_ids = set()
    our_user_id = session.get("user_id")

    # Snapshot existing messages so we only surface NEW ones.
    try:
        existing = _list_messages(session, headers)
        for msg in existing:
            known_ids.add(msg.get("id"))
    except Exception:
        pass

    deadline = time.time() + _REPLY_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        try:
            messages = _list_messages(session, headers)
        except Exception:
            continue
        replies = []
        for msg in messages:
            if msg.get("id") in known_ids:
                continue
            if our_user_id and msg.get("userId") == our_user_id:
                continue
            text = _message_text(msg)
            if text:
                replies.append(text)
        if replies:
            return replies
    return []


def _list_messages(session: dict, headers: dict) -> list[dict]:
    base = _base_url()
    resp = requests.get(
        f"{base}/conversations/{session['conversation_id']}/messages",
        headers=headers,
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    messages = resp.json().get("messages") or []
    # Return oldest-first regardless of API ordering.
    return sorted(messages, key=lambda m: m.get("createdAt", ""))
