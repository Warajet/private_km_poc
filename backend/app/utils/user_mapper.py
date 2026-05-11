"""
Maps HWC (Huawei Cloud) user identities to GCP identities.

The mapping can be provided three ways (checked in order):
1. A JSON file at settings.user_mapping_file
2. An in-process dict (tests / local dev)
3. A Google Directory API lookup (production – requires Admin SDK scope)

Mapping JSON structure:
{
  "userA@hello.org": {
    "department": "A",
    "jd_code": "ENG001",
    "display_name": "User A",
    "relate_groups": ["ab"]
  },
  "userB@hello.org": {
    "department": "A",
    "jd_code": "ENG002",
    "display_name": "User B",
    "relate_groups": []
  }
}
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from app.models.user import GCPIdentity, HWCUser

logger = logging.getLogger(__name__)

# In-process cache – keyed by HWC email (lower-cased)
_mapping_cache: Dict[str, HWCUser] = {}


def load_mapping_from_file(path: str) -> None:
    """Load (or reload) the user↔department mapping from a JSON file."""
    mapping_path = Path(path)
    if not mapping_path.exists():
        logger.warning("User mapping file not found: %s", path)
        return

    with mapping_path.open() as fh:
        raw: Dict[str, dict] = json.load(fh)

    _mapping_cache.clear()
    for email, attrs in raw.items():
        _mapping_cache[email.lower()] = HWCUser(
            email=email.lower(),
            department=attrs["department"],
            jd_code=attrs.get("jd_code", ""),
            display_name=attrs.get("display_name", ""),
            relate_groups=attrs.get("relate_groups", []),
        )

    logger.info("Loaded %d user mappings from %s", len(_mapping_cache), path)


def register_user(
    email: str,
    department: str,
    jd_code: str,
    display_name: str = "",
    relate_groups: Optional[list] = None,
) -> None:
    """Register a single user mapping (useful for tests and seeding)."""
    _mapping_cache[email.lower()] = HWCUser(
        email=email.lower(),
        department=department,
        jd_code=jd_code,
        display_name=display_name,
        relate_groups=relate_groups or [],
    )


def resolve_hwc_user(email: str) -> Optional[HWCUser]:
    """Return the HWCUser for the given email, or None if not found."""
    return _mapping_cache.get(email.lower())


def resolve_gcp_identity(hwc_user: HWCUser, workspace_domain: str) -> GCPIdentity:
    """
    Derive the GCP identity the service account will impersonate.

    Multiple HWC users in the same department share one GCP department email:
      userA@hello.org  (dept A)  →  A@hello.org
      userB@hello.org  (dept A)  →  A@hello.org
    """
    dept_email = f"{hwc_user.department.upper()}@{workspace_domain}"
    return GCPIdentity(
        impersonated_email=dept_email,
        department=hwc_user.department,
        jd_code=hwc_user.jd_code,
        relate_groups=hwc_user.relate_groups,
        original_hwc_email=hwc_user.email,
    )
