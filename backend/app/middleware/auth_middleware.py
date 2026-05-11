"""
FastAPI dependency that extracts and validates the bearer token on every
protected endpoint, returning a populated UserContext.

Usage in endpoints:
    @router.post("/chat")
    def chat(request: ChatRequest, ctx: UserContext = Depends(get_user_context)):
        ...
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.controllers.auth_controller import AuthController
from app.models.user import UserContext

_bearer_scheme = HTTPBearer(auto_error=True)


def get_user_context(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> UserContext:
    """
    FastAPI dependency that validates the bearer JWT and resolves the caller's
    HWC identity + GCP impersonation target.

    Raises HTTP 401 if the token is missing or invalid.
    Raises HTTP 403 if the email is not in the user mapping.
    """
    controller = AuthController(settings)
    return controller.resolve_context_from_token(credentials.credentials)
