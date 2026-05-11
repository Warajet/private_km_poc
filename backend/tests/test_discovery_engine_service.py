"""
Unit tests for DiscoveryEngineService.answer_query() integration.

All GCP client calls are mocked; these tests verify:
  - Correct AnswerQueryRequest construction (filter, session, serving_config)
  - Reference parsing (ChunkInfo and UnstructuredDocumentInfo shapes)
  - AggregatedAnswer assembly (best answer selection, dedup, session update)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models.datastore import DatastoreTarget
from app.models.user import AccessLevel, GCPIdentity
from app.services.discovery_engine_service import (
    AggregatedAnswer,
    DiscoveryEngineService,
    TargetAnswer,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("WORKSPACE_DOMAIN", "hello.org")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    import app.config as cfg
    cfg._settings = None


@pytest.fixture()
def gcp_identity():
    return GCPIdentity(
        impersonated_email="A@hello.org",
        department="A",
        jd_code="ENG001",
        relate_groups=["ab"],
        original_hwc_email="userA@hello.org",
    )


@pytest.fixture()
def service(gcp_identity):
    with patch("app.services.discovery_engine_service.get_impersonated_credentials"):
        with patch("app.services.discovery_engine_service.discoveryengine.ConversationalSearchServiceClient"):
            svc = DiscoveryEngineService(gcp_identity=gcp_identity)
    return svc


# ── Filter construction ────────────────────────────────────────────────────────

class TestFilterConstruction:
    def test_public_has_no_filter(self, service):
        target = DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public")
        assert service._build_filter(target) == ""

    def test_internal_has_no_filter(self, service):
        target = DatastoreTarget("ds-internal-a", AccessLevel.INTERNAL, label="internal-A")
        assert service._build_filter(target) == ""

    def test_relate_has_no_filter(self, service):
        target = DatastoreTarget("ds-relate-ab", AccessLevel.RELATE, label="relate-ab")
        assert service._build_filter(target) == ""

    def test_confidential_includes_dept_and_jd(self, service):
        target = DatastoreTarget(
            "ds-conf", AccessLevel.CONFIDENTIAL, jd_code="ENG001", label="confidential"
        )
        f = service._build_filter(target)
        assert 'structData.department: ANY("A")' in f
        assert 'structData.required_jd_code: ANY("ENG001")' in f

    def test_confidential_without_jd_returns_safety_net_filter(self, service):
        target = DatastoreTarget(
            "ds-conf", AccessLevel.CONFIDENTIAL, jd_code="", label="confidential"
        )
        f = service._build_filter(target)
        # Safety net: matches nothing
        assert "__never__" in f


# ── Session path construction ──────────────────────────────────────────────────

class TestSessionPath:
    def test_auto_create_session_when_empty(self, service):
        path = service._resolve_session("ds-pub", "")
        assert path.endswith("/sessions/-")
        assert "ds-pub" in path
        assert "test-project" in path

    def test_continues_existing_session(self, service):
        existing = "projects/p/locations/global/collections/c/dataStores/ds/sessions/123"
        path = service._resolve_session("ds-pub", existing)
        assert path == existing


# ── Serving config path ────────────────────────────────────────────────────────

class TestServingConfigPath:
    def test_path_includes_all_segments(self, service):
        path = service._serving_config_path("my-datastore")
        assert "test-project" in path
        assert "my-datastore" in path
        assert "servingConfigs" in path


# ── Answer aggregation ─────────────────────────────────────────────────────────

class TestAggregation:
    def _make_doc(self, doc_id: str, score: float = 0.5):
        from app.models.document import DocumentStructData, RetrievedDocument
        return RetrievedDocument(
            id=doc_id,
            struct_data=DocumentStructData(title=f"Doc {doc_id}"),
            relevance_score=score,
        )

    def test_picks_answer_with_most_references(self, service):
        ta1 = TargetAnswer("ds-a", "a", "Short answer.", [self._make_doc("d1")], grounded=True)
        ta2 = TargetAnswer("ds-b", "b", "Longer answer with more refs.",
                           [self._make_doc("d2"), self._make_doc("d3"), self._make_doc("d4")],
                           grounded=True)
        result = service._aggregate([ta1, ta2])
        assert result.answer_text == "Longer answer with more refs."

    def test_merges_all_references(self, service):
        ta1 = TargetAnswer("ds-a", "a", "Answer A.", [self._make_doc("d1")], grounded=True)
        ta2 = TargetAnswer("ds-b", "b", "Answer B.", [self._make_doc("d2")], grounded=True)
        result = service._aggregate([ta1, ta2])
        doc_ids = {d.id for d in result.references}
        assert doc_ids == {"d1", "d2"}

    def test_deduplicates_references(self, service):
        same_doc = self._make_doc("d1", score=0.9)
        ta1 = TargetAnswer("ds-a", "a", "Answer.", [same_doc], grounded=True)
        ta2 = TargetAnswer("ds-b", "b", "Answer.", [same_doc], grounded=True)
        result = service._aggregate([ta1, ta2])
        assert len(result.references) == 1

    def test_references_sorted_by_relevance(self, service):
        ta = TargetAnswer(
            "ds-a", "a", "Answer.",
            [self._make_doc("low", 0.1), self._make_doc("high", 0.9), self._make_doc("mid", 0.5)],
            grounded=True,
        )
        result = service._aggregate([ta])
        scores = [d.relevance_score for d in result.references]
        assert scores == sorted(scores, reverse=True)

    def test_updates_session_names(self, service):
        ta1 = TargetAnswer("ds-a", "a", "Ans.", [], session_name="projects/p/sessions/s1")
        ta2 = TargetAnswer("ds-b", "b", "Ans.", [], session_name="projects/p/sessions/s2")
        result = service._aggregate([ta1, ta2])
        assert result.updated_de_sessions == {
            "ds-a": "projects/p/sessions/s1",
            "ds-b": "projects/p/sessions/s2",
        }

    def test_empty_targets_returns_empty_aggregated(self, service):
        result = service._aggregate([])
        assert result.answer_text == ""
        assert result.references == []
        assert result.grounded is False

    def test_no_grounded_answers_uses_ungrounded_text(self, service):
        ta = TargetAnswer("ds-a", "a", "Partial answer.", [], grounded=False)
        result = service._aggregate([ta])
        assert result.answer_text == "Partial answer."
        assert result.grounded is False

    def test_grounded_flag_set_when_references_exist(self, service):
        ta = TargetAnswer("ds-a", "a", "Answer.", [self._make_doc("d1")], grounded=True)
        result = service._aggregate([ta])
        assert result.grounded is True
