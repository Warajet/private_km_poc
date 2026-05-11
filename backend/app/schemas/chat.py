"""Pydantic request/response schemas for the chat API."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Inbound ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8192)
    session_id: Optional[str] = Field(
        None,
        description="Omit to start a new session; provide to continue an existing one.",
    )


class NewSessionRequest(BaseModel):
    """Explicitly create a session before chatting (optional convenience endpoint)."""
    pass


# ── Outbound ──────────────────────────────────────────────────────────────────

class SourceDocument(BaseModel):
    id: str
    title: str
    snippet: str
    access_level: str
    department: Optional[str] = None
    uri: Optional[str] = None
    relevance_score: float = 0.0


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    timestamp: datetime
    sources: List[SourceDocument] = []


class ChatResponse(BaseModel):
    session_id: str
    message: ChatMessageResponse
    grounded: bool = False


class SessionResponse(BaseModel):
    session_id: str
    user_email: str
    department: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class SessionHistoryResponse(BaseModel):
    session_id: str
    messages: List[ChatMessageResponse]
