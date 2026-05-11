"""
Chat controller: orchestrates a chat turn end-to-end.

Flow per chat turn:
  1. Get or create a ChatSession for the user.
  2. Record the incoming user message.
  3. Build impersonated GCP credentials for the user's department identity.
  4. Ensure the session has a Discovery Engine conversation resource.
  5. Call DiscoveryEngineService.converse() — retrieves ACL-filtered docs and
     generates a grounded Gemini response.
  6. Append the assistant message (with source citations) to the session.
  7. Return a ChatResponse.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from app.config import Settings, get_settings
from app.models.user import UserContext
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ChatMessageResponse,
    SessionHistoryResponse,
    SessionResponse,
    SourceDocument,
)
from app.services.discovery_engine_service import DiscoveryEngineService
from app.services.session_service import SessionService

logger = logging.getLogger(__name__)


class ChatController:
    def __init__(
        self,
        session_service: SessionService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_service = session_service or SessionService(settings=self._settings)

    # ── Chat turn ──────────────────────────────────────────────────────────────

    def chat(self, request: ChatRequest, user_context: UserContext) -> ChatResponse:
        """Execute a single chat turn and return the assistant response."""
        try:
            session = self._session_service.get_or_create_session(
                user_context, request.session_id
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

        # Record incoming message
        self._session_service.append_user_message(session, request.message)

        # Build the DE service scoped to this user's identity
        de_service = DiscoveryEngineService(
            gcp_identity=user_context.gcp_identity,
            settings=self._settings,
        )

        # Ensure a DE conversation exists for multi-turn continuity
        if not session.de_conversation_name:
            try:
                conv_name = de_service.create_conversation()
                self._session_service.set_de_conversation(session, conv_name)
            except Exception as exc:
                logger.error("Failed to create DE conversation: %s", exc)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Could not initialise conversation with the knowledge base.",
                ) from exc

        # Call Gemini Enterprise via Discovery Engine
        try:
            answer_text, documents = de_service.converse(
                user_message=request.message,
                conversation_name=session.de_conversation_name,
                jd_code=user_context.gcp_identity.jd_code or None,
            )
        except Exception as exc:
            logger.error("Discovery Engine error: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Error retrieving answer from the knowledge base.",
            ) from exc

        if not answer_text:
            answer_text = (
                "I could not find relevant information in the knowledge base "
                "for your query. Please try rephrasing or contact your administrator."
            )

        # Persist assistant message with source citations
        assistant_msg = self._session_service.append_assistant_message(
            session, answer_text, documents
        )

        sources = [
            SourceDocument(
                id=doc.id,
                title=doc.struct_data.title,
                snippet=doc.snippet,
                access_level=doc.struct_data.access_level.value,
                department=doc.struct_data.department,
                uri=doc.uri,
                relevance_score=doc.relevance_score,
            )
            for doc in documents
        ]

        return ChatResponse(
            session_id=session.id,
            message=ChatMessageResponse(
                role=assistant_msg.role.value,
                content=assistant_msg.content,
                timestamp=assistant_msg.timestamp,
                sources=sources,
            ),
            grounded=len(documents) > 0,
        )

    # ── Session management ─────────────────────────────────────────────────────

    def create_session(self, user_context: UserContext) -> SessionResponse:
        session = self._session_service.create_session(user_context)
        return self._to_session_response(session)

    def list_sessions(self, user_context: UserContext) -> list[SessionResponse]:
        sessions = self._session_service.list_sessions(user_context.hwc_user.email)
        return [self._to_session_response(s) for s in sessions]

    def get_session_history(
        self,
        session_id: str,
        user_context: UserContext,
    ) -> SessionHistoryResponse:
        try:
            session = self._session_service.get_session(
                session_id, user_context.hwc_user.email
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

        messages = [
            ChatMessageResponse(
                role=m.role.value,
                content=m.content,
                timestamp=m.timestamp,
                sources=[
                    SourceDocument(
                        id=s.id,
                        title=s.struct_data.title,
                        snippet=s.snippet,
                        access_level=s.struct_data.access_level.value,
                        department=s.struct_data.department,
                        uri=s.uri,
                        relevance_score=s.relevance_score,
                    )
                    for s in m.sources
                ],
            )
            for m in session.messages
        ]
        return SessionHistoryResponse(session_id=session.id, messages=messages)

    def delete_session(self, session_id: str, user_context: UserContext) -> None:
        try:
            session = self._session_service.get_session(
                session_id, user_context.hwc_user.email
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

        # Clean up the Discovery Engine conversation resource
        if session.de_conversation_name:
            de_service = DiscoveryEngineService(
                gcp_identity=user_context.gcp_identity,
                settings=self._settings,
            )
            de_service.delete_conversation(session.de_conversation_name)

        self._session_service.delete_session(session_id, user_context.hwc_user.email)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_session_response(session) -> SessionResponse:
        return SessionResponse(
            session_id=session.id,
            user_email=session.user_email,
            department=session.department,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=len(session.messages),
        )
