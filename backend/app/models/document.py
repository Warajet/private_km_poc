"""
Domain models for documents returned by Discovery Engine.
Mirrors the NDJSON/JSONL structure ingested into the datastore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models.user import AccessLevel


@dataclass
class AclPrincipal:
    user_id: Optional[str] = None
    group_id: Optional[str] = None


@dataclass
class AclReader:
    principals: List[AclPrincipal] = field(default_factory=list)


@dataclass
class AclInfo:
    """
    Access control information embedded in each document (matches Discovery Engine schema).

    NDJSON example:
    {
      "acl_info": {
        "readers": [
          {"principals": [{"userId": "A@hello.org"}]},
          {"principals": [{"groupId": "jd-eng001@hello.org"}]}
        ]
      }
    }
    """
    readers: List[AclReader] = field(default_factory=list)


@dataclass
class DocumentStructData:
    """
    Arbitrary metadata fields stored in structData of a Discovery Engine document.
    Only the fields relevant to the authorization matrix are typed here.
    """
    title: str = ""
    content: str = ""
    access_level: AccessLevel = AccessLevel.PUBLIC
    department: Optional[str] = None          # owning department code, e.g. "A"
    relate_id: Optional[str] = None           # relate-group ID if access_level==RELATE
    required_jd_code: Optional[str] = None    # JD code required for CONFIDENTIAL docs
    source_uri: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedDocument:
    """A document returned from a Discovery Engine search or conversation."""
    id: str
    struct_data: DocumentStructData
    acl_info: AclInfo = field(default_factory=AclInfo)
    relevance_score: float = 0.0
    snippet: str = ""
    uri: Optional[str] = None


# ── NDJSON ingestion format (for reference / import tooling) ──────────────────

NDJSON_TEMPLATE = {
    "id": "<unique-doc-id>",
    "structData": {
        "title": "<title>",
        "content": "<plain text content>",
        "access_level": "public | internal | relate | confidential",
        "department": "<dept-code>",
        "relate_id": "<relate-group-id>",
        "required_jd_code": "<JD-code>",
        "source_uri": "gs://<bucket>/<path>",
    },
    "content": {
        "mimeType": "text/html",
        "uri": "gs://<bucket>/<path>",
    },
    "acl_info": {
        "readers": [
            {"principals": [{"userId": "<dept>@<domain>"}]},
            {"principals": [{"groupId": "jd-<code>@<domain>"}]},
        ]
    },
}
