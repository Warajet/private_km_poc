"""
GCP authentication helpers.

Provides two credential modes:
  1. Domain-wide Delegation (DWD) — Service Account impersonates a Workspace user.
     Used when a service account JSON key is available (local dev or explicit key mount).
  2. Workload Identity + impersonation — preferred on Cloud Run / GKE.
     The attached SA gets a short-lived token for the target email via the
     IAM Credentials API (requires roles/iam.serviceAccountTokenCreator on target).

Both modes return google.auth.credentials.Credentials scoped to cloud-platform,
ready to pass into Discovery Engine / Vertex AI clients.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import google.auth
import google.auth.impersonated_credentials as impersonated_creds
from google.auth.transport.requests import Request
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def get_impersonated_credentials(
    target_email: str,
    scopes: Optional[List[str]] = None,
    sa_key_file: Optional[str] = None,
    lifetime_seconds: int = 3600,
):
    """
    Return credentials that act as `target_email`.

    Strategy:
    - If `sa_key_file` is provided → use DWD via service_account.Credentials.with_subject().
      The SA must have DWD enabled in Google Workspace Admin for the requested scopes.
    - Otherwise → use ADC (Application Default Credentials) as the source and create
      impersonated_credentials.Credentials targeting `target_email`.
      The ADC principal must have roles/iam.serviceAccountTokenCreator on the target SA.

    On Cloud Run the ADC is automatically the attached SA; no key file is needed.
    """
    resolved_scopes = scopes or [_CLOUD_PLATFORM_SCOPE]

    if sa_key_file:
        return _dwd_credentials(sa_key_file, target_email, resolved_scopes)

    return _workload_identity_impersonation(target_email, resolved_scopes, lifetime_seconds)


def _dwd_credentials(key_file: str, subject: str, scopes: List[str]):
    """Service account key file + Domain-wide Delegation."""
    logger.debug("Using DWD credentials: key=%s subject=%s", key_file, subject)
    sa_creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=scopes
    )
    return sa_creds.with_subject(subject)


def _workload_identity_impersonation(
    target_email: str,
    scopes: List[str],
    lifetime_seconds: int,
):
    """ADC source + impersonated_credentials targeting a Workspace user / SA."""
    logger.debug("Using Workload Identity impersonation: target=%s", target_email)
    source_credentials, _ = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
    # Refresh so the source token is valid
    source_credentials.refresh(Request())

    return impersonated_creds.Credentials(
        source_credentials=source_credentials,
        target_principal=target_email,
        target_scopes=scopes,
        lifetime=lifetime_seconds,
    )


def get_application_default_credentials(scopes: Optional[List[str]] = None):
    """
    Plain ADC — used for API calls that do NOT need user-level impersonation
    (e.g. session management, health checks).
    """
    resolved_scopes = scopes or [_CLOUD_PLATFORM_SCOPE]
    credentials, project = google.auth.default(scopes=resolved_scopes)
    return credentials, project
