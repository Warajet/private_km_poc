"""
ChatController — orchestrates a complete chat turn end-to-end.

answer_query() flow per turn
──────────────────────────────
 ┌─────────────────────────────────────────────────────────────────────────┐
 │  LAYER 1 — API Routing  (DatastoreRoutingService)                       │
 │  Selects which datastore buckets to query.                              │
 └───────────────────────────────────┬─────────────────────────────────────┘
                                     │  List[DatastoreTarget]
 ┌───────────────────────────────────▼─────────────────────────────────────┐
 │  LAYER 2 — answer_query()  (DiscoveryEngineService)                     │
 │                                                                         │
 │  For each target:                                                       │
 │    ConversationalSearchServiceClient.answer_query(                      │
 │        serving_config = <bucket-datastore-serving-config>,              │
 │        query          = Query(text=user_message),                       │
 │        session        = de_sessions[datastore_id],  # multi-turn       │
 │        user_pseudo_id = impersonated_email,                             │
 │        ...                                                              │
 │    )                                                                    │
 │                                                                         │
 │  → Discovery Engine enforces acl_info against impersonated credentials │
 │  → Gemini generates a grounded answer from retrieved documents         │
 │  → Response.session contains the updated session name                  │
 └───────────────────────────────────┬─────────────────────────────────────┘
                                     │  AggregatedAnswer
 ┌───────────────────────────────────▼─────────────────────────────────────┐
 │  Session update                                                         │
 │  Store updated DE session names so next turn continues each conversation│
 └─────────────────────────────────────────────────────────────────────────┘

No separate GeminiService exists. answer_query() handles both retrieval and
answer generation in a single API call.
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
        """Execute one chat turn via answer_query() and return the response."""

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
            routing = DatastoreRoutingService()
            targets = routing.resolve_targets(user_context)
        except RuntimeError as exc:
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

        # ── 4. Layer 2: answer_query() across all accessible datastores ────────
        de_service = DiscoveryEngineService(
            gcp_identity=user_context.gcp_identity,
            settings=self._settings,
        )

        try:
            aggregated = de_service.answer_all_targets(
                query=request.message,
                targets=targets,
                # Pass existing DE session names so answer_query() continues
                # the multi-turn conversation within each datastore bucket.
                de_sessions=session.de_sessions,
            )
        except Exception as exc:
            logger.error("answer_query error: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Error retrieving answer from the knowledge base.",
            ) from exc

        # ── 5. Persist updated DE session names for next turn ──────────────────
        if aggregated.updated_de_sessions:
            self._session_service.update_de_sessions(
                session, aggregated.updated_de_sessions
            )

        # ── 6. Fallback answer when knowledge base has nothing ─────────────────
        answer_text = aggregated.answer_text or (
            "I could not find relevant information in the knowledge base for your query. "
            "Please try rephrasing or contact your administrator."
        )

        # ── 7. Persist assistant message with source citations ─────────────────
        assistant_msg = self._session_service.append_assistant_message(
            session, answer_text, aggregated.references
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
            for doc in aggregated.references
        ]

        return ChatResponse(
            session_id=session.id,
            message=ChatMessageResponse(
                role=assistant_msg.role.value,
                content=assistant_msg.content,
                timestamp=assistant_msg.timestamp,
                sources=sources,
            ),
            grounded=aggregated.grounded,
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

    # ── Access introspection ───────────────────────────────────────────────────

    def describe_access(self, user_context: UserContext) -> dict:
        """Returns which datastores this user can query. Debug only."""
        try:
            routing = DatastoreRoutingService()
            return routing.describe_access(user_context)
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
