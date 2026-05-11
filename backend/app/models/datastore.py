"""
Datastore bucket models for the 2-layer access-control architecture.

Layer 1 – API routing (this file):
  The DatastoreRegistry maps each access-level bucket to one or more
  Discovery Engine datastore IDs. DatastoreRoutingService uses the user's
  identity to decide WHICH datastores to query before a single byte of content
  is ever retrieved.

  Bucket layout
  ─────────────────────────────────────────────────────────────────────────────
  PUBLIC         1 datastore   – queried for every authenticated user
  INTERNAL       N datastores  – one per department; only the user's own is queried
  RELATE         K datastores  – one per cross-dept relationship (AB, AC, ABC …);
                                 only those the user belongs to are queried
  CONFIDENTIAL   1 datastore   – queried only if the user carries a JD code;
                                 a JD-code filter is applied at query time

Layer 2 – Discovery Engine ACL (acl_info on each document):
  After the API selects the target datastores, the Discovery Engine itself
  enforces document-level ACL by evaluating acl_info against the impersonated
  GCP identity.  The two layers are independent and both must pass.

Bucket  │ API gate (Layer 1)              │ ACL gate (Layer 2)
────────┼─────────────────────────────────┼────────────────────────────────────
PUBLIC  │ always                          │ no acl_info (open)
INTERNAL│ user's dept datastore only      │ userId: <dept>@<domain>
RELATE  │ user's relate-group datastores  │ groupId: relate-<id>@<domain>
CONFID. │ user has jd_code                │ userId: <dept>@<domain> +
        │                                 │   structData.required_jd_code filter
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.models.user import AccessLevel


@dataclass
class DatastoreTarget:
    """
    A single Discovery Engine datastore that should be queried for this request.
    Each target carries the context needed to build the right search request.
    """
    datastore_id: str
    access_level: AccessLevel
    # For CONFIDENTIAL targets: the JD code to embed in the filter expression
    jd_code: Optional[str] = None
    # Human-readable label (dept code, relate-group id, etc.)
    label: str = ""


@dataclass
class DatastoreRegistry:
    """
    Loaded once at startup from a JSON file (or env vars) and held as a
    singleton.  Maps bucket types to Discovery Engine datastore IDs.

    JSON schema
    ───────────
    {
      "public": "public-datastore-id",
      "internal": {
        "A": "internal-dept-a-id",
        "B": "internal-dept-b-id"
      },
      "relate": {
        "ab":  "relate-ab-datastore-id",
        "abc": "relate-abc-datastore-id"
      },
      "confidential": "confidential-datastore-id"
    }
    """
    public_datastore_id: str
    # dept_code (uppercase) → datastore_id
    internal_datastores: Dict[str, str] = field(default_factory=dict)
    # relate_id (lowercase)  → datastore_id
    relate_datastores: Dict[str, str] = field(default_factory=dict)
    confidential_datastore_id: Optional[str] = None

    # ── Factory ────────────────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict) -> "DatastoreRegistry":
        return cls(
            public_datastore_id=data["public"],
            internal_datastores={k.upper(): v for k, v in data.get("internal", {}).items()},
            relate_datastores={k.lower(): v for k, v in data.get("relate", {}).items()},
            confidential_datastore_id=data.get("confidential"),
        )

    # ── Accessors ──────────────────────────────────────────────────────────────

    def get_internal(self, department: str) -> Optional[str]:
        return self.internal_datastores.get(department.upper())

    def get_relate(self, relate_id: str) -> Optional[str]:
        return self.relate_datastores.get(relate_id.lower())

    def all_datastore_ids(self) -> List[str]:
        ids = [self.public_datastore_id]
        ids.extend(self.internal_datastores.values())
        ids.extend(self.relate_datastores.values())
        if self.confidential_datastore_id:
            ids.append(self.confidential_datastore_id)
        return ids
