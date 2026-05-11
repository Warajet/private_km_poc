"""Pydantic request/response schemas for authentication."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class TokenRequest(BaseModel):
    """Frontend sends this to exchange a Google ID token for an API JWT."""
    google_id_token: str = Field(..., description="Google ID token from frontend OIDC flow")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserInfoResponse(BaseModel):
    email: str
    display_name: str
    department: str
    jd_code: str
    relate_groups: List[str] = []
    gcp_department_email: str


class ServiceAccountAuthRequest(BaseModel):
    """
    Used when the caller is a Service Account (machine-to-machine).
    The SA presents a signed JWT / access token; the API validates it against
    Google's token introspection endpoint and confirms it has the VertexAI role.
    """
    service_account_email: str
    # Impersonation target – the department email derived from the HWC user's dept
    impersonate_email: str
    jd_code: Optional[str] = None
