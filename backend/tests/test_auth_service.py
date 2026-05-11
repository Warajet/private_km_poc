"""Unit tests for the AuthService (no GCP calls)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import jwt
import pytest

from app.services.auth_service import AuthService
from app.utils.user_mapper import register_user


@pytest.fixture()
def settings():
    mock = MagicMock()
    mock.jwt_secret = "test-secret"
    mock.jwt_algorithm = "HS256"
    mock.jwt_expiry_minutes = 60
    mock.workspace_domain = "hello.org"
    return mock


@pytest.fixture(autouse=True)
def seed():
    register_user(
        email="userA@hello.org",
        department="A",
        jd_code="ENG001",
        display_name="User A",
        relate_groups=["ab"],
    )


@pytest.fixture()
def service(settings):
    return AuthService(settings=settings)


class TestIssueAndValidateJWT:
    def test_round_trip(self, service, settings):
        from app.models.user import HWCUser
        hwc = HWCUser(email="userA@hello.org", department="A", jd_code="ENG001")
        token = service.issue_internal_jwt(hwc)
        payload = service.validate_internal_jwt(token)
        assert payload["email"] == "userA@hello.org"
        assert payload["department"] == "A"
        assert payload["jd_code"] == "ENG001"

    def test_expired_token_raises(self, service):
        expired = jwt.encode(
            {"email": "x@x.com", "exp": 0},
            "test-secret",
            algorithm="HS256",
        )
        with pytest.raises(ValueError, match="expired"):
            service.validate_internal_jwt(expired)

    def test_wrong_secret_raises(self, service):
        bad = jwt.encode({"email": "x@x.com"}, "wrong-secret", algorithm="HS256")
        with pytest.raises(ValueError):
            service.validate_internal_jwt(bad)


class TestResolveUserContext:
    def test_known_user(self, service):
        ctx = service.resolve_user_context("userA@hello.org")
        assert ctx.hwc_user.department == "A"
        assert ctx.gcp_identity.impersonated_email == "A@hello.org"

    def test_unknown_user_raises(self, service):
        with pytest.raises(ValueError, match="Unknown user"):
            service.resolve_user_context("nobody@hello.org")


class TestConfidentialAccess:
    def test_matching_jd_code(self, service):
        from app.models.user import GCPIdentity
        gcp = GCPIdentity(
            impersonated_email="A@hello.org",
            department="A",
            jd_code="ENG001",
        )
        assert service.can_access_confidential(gcp, "ENG001") is True

    def test_non_matching_jd_code(self, service):
        from app.models.user import GCPIdentity
        gcp = GCPIdentity(
            impersonated_email="A@hello.org",
            department="A",
            jd_code="ENG002",
        )
        assert service.can_access_confidential(gcp, "ENG001") is False

    def test_case_insensitive(self, service):
        from app.models.user import GCPIdentity
        gcp = GCPIdentity(
            impersonated_email="A@hello.org",
            department="A",
            jd_code="eng001",
        )
        assert service.can_access_confidential(gcp, "ENG001") is True
