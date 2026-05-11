"""
ChatController — orchestrates a complete chat turn end-to-end.

Two-layer access-control flow per turn
───────────────────────────────────────
 ┌─────────────────────────────────────────────────────────────────────────┐
 │  LAYER 1 — API Routing  (DatastoreRoutingService)                       │
 │  Decides WHICH datastore buckets to query based on user identity:       │
 │    PUBLIC  → always                                                     │
 │    INTERNAL→ user's own department bucket only                          │
 │    RELATE  → only relate-group buckets the user belongs to              │
 │    CONFID. → confidential bucket, only if user has a JD code            │
 └───────────────────────────────────┬─────────────────────────────────────┘
                                     │  List[DatastoreTarget]
 ┌───────────────────────────────────▼─────────────────────────────────────┐
 │  LAYER 2 — ACL Enforcement  (DiscoveryEngineService)                    │
 │  Queries each target in parallel with impersonated credentials.         │
 │  Discovery Engine evaluates acl_info on every document, ensuring each  │
 │  bucket's contents are only visible to authorised identities.           │
 │  CONFIDENTIAL target also carries a JD-code filter expression.         │
 └───────────────────────────────────┬─────────────────────────────────────┘
                                     │  List[RetrievedDocument] (merged)
 ┌───────────────────────────────────▼─────────────────────────────────────┐
 │  GeminiService — grounded response generation                           │
 │  Builds a grounded prompt from the retrieved docs + conversation        │
 │  history and calls Gemini via Vertex AI (same impersonated creds).      │
 └─────────────────────────────────────────────────────────────────────────┘
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
from app.services.datastore_routing_service import DatastoreRoutingService
from app.services.discovery_engine_service import DiscoveryEngineService
from app.services.gemini_service import GeminiService
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
        """Execute one chat turn and return the grounded assistant response."""

        # ── 1. Resolve / create session ────────────────────────────────────────
        try:
            session = self._session_service.get_or_create_session(
                user_context, request.session_id
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

        # ── 2. Persist user message ────────────────────────────────────────────
        self._session_service.append_user_message(session, request.message)

        # ── 3. Layer 1: determine accessible datastore buckets ─────────────────
        try:
            routing_service = DatastoreRoutingService()
            targets = routing_service.resolve_targets(user_context)
        except RuntimeError as exc:
            # Registry not initialised — configuration error
            logger.error("Datastore registry not initialised: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Knowledge base registry is not configured. Contact your administrator.",
            ) from exc

        logger.info(
            "User %s → %d accessible buckets: %s",
            user_context.hwc_user.email,
            len(targets),
            [t.label for t in targets],
        )

        # ── 4. Layer 2: parallel search across all accessible datastores ───────
        de_service = DiscoveryEngineService(
            gcp_identity=user_context.gcp_identity,
            settings=self._settings,
        )

        try:
            retrieved_docs = de_service.search_targets(
                query=request.message,
                targets=targets,
            )
        except Exception as exc:
            logger.error("Discovery Engine search error: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Error retrieving documents from the knowledge base.",
            ) from exc

        # ── 5. Generate grounded Gemini response ───────────────────────────────
        try:
            gemini = GeminiService(
                gcp_identity=user_context.gcp_identity,
                credentials=de_service.credentials,
                settings=self._settings,
            )
            answer_text = gemini.generate(
                user_message=request.message,
                history=session.messages[:-1],  # exclude the just-appended user msg
                retrieved_docs=retrieved_docs,
            )
        except Exception as exc:
            logger.error("Gemini generation error: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Error generating response from the AI model.",
            ) from exc

        # ── 6. Persist assistant message ───────────────────────────────────────
        assistant_msg = self._session_service.append_assistant_message(
            session, answer_text, retrieved_docs
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
            for doc in retrieved_docs
        ]

        return ChatResponse(
            session_id=session.id,
            message=ChatMessageResponse(
                role=assistant_msg.role.value,
                content=assistant_msg.content,
                timestamp=assistant_msg.timestamp,
                sources=sources,
            ),
            grounded=len(retrieved_docs) > 0,
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
            self._session_service.get_session(
                session_id, user_context.hwc_user.email
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))

        self._session_service.delete_session(session_id, user_context.hwc_user.email)

    # ── Access introspection (debug endpoint) ──────────────────────────────────

    def describe_access(self, user_context: UserContext) -> dict:
        """
        Returns which datastores this user can query and why.
        Useful for debugging ACL issues during onboarding.
        """
        try:
            routing_service = DatastoreRoutingService()
            return routing_service.describe_access(user_context)
        except RuntimeError:
            return {"error": "Registry not initialised"}

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
