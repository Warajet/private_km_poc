"""Chat and session endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, status

from app.config import Settings, get_settings
from app.controllers.chat_controller import ChatController
from app.middleware.auth_middleware import get_user_context
from app.models.user import UserContext
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    NewSessionRequest,
    SessionHistoryResponse,
    SessionResponse,
)

router = APIRouter(prefix="/chat", tags=["chat"])


def _controller(settings: Settings = Depends(get_settings)) -> ChatController:
    return ChatController(settings=settings)


# ── Chat ────────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ChatResponse,
    summary="Send a chat message",
    description=(
        "Send a message to the knowledge-base chatbot. "
        "Provide `session_id` to continue an existing conversation; "
        "omit it to start a new one.\n\n"
        "**Two-layer access control** is applied automatically:\n"
        "- **Layer 1 (API)**: only the datastores the user's identity is "
        "authorised to query are selected (Public, Internal-dept, Relate-groups, "
        "Confidential-if-JD-code).\n"
        "- **Layer 2 (ACL)**: within each selected datastore, Discovery Engine "
        "evaluates `acl_info` against the impersonated GCP identity."
    ),
)
def send_message(
    body: ChatRequest,
    user_context: UserContext = Depends(get_user_context),
    controller: ChatController = Depends(_controller),
):
    return controller.chat(body, user_context)


# ── Sessions ────────────────────────────────────────────────────────────────────

@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new chat session",
)
def create_session(
    _body: NewSessionRequest = None,
    user_context: UserContext = Depends(get_user_context),
    controller: ChatController = Depends(_controller),
):
    return controller.create_session(user_context)


@router.get(
    "/sessions",
    response_model=List[SessionResponse],
    summary="List the current user's sessions",
)
def list_sessions(
    user_context: UserContext = Depends(get_user_context),
    controller: ChatController = Depends(_controller),
):
    return controller.list_sessions(user_context)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionHistoryResponse,
    summary="Get session message history",
)
def get_session(
    session_id: str,
    user_context: UserContext = Depends(get_user_context),
    controller: ChatController = Depends(_controller),
):
    return controller.get_session_history(session_id, user_context)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a session",
)
def delete_session(
    session_id: str,
    user_context: UserContext = Depends(get_user_context),
    controller: ChatController = Depends(_controller),
):
    controller.delete_session(session_id, user_context)


# ── Debug / introspection ───────────────────────────────────────────────────────

@router.get(
    "/access",
    summary="Describe accessible datastores for the current user",
    description=(
        "Returns which datastore buckets this user can query and why. "
        "Useful for debugging access issues. "
        "Only available when DEBUG=true."
    ),
)
def describe_access(
    user_context: UserContext = Depends(get_user_context),
    controller: ChatController = Depends(_controller),
    settings: Settings = Depends(get_settings),
):
    if not settings.debug:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not found",
        )
    return controller.describe_access(user_context)
