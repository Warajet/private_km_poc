"""Domain models for chat sessions and messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

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
    # Discovery Engine session resource names, keyed by datastore_id.
    # Populated after the first answer_query turn; passed on subsequent turns
    # to continue the multi-turn conversation within each datastore.
    de_sessions: Dict[str, str] = field(default_factory=dict)


@dataclass
class ChatResponse:
    session_id: str
    message: ChatMessage
    sources: List[RetrievedDocument] = field(default_factory=list)
    grounded: bool = False
