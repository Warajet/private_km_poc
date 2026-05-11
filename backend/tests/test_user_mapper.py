"""Unit tests for user mapping and identity resolution."""

from __future__ import annotations

import pytest

from app.utils.user_mapper import register_user, resolve_gcp_identity, resolve_hwc_user


@pytest.fixture(autouse=True)
def seed_users():
    register_user(
        email="userA@hello.org",
        department="A",
        jd_code="ENG001",
        display_name="User A",
        relate_groups=["ab"],
    )
    register_user(
        email="userB@hello.org",
        department="A",
        jd_code="ENG002",
        display_name="User B",
        relate_groups=[],
    )
    register_user(
        email="userC@hello.org",
        department="B",
        jd_code="MKT001",
        display_name="User C",
        relate_groups=["ab"],
    )


def test_resolve_known_user():
    user = resolve_hwc_user("userA@hello.org")
    assert user is not None
    assert user.department == "A"
    assert user.jd_code == "ENG001"


def test_resolve_unknown_user():
    user = resolve_hwc_user("unknown@hello.org")
    assert user is None


def test_case_insensitive_lookup():
    user = resolve_hwc_user("USERA@HELLO.ORG")
    assert user is not None
    assert user.email == "usera@hello.org"


def test_gcp_identity_department_email():
    """Multiple HWC users in same department map to the same GCP email."""
    userA = resolve_hwc_user("userA@hello.org")
    userB = resolve_hwc_user("userB@hello.org")

    gcp_a = resolve_gcp_identity(userA, "hello.org")
    gcp_b = resolve_gcp_identity(userB, "hello.org")

    assert gcp_a.impersonated_email == "A@hello.org"
    assert gcp_b.impersonated_email == "A@hello.org"


def test_gcp_identity_preserves_jd_code():
    userA = resolve_hwc_user("userA@hello.org")
    gcp_a = resolve_gcp_identity(userA, "hello.org")
    assert gcp_a.jd_code == "ENG001"
    assert gcp_a.original_hwc_email == "usera@hello.org"


def test_different_departments_have_different_gcp_emails():
    userA = resolve_hwc_user("userA@hello.org")
    userC = resolve_hwc_user("userC@hello.org")

    gcp_a = resolve_gcp_identity(userA, "hello.org")
    gcp_c = resolve_gcp_identity(userC, "hello.org")

    assert gcp_a.impersonated_email != gcp_c.impersonated_email
    assert gcp_c.impersonated_email == "B@hello.org"
