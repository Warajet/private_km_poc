"""Domain models for chat sessions and messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from app.models.document import RetrievedDocument


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ChatMessage:
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    sources: List[RetrievedDocument] = field(default_factory=list)


@dataclass
class ChatSession:
    id: str
    user_email: str
    department: str
    jd_code: str
    messages: List[ChatMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    # Engine-level DE session resource name (single session for the whole chat).
    # Empty until the first answer_query turn; passed on subsequent turns to
    # continue the multi-turn conversation.
    de_session_name: Optional[str] = None


@dataclass
class ChatResponse:
    session_id: str
    message: ChatMessage
    sources: List[RetrievedDocument] = field(default_factory=list)
    grounded: bool = False
