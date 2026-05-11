"""Chat session service."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

from app.config import Settings, get_settings
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.document import RetrievedDocument
from app.models.user import UserContext
from app.repositories.session_repository import SessionRepository, get_repository

logger = logging.getLogger(__name__)


class SessionService:
    def __init__(
        self,
        repository: Optional[SessionRepository] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._repo = repository or get_repository()
        self._settings = settings or get_settings()

    # ── Session lifecycle ──────────────────────────────────────────────────────

    def create_session(self, user_context: UserContext) -> ChatSession:
        session_id = str(uuid.uuid4())
        session = ChatSession(
            id=session_id,
            user_email=user_context.hwc_user.email,
            department=user_context.hwc_user.department,
            jd_code=user_context.hwc_user.jd_code,
        )
        self._repo.save(session)
        logger.info("Created session %s for %s", session_id, user_context.hwc_user.email)
        return session

    def get_session(self, session_id: str, user_email: str) -> ChatSession:
        session = self._repo.get(session_id)
        if session is None:
            raise ValueError(f"Session '{session_id}' not found")
        if session.user_email != user_email:
            raise PermissionError(f"Session '{session_id}' does not belong to this user")
        return session

    def get_or_create_session(
        self,
        user_context: UserContext,
        session_id: Optional[str],
    ) -> ChatSession:
        if session_id:
            return self.get_session(session_id, user_context.hwc_user.email)
        return self.create_session(user_context)

    def list_sessions(self, user_email: str) -> List[ChatSession]:
        return self._repo.list_by_user(user_email)

    def delete_session(self, session_id: str, user_email: str) -> None:
        session = self.get_session(session_id, user_email)
        self._repo.delete(session.id)
        logger.info("Deleted session %s", session_id)

    # ── Message management ─────────────────────────────────────────────────────

    def append_user_message(self, session: ChatSession, content: str) -> ChatMessage:
        msg = ChatMessage(role=MessageRole.USER, content=content)
        session.messages.append(msg)
        session.updated_at = datetime.utcnow()
        self._repo.save(session)
        return msg

    def append_assistant_message(
        self,
        session: ChatSession,
        content: str,
        sources: Optional[List[RetrievedDocument]] = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            sources=sources or [],
        )
        session.messages.append(msg)
        session.updated_at = datetime.utcnow()
        self._repo.save(session)
        return msg

    # ── Discovery Engine session tracking ──────────────────────────────────────

    def update_de_session(
        self,
        session: ChatSession,
        session_name: str,
    ) -> None:
        """
        Persist the Discovery Engine engine-level session resource name.
        Called after each answer_query turn so subsequent turns can continue
        the multi-turn conversation.
        """
        session.de_session_name = session_name
        self._repo.save(session)
