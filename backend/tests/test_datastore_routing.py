"""
Unit tests for DatastoreRoutingService — Layer 1 access-control logic.

These tests verify the routing rules without any GCP calls.
"""

from __future__ import annotations

import pytest

from app.models.datastore import DatastoreRegistry, DatastoreTarget
from app.models.user import AccessLevel, GCPIdentity, HWCUser, UserContext
from app.services.datastore_routing_service import DatastoreRoutingService


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def registry():
    return DatastoreRegistry(
        public_datastore_id="ds-public",
        internal_datastores={"A": "ds-internal-a", "B": "ds-internal-b"},
        relate_datastores={"ab": "ds-relate-ab", "abc": "ds-relate-abc"},
        confidential_datastore_id="ds-confidential",
    )


@pytest.fixture()
def routing(registry):
    return DatastoreRoutingService(registry=registry)


def _make_context(department: str, jd_code: str = "", relate_groups=None) -> UserContext:
    hwc = HWCUser(
        email=f"user@hello.org",
        department=department,
        jd_code=jd_code,
        relate_groups=relate_groups or [],
    )
    gcp = GCPIdentity(
        impersonated_email=f"{department.upper()}@hello.org",
        department=department,
        jd_code=jd_code,
        relate_groups=relate_groups or [],
        original_hwc_email=f"user@hello.org",
    )
    return UserContext(hwc_user=hwc, gcp_identity=gcp)


# ── Public bucket ──────────────────────────────────────────────────────────────

class TestPublicBucket:
    def test_always_included_for_any_user(self, routing):
        ctx = _make_context("A")
        targets = routing.resolve_targets(ctx)
        public = [t for t in targets if t.access_level == AccessLevel.PUBLIC]
        assert len(public) == 1
        assert public[0].datastore_id == "ds-public"

    def test_included_even_with_no_jd_code(self, routing):
        ctx = _make_context("A", jd_code="")
        targets = routing.resolve_targets(ctx)
        assert any(t.access_level == AccessLevel.PUBLIC for t in targets)


# ── Internal bucket ────────────────────────────────────────────────────────────

class TestInternalBucket:
    def test_includes_own_department(self, routing):
        ctx = _make_context("A")
        targets = routing.resolve_targets(ctx)
        internal = [t for t in targets if t.access_level == AccessLevel.INTERNAL]
        assert len(internal) == 1
        assert internal[0].datastore_id == "ds-internal-a"

    def test_excludes_other_departments(self, routing):
        ctx = _make_context("A")
        targets = routing.resolve_targets(ctx)
        ds_ids = {t.datastore_id for t in targets}
        assert "ds-internal-b" not in ds_ids

    def test_dept_b_gets_b_datastore(self, routing):
        ctx = _make_context("B")
        targets = routing.resolve_targets(ctx)
        internal = [t for t in targets if t.access_level == AccessLevel.INTERNAL]
        assert internal[0].datastore_id == "ds-internal-b"

    def test_unknown_department_has_no_internal(self, routing):
        ctx = _make_context("Z")  # Z not in registry
        targets = routing.resolve_targets(ctx)
        internal = [t for t in targets if t.access_level == AccessLevel.INTERNAL]
        assert len(internal) == 0

    def test_case_insensitive_department(self, routing):
        ctx = _make_context("a")  # lowercase
        targets = routing.resolve_targets(ctx)
        internal = [t for t in targets if t.access_level == AccessLevel.INTERNAL]
        assert len(internal) == 1
        assert internal[0].datastore_id == "ds-internal-a"


# ── Relate buckets ─────────────────────────────────────────────────────────────

class TestRelateBuckets:
    def test_includes_relate_groups_user_belongs_to(self, routing):
        ctx = _make_context("A", relate_groups=["ab"])
        targets = routing.resolve_targets(ctx)
        relate = [t for t in targets if t.access_level == AccessLevel.RELATE]
        assert len(relate) == 1
        assert relate[0].datastore_id == "ds-relate-ab"

    def test_includes_multiple_relate_groups(self, routing):
        ctx = _make_context("A", relate_groups=["ab", "abc"])
        targets = routing.resolve_targets(ctx)
        relate = [t for t in targets if t.access_level == AccessLevel.RELATE]
        ds_ids = {t.datastore_id for t in relate}
        assert ds_ids == {"ds-relate-ab", "ds-relate-abc"}

    def test_excludes_relate_groups_not_belonging_to(self, routing):
        ctx = _make_context("A", relate_groups=[])  # no relate groups
        targets = routing.resolve_targets(ctx)
        relate = [t for t in targets if t.access_level == AccessLevel.RELATE]
        assert len(relate) == 0

    def test_unregistered_relate_group_skipped(self, routing):
        ctx = _make_context("A", relate_groups=["xy"])  # xy not in registry
        targets = routing.resolve_targets(ctx)
        relate = [t for t in targets if t.access_level == AccessLevel.RELATE]
        assert len(relate) == 0


