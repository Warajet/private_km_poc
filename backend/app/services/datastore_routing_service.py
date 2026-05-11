"""
DatastoreRoutingService — Layer 1 of the 2-layer access-control model.

Given a UserContext this service answers the question:
  "Which Discovery Engine datastores is this user allowed to even query?"

The answer is a list of DatastoreTarget objects, one per accessible bucket.
The DiscoveryEngineService then queries each target in parallel (Layer 2 ACL
is enforced inside each datastore by Discovery Engine itself).

Routing rules
─────────────
Bucket        Gate                          Impersonation identity used
────────────  ────────────────────────────  ─────────────────────────────────
PUBLIC        always included               SA default / ADC (no subject)
INTERNAL      user's own department only    <dept>@<domain>  (e.g. A@hello.org)
RELATE        user's relate-group IDs       <dept>@<domain>  (must be GWS member
              that exist in the registry    of relate-<id>@<domain> group)
CONFIDENTIAL  user has a non-empty jd_code  <dept>@<domain>  + JD filter in query
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from app.models.datastore import DatastoreRegistry, DatastoreTarget
from app.models.user import AccessLevel, UserContext

logger = logging.getLogger(__name__)

_registry: Optional[DatastoreRegistry] = None


# ── Registry lifecycle ─────────────────────────────────────────────────────────

def load_registry_from_file(path: str) -> DatastoreRegistry:
    """Load (or reload) the DatastoreRegistry from a JSON file."""
    global _registry
    registry_path = Path(path)
    if not registry_path.exists():
        raise FileNotFoundError(f"Datastore registry file not found: {path}")
    with registry_path.open() as fh:
        data = json.load(fh)
    _registry = DatastoreRegistry.from_dict(data)
    logger.info(
        "Loaded datastore registry: public=%s internal=%d relate=%d confidential=%s",
        _registry.public_datastore_id,
        len(_registry.internal_datastores),
        len(_registry.relate_datastores),
        _registry.confidential_datastore_id or "none",
    )
    return _registry


def set_registry(registry: DatastoreRegistry) -> None:
    """Register a registry directly (useful for tests and seeding)."""
    global _registry
    _registry = registry


def get_registry() -> DatastoreRegistry:
    if _registry is None:
        raise RuntimeError(
            "DatastoreRegistry not initialised. "
            "Call load_registry_from_file() at startup or set DATASTORE_REGISTRY_FILE."
        )
    return _registry


# ── Routing ────────────────────────────────────────────────────────────────────

class DatastoreRoutingService:
    """
    Stateless service that maps a UserContext to the list of DatastoreTargets
    to query.  No I/O; pure routing logic only.
    """

    def __init__(self, registry: Optional[DatastoreRegistry] = None) -> None:
        self._registry = registry or get_registry()

    def resolve_targets(self, user_context: UserContext) -> List[DatastoreTarget]:
        """
        Return the ordered list of DatastoreTargets for this user.

        Order: PUBLIC → INTERNAL → RELATE → CONFIDENTIAL
        The order is cosmetic; all are queried in parallel.
        """
        targets: List[DatastoreTarget] = []
        gcp = user_context.gcp_identity

        # ── 1. PUBLIC bucket (always) ──────────────────────────────────────────
        targets.append(DatastoreTarget(
            datastore_id=self._registry.public_datastore_id,
            access_level=AccessLevel.PUBLIC,
            label="public",
        ))
        logger.debug("Route: PUBLIC → %s", self._registry.public_datastore_id)

        # ── 2. INTERNAL bucket (user's department only) ────────────────────────
        dept = gcp.department.upper()
        internal_id = self._registry.get_internal(dept)
        if internal_id:
            targets.append(DatastoreTarget(
                datastore_id=internal_id,
                access_level=AccessLevel.INTERNAL,
                label=f"internal-{dept}",
            ))
            logger.debug("Route: INTERNAL[%s] → %s", dept, internal_id)
        else:
            logger.debug("Route: INTERNAL[%s] → no datastore registered", dept)

        # ── 3. RELATE buckets (only groups user belongs to) ───────────────────
        for relate_id in gcp.relate_groups:
            relate_ds_id = self._registry.get_relate(relate_id)
            if relate_ds_id:
                targets.append(DatastoreTarget(
                    datastore_id=relate_ds_id,
                    access_level=AccessLevel.RELATE,
                    label=f"relate-{relate_id}",
                ))
                logger.debug("Route: RELATE[%s] → %s", relate_id, relate_ds_id)
            else:
                logger.debug("Route: RELATE[%s] → no datastore registered", relate_id)

        # ── 4. CONFIDENTIAL bucket (only if user has a JD code) ───────────────
        if gcp.jd_code and self._registry.confidential_datastore_id:
            targets.append(DatastoreTarget(
                datastore_id=self._registry.confidential_datastore_id,
                access_level=AccessLevel.CONFIDENTIAL,
                jd_code=gcp.jd_code,
                label=f"confidential-jd:{gcp.jd_code}",
            ))
            logger.debug(
                "Route: CONFIDENTIAL (jd=%s) → %s",
                gcp.jd_code,
                self._registry.confidential_datastore_id,
            )
        else:
            logger.debug(
                "Route: CONFIDENTIAL → skipped (jd_code=%r, ds=%r)",
                gcp.jd_code,
                self._registry.confidential_datastore_id,
            )

        logger.info(
            "Routing for %s → %d datastores: %s",
            gcp.original_hwc_email,
            len(targets),
            [t.label for t in targets],
        )
        return targets

    def describe_access(self, user_context: UserContext) -> dict:
        """Return a debug-friendly summary of what the user can access."""
        targets = self.resolve_targets(user_context)
        return {
            "user": user_context.hwc_user.email,
            "gcp_identity": user_context.gcp_identity.impersonated_email,
            "accessible_buckets": [
                {"label": t.label, "datastore_id": t.datastore_id, "access_level": t.access_level.value}
                for t in targets
            ],
        }
