"""
Auth controller: orchestrates authentication flows and returns API-ready responses.

Separates HTTP concerns (FastAPI dependency injection, response shaping) from
the auth service's pure domain logic.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from app.config import Settings, get_settings
from app.models.user import UserContext
from app.schemas.auth import TokenResponse, UserInfoResponse
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)


class AuthController:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._auth_service = AuthService(self._settings)

    def exchange_google_token(self, google_id_token: str) -> TokenResponse:
        """
        Validate a Google ID token from the frontend and issue an internal JWT.
        The internal JWT is used for all subsequent API calls.
        """
        try:
            claims = self._auth_service.validate_google_id_token(google_id_token)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc

        email = claims.get("email", "")
        try:
            user_context = self._auth_service.resolve_user_context(email)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc

        token = self._auth_service.issue_internal_jwt(user_context.hwc_user)
        return TokenResponse(
            access_token=token,
            expires_in=self._settings.jwt_expiry_minutes * 60,
        )

    def get_current_user_info(self, user_context: UserContext) -> UserInfoResponse:
        hwc = user_context.hwc_user
        gcp = user_context.gcp_identity
        return UserInfoResponse(
            email=hwc.email,
            display_name=hwc.display_name,
            department=hwc.department,
            jd_code=hwc.jd_code,
            relate_groups=hwc.relate_groups,
            gcp_department_email=gcp.impersonated_email,
        )

    def resolve_context_from_token(
        self,
        token: str,
        session_id: str | None = None,
    ) -> UserContext:
        """
        Validate the bearer JWT and return a populated UserContext.
        Raises HTTP 401/403 on failure.
        """
        try:
            return self._auth_service.resolve_user_context_from_jwt(token, session_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