# ── Confidential bucket ────────────────────────────────────────────────────────

class TestConfidentialBucket:
    def test_included_when_jd_code_present(self, routing):
        ctx = _make_context("A", jd_code="ENG001")
        targets = routing.resolve_targets(ctx)
        conf = [t for t in targets if t.access_level == AccessLevel.CONFIDENTIAL]
        assert len(conf) == 1
        assert conf[0].datastore_id == "ds-confidential"
        assert conf[0].jd_code == "ENG001"

    def test_excluded_when_no_jd_code(self, routing):
        ctx = _make_context("A", jd_code="")
        targets = routing.resolve_targets(ctx)
        conf = [t for t in targets if t.access_level == AccessLevel.CONFIDENTIAL]
        assert len(conf) == 0

    def test_excluded_when_no_confidential_datastore_in_registry(self):
        registry_no_conf = DatastoreRegistry(
            public_datastore_id="ds-public",
            internal_datastores={"A": "ds-internal-a"},
            confidential_datastore_id=None,
        )
        routing = DatastoreRoutingService(registry=registry_no_conf)
        ctx = _make_context("A", jd_code="ENG001")
        targets = routing.resolve_targets(ctx)
        conf = [t for t in targets if t.access_level == AccessLevel.CONFIDENTIAL]
        assert len(conf) == 0


# ── Full routing scenario ──────────────────────────────────────────────────────

class TestFullScenario:
    def test_dept_a_member_with_relate_and_jd(self, routing):
        """userA@hello.org: Dept A, JD ENG001, relate-ab group."""
        ctx = _make_context("A", jd_code="ENG001", relate_groups=["ab"])
        targets = routing.resolve_targets(ctx)
        levels = {t.access_level for t in targets}
        assert levels == {
            AccessLevel.PUBLIC,
            AccessLevel.INTERNAL,
            AccessLevel.RELATE,
            AccessLevel.CONFIDENTIAL,
        }
        assert len(targets) == 4

    def test_dept_b_no_relate_no_jd(self, routing):
        """userD@hello.org: Dept B, no JD, no relate groups."""
        ctx = _make_context("B", jd_code="", relate_groups=[])
        targets = routing.resolve_targets(ctx)
        levels = {t.access_level for t in targets}
        assert levels == {AccessLevel.PUBLIC, AccessLevel.INTERNAL}
        assert len(targets) == 2

    def test_ordering_public_first(self, routing):
        ctx = _make_context("A", jd_code="ENG001", relate_groups=["ab"])
        targets = routing.resolve_targets(ctx)
        assert targets[0].access_level == AccessLevel.PUBLIC

    def test_no_duplicate_datastores(self, routing):
        ctx = _make_context("A", jd_code="ENG001", relate_groups=["ab", "abc"])
        targets = routing.resolve_targets(ctx)
        ds_ids = [t.datastore_id for t in targets]
        assert len(ds_ids) == len(set(ds_ids))


# ── DatastoreRegistry helpers ──────────────────────────────────────────────────

class TestDatastoreRegistry:
    def test_from_dict(self):
        data = {
            "public": "pub",
            "internal": {"a": "int-a", "B": "int-b"},
            "relate": {"AB": "rel-ab"},
            "confidential": "conf",
        }
        reg = DatastoreRegistry.from_dict(data)
        assert reg.public_datastore_id == "pub"
        assert reg.get_internal("a") == "int-a"   # normalised to upper
        assert reg.get_internal("B") == "int-b"
        assert reg.get_relate("ab") == "rel-ab"   # normalised to lower
        assert reg.confidential_datastore_id == "conf"

    def test_all_datastore_ids(self, registry):
        ids = registry.all_datastore_ids()
        assert "ds-public" in ids
        assert "ds-internal-a" in ids
        assert "ds-confidential" in ids
        assert len(ids) == 6  # 1 public + 2 internal + 2 relate + 1 confidential
