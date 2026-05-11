"""
Unit tests for DiscoveryEngineService.answer_query() with DataStoreSpecs.

All GCP client calls are mocked; these tests verify:
  - Correct DataStoreSpec construction (filters, datastore resource names)
  - Filter logic (CONFIDENTIAL gets explicit filter; others rely on ACL)
  - Response parsing (ChunkInfo and UnstructuredDocumentInfo shapes)
  - AnswerResult assembly (answer text, references, session name)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models.datastore import DatastoreTarget
from app.models.user import AccessLevel, GCPIdentity
from app.services.discovery_engine_service import AnswerResult, DiscoveryEngineService


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("DISCOVERY_ENGINE_ENGINE_ID", "test-engine")
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
        assert "__never__" in f


# ── DataStoreSpec construction ─────────────────────────────────────────────────

class TestDataStoreSpecConstruction:
    def test_spec_count_matches_targets(self, service):
        targets = [
            DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public"),
            DatastoreTarget("ds-int", AccessLevel.INTERNAL, label="internal"),
        ]
        specs = service._build_data_store_specs(targets)
        assert len(specs) == 2

    def test_spec_has_full_resource_name(self, service):
        targets = [DatastoreTarget("my-ds", AccessLevel.PUBLIC, label="public")]
        specs = service._build_data_store_specs(targets)
        assert "test-project" in specs[0].data_store
        assert "my-ds" in specs[0].data_store

    def test_public_spec_has_no_filter(self, service):
        targets = [DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public")]
        specs = service._build_data_store_specs(targets)
        assert not specs[0].filter

    def test_confidential_spec_has_filter(self, service):
        from google.cloud import discoveryengine_v1 as de
        with patch("app.services.discovery_engine_service.discoveryengine", de):
            targets = [DatastoreTarget("ds-conf", AccessLevel.CONFIDENTIAL, jd_code="ENG001", label="conf")]
            specs = service._build_data_store_specs(targets)
            assert "ENG001" in specs[0].filter


# ── Session path ───────────────────────────────────────────────────────────────

class TestSessionPath:
    def test_engine_session_auto_path_ends_with_dash(self, service):
        path = service._settings.engine_session_auto_path()
        assert path.endswith("/sessions/-")
        assert "test-engine" in path
        assert "test-project" in path

    def test_answer_query_uses_existing_session(self, service):
        """When existing_session is provided it is passed straight to the request."""
        existing = "projects/p/locations/global/collections/c/engines/e/sessions/123"
        targets = [DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public")]

        captured = {}

        def fake_answer_query(request):
            captured["session"] = request.session
            mock_resp = MagicMock()
            mock_resp.answer = None
            mock_resp.session = existing
            return mock_resp

        service._client.answer_query = fake_answer_query
        service.answer_query("hello", targets, existing_session=existing)
        assert captured["session"] == existing

    def test_answer_query_auto_creates_session_when_empty(self, service):
        targets = [DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public")]

        captured = {}

        def fake_answer_query(request):
            captured["session"] = request.session
            mock_resp = MagicMock()
            mock_resp.answer = None
            mock_resp.session = "projects/p/engines/e/sessions/new"
            return mock_resp

        service._client.answer_query = fake_answer_query
        service.answer_query("hello", targets, existing_session="")
        assert captured["session"].endswith("/sessions/-")


# ── AnswerResult assembly ──────────────────────────────────────────────────────

class TestAnswerResult:
    def _mock_response(self, answer_text="", session_name="projects/p/sessions/s1"):
        resp = MagicMock()
        if answer_text:
            resp.answer = MagicMock()
            resp.answer.answer_text = answer_text
            resp.answer.references = []
        else:
            resp.answer = None
        resp.session = session_name
        return resp

    def test_empty_targets_returns_empty_result(self, service):
        result = service.answer_query("q", targets=[])
        assert result.answer_text == ""
        assert result.session_name == ""
        assert result.references == []
        assert result.grounded is False

    def test_answer_text_extracted(self, service):
        resp = self._mock_response(answer_text="The answer.")
        service._client.answer_query = MagicMock(return_value=resp)
        targets = [DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public")]
        result = service.answer_query("q", targets)
        assert result.answer_text == "The answer."

    def test_session_name_extracted(self, service):
        resp = self._mock_response(answer_text="A.", session_name="projects/p/sessions/s99")
        service._client.answer_query = MagicMock(return_value=resp)
        targets = [DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public")]
        result = service.answer_query("q", targets)
        assert result.session_name == "projects/p/sessions/s99"

    def test_grounded_false_when_no_references(self, service):
        resp = self._mock_response(answer_text="A.")
        service._client.answer_query = MagicMock(return_value=resp)
        targets = [DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public")]
        result = service.answer_query("q", targets)
        assert result.grounded is False

    def test_engine_serving_config_used_in_request(self, service):
        captured = {}

        def fake_answer_query(request):
            captured["serving_config"] = request.serving_config
            mock_resp = MagicMock()
            mock_resp.answer = None
            mock_resp.session = ""
            return mock_resp

        service._client.answer_query = fake_answer_query
        targets = [DatastoreTarget("ds-pub", AccessLevel.PUBLIC, label="public")]
        service.answer_query("q", targets)
        assert "test-engine" in captured["serving_config"]
        assert "servingConfigs" in captured["serving_config"]
