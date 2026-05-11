"""
Domain models for users and identity.
These are internal data structures, not Pydantic schemas for the API layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class AccessLevel(str, Enum):
    """Authorization matrix levels for document access."""
    PUBLIC = "public"
    INTERNAL = "internal"
    RELATE = "relate"
    CONFIDENTIAL = "confidential"


@dataclass
class HWCUser:
    """
    A user as known by the HWC (Huawei Cloud) identity system.
    Multiple HWC users from the same department share a single GCP identity.
    """
    email: str           # e.g. userA@hello.org
    department: str      # e.g. "A"
    jd_code: str         # e.g. "ENG001"
    display_name: str = ""
    relate_groups: List[str] = field(default_factory=list)  # relate-group IDs

    @property
    def gcp_department_email(self) -> str:
        """The GCP impersonation target for this user's department."""
        domain = self.email.split("@", 1)[1]
        return f"{self.department.upper()}@{domain}"


@dataclass
class GCPIdentity:
    """
    The resolved GCP identity used when calling Discovery Engine.
    The service account impersonates this identity via Domain-wide Delegation.
    """
    impersonated_email: str     # e.g. A@hello.org
    department: str
    jd_code: str
    relate_groups: List[str] = field(default_factory=list)

    # Original HWC user for audit logging
    original_hwc_email: str = ""


@dataclass
class UserContext:
    """
    Full context for a single API request: the resolved HWC and GCP identities.
    Passed through the controller → service layer.
    """
    hwc_user: HWCUser
    gcp_identity: GCPIdentity
    session_id: Optional[str] = None
