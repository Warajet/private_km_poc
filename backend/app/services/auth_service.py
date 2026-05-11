"""
Authentication and identity-resolution service.

Responsibilities:
  1. Validate the bearer token sent by the frontend (Google ID token or internal JWT).
  2. Look up the HWC user record from the user mapping.
  3. Resolve the GCP identity (department email) the SA will impersonate.
  4. Issue short-lived internal JWTs for subsequent requests.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import jwt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import Settings, get_settings
from app.models.user import GCPIdentity, HWCUser, UserContext
from app.utils.user_mapper import resolve_gcp_identity, resolve_hwc_user

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    # ── Token validation ───────────────────────────────────────────────────────

    def validate_google_id_token(self, raw_token: str) -> dict:
        """
        Validate a Google ID token (issued by Google Sign-In / OIDC).
        Returns the decoded claims dict.
        Raises ValueError on invalid token.
        """
        try:
            claims = id_token.verify_oauth2_token(
                raw_token,
                google_requests.Request(),
                clock_skew_in_seconds=30,
            )
        except Exception as exc:
            raise ValueError(f"Invalid Google ID token: {exc}") from exc

        if not claims.get("email_verified"):
            raise ValueError("Google account email is not verified")

        return claims

    def validate_internal_jwt(self, token: str) -> dict:
        """
        Validate a JWT issued by this service (used for subsequent requests
        after the initial Google ID token exchange).
        """
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError:
            raise ValueError("Token has expired")
        except jwt.InvalidTokenError as exc:
            raise ValueError(f"Invalid token: {exc}") from exc
        return payload

    def issue_internal_jwt(self, hwc_user: HWCUser) -> str:
        """Issue a short-lived JWT for the given HWC user."""
        exp = datetime.utcnow() + timedelta(minutes=self._settings.jwt_expiry_minutes)
        payload = {
            "sub": hwc_user.email,
            "email": hwc_user.email,
            "department": hwc_user.department,
            "jd_code": hwc_user.jd_code,
            "relate_groups": hwc_user.relate_groups,
            "exp": exp,
        }
        return jwt.encode(
            payload,
            self._settings.jwt_secret,
            algorithm=self._settings.jwt_algorithm,
        )

    # ── Identity resolution ────────────────────────────────────────────────────

    def resolve_user_context(
        self,
        hwc_email: str,
        session_id: Optional[str] = None,
    ) -> UserContext:
        """
        Given an HWC user email, return the full UserContext including
        the GCP identity the service account will impersonate.
        Raises ValueError if the user is unknown.
        """
        hwc_user = resolve_hwc_user(hwc_email)
        if hwc_user is None:
            raise ValueError(
                f"Unknown user '{hwc_email}'. "
                "User must be registered in the department mapping."
            )

        gcp_identity = resolve_gcp_identity(hwc_user, self._settings.workspace_domain)
        logger.info(
            "Resolved identity: hwc=%s → gcp=%s (dept=%s jd=%s)",
            hwc_email,
            gcp_identity.impersonated_email,
            gcp_identity.department,
            gcp_identity.jd_code,
        )
        return UserContext(
            hwc_user=hwc_user,
            gcp_identity=gcp_identity,
            session_id=session_id,
        )

    def resolve_user_context_from_jwt(
        self,
        token: str,
        session_id: Optional[str] = None,
    ) -> UserContext:
        """Validate an internal JWT and return the UserContext."""
        payload = self.validate_internal_jwt(token)
        return self.resolve_user_context(payload["email"], session_id)

    # ── Authorization checks (application-layer) ───────────────────────────────

    def can_access_confidential(
        self,
        gcp_identity: GCPIdentity,
        required_jd_code: str,
    ) -> bool:
        """
        Application-layer check for CONFIDENTIAL documents.
        Discovery Engine ACL already restricts to the department; this check
        additionally verifies the user's JD code matches the document's required code.
        """
        return gcp_identity.jd_code.upper() == required_jd_code.upper()
