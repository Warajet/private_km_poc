"""
Session repository: pluggable persistence backend for ChatSession objects.

Two implementations:
  - InMemorySessionRepository  (default; suitable for local dev and single-instance)
  - RedisSessionRepository      (production; requires REDIS_URL env var)
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional

from app.config import Settings, get_settings
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.document import DocumentStructData, RetrievedDocument
from app.models.user import AccessLevel

logger = logging.getLogger(__name__)


# ── Abstract base ──────────────────────────────────────────────────────────────

class SessionRepository(ABC):
    @abstractmethod
    def save(self, session: ChatSession) -> None: ...

    @abstractmethod
    def get(self, session_id: str) -> Optional[ChatSession]: ...

    @abstractmethod
    def delete(self, session_id: str) -> None: ...

    @abstractmethod
    def list_by_user(self, user_email: str) -> List[ChatSession]: ...


# ── In-memory implementation ────────────────────────────────────────────────────

class InMemorySessionRepository(SessionRepository):
    def __init__(self) -> None:
        self._store: Dict[str, ChatSession] = {}

    def save(self, session: ChatSession) -> None:
        self._store[session.id] = session

    def get(self, session_id: str) -> Optional[ChatSession]:
        return self._store.get(session_id)

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    def list_by_user(self, user_email: str) -> List[ChatSession]:
        return [s for s in self._store.values() if s.user_email == user_email]


# ── Redis implementation ────────────────────────────────────────────────────────

class RedisSessionRepository(SessionRepository):
    """
    Stores sessions as JSON in Redis with a TTL.
    Requires `redis` package: pip install redis
    """

    def __init__(self, redis_url: str, ttl: int = 3600) -> None:
        import redis  # type: ignore[import]
        self._client = redis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def _user_index_key(self, user_email: str) -> str:
        return f"user_sessions:{user_email}"

    def save(self, session: ChatSession) -> None:
        data = _session_to_dict(session)
        self._client.set(self._key(session.id), json.dumps(data), ex=self._ttl)
        self._client.sadd(self._user_index_key(session.user_email), session.id)
        self._client.expire(self._user_index_key(session.user_email), self._ttl)

    def get(self, session_id: str) -> Optional[ChatSession]:
        raw = self._client.get(self._key(session_id))
        if raw is None:
            return None
        return _session_from_dict(json.loads(raw))

    def delete(self, session_id: str) -> None:
        session = self.get(session_id)
        if session:
            self._client.srem(self._user_index_key(session.user_email), session_id)
        self._client.delete(self._key(session_id))

    def list_by_user(self, user_email: str) -> List[ChatSession]:
        session_ids = self._client.smembers(self._user_index_key(user_email))
        sessions = []
        for sid in session_ids:
            s = self.get(sid)
            if s:
                sessions.append(s)
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)


# ── Serialization helpers ──────────────────────────────────────────────────────

def _session_to_dict(session: ChatSession) -> dict:
    return {
        "id": session.id,
        "user_email": session.user_email,
        "department": session.department,
        "jd_code": session.jd_code,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "de_conversation_name": session.de_conversation_name,
        "messages": [
            {
                "role": m.role.value,
                "content": m.content,
                "timestamp": m.timestamp.isoformat(),
                "sources": [
                    {
                        "id": s.id,
                        "title": s.struct_data.title,
                        "access_level": s.struct_data.access_level.value,
                        "department": s.struct_data.department,
                        "snippet": s.snippet,
                        "uri": s.uri,
                        "relevance_score": s.relevance_score,
                    }
                    for s in m.sources
                ],
            }
            for m in session.messages
        ],
    }


def _session_from_dict(data: dict) -> ChatSession:
    messages = []
    for m in data.get("messages", []):
        sources = []
        for src in m.get("sources", []):
            sources.append(
                RetrievedDocument(
                    id=src["id"],
                    struct_data=DocumentStructData(
                        title=src.get("title", ""),
                        access_level=AccessLevel(src.get("access_level", "public")),
                        department=src.get("department"),
                    ),
                    snippet=src.get("snippet", ""),
                    uri=src.get("uri"),
                    relevance_score=src.get("relevance_score", 0.0),
                )
            )
        messages.append(
            ChatMessage(
                role=MessageRole(m["role"]),
                content=m["content"],
                timestamp=datetime.fromisoformat(m["timestamp"]),
                sources=sources,
            )
        )

    return ChatSession(
        id=data["id"],
        user_email=data["user_email"],
        department=data["department"],
        jd_code=data["jd_code"],
        messages=messages,
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
        de_conversation_name=data.get("de_conversation_name"),
    )


# ── Factory ─────────────────────────────────────────────────────────────────────

_repository: Optional[SessionRepository] = None


def get_repository(settings: Optional[Settings] = None) -> SessionRepository:
    global _repository
    if _repository is not None:
        return _repository

    cfg = settings or get_settings()
    if cfg.session_backend == "redis" and cfg.redis_url:
        logger.info("Using Redis session repository: %s", cfg.redis_url)
        _repository = RedisSessionRepository(cfg.redis_url, cfg.session_ttl_seconds)
    else:
        logger.info("Using in-memory session repository")
        _repository = InMemorySessionRepository()

    return _repository
