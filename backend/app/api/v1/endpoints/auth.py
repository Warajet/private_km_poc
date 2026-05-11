"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.controllers.auth_controller import AuthController
from app.middleware.auth_middleware import get_user_context
from app.models.user import UserContext
from app.schemas.auth import TokenRequest, TokenResponse, UserInfoResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Exchange Google ID token for API JWT",
    description=(
        "Frontend sends the Google ID token obtained from OIDC sign-in. "
        "Returns a short-lived JWT used for all subsequent API calls."
    ),
)
def exchange_token(
    body: TokenRequest,
    settings: Settings = Depends(get_settings),
):
    controller = AuthController(settings)
    return controller.exchange_google_token(body.google_id_token)


@router.get(
    "/me",
    response_model=UserInfoResponse,
    summary="Get current user information",
    description="Returns the authenticated user's HWC profile and resolved GCP identity.",
)
def get_me(
    user_context: UserContext = Depends(get_user_context),
    settings: Settings = Depends(get_settings),
):
    controller = AuthController(settings)
    return controller.get_current_user_info(user_context)
