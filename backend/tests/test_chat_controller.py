"""Integration tests for ChatController (all external services mocked)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.controllers.chat_controller import ChatController
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.datastore import DatastoreRegistry, DatastoreTarget
from app.models.document import DocumentStructData, RetrievedDocument
from app.models.user import AccessLevel, GCPIdentity, HWCUser, UserContext
from app.schemas.chat import ChatRequest


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("WORKSPACE_DOMAIN", "hello.org")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    import app.config as cfg
    cfg._settings = None


@pytest.fixture()
def user_context():
    hwc = HWCUser(
        email="userA@hello.org",
        department="A",
        jd_code="ENG001",
        display_name="User A",
        relate_groups=["ab"],
    )
    gcp = GCPIdentity(
        impersonated_email="A@hello.org",
        department="A",
        jd_code="ENG001",
        relate_groups=["ab"],
        original_hwc_email="userA@hello.org",
    )
    return UserContext(hwc_user=hwc, gcp_identity=gcp)


@pytest.fixture()
def mock_session():
    return ChatSession(
        id="session-123",
        user_email="userA@hello.org",
        department="A",
        jd_code="ENG001",
    )


@pytest.fixture()
def mock_session_service(mock_session):
    svc = MagicMock()
    svc.get_or_create_session.return_value = mock_session

    def _make_msg(session, content, sources=None):
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            timestamp=datetime.utcnow(),
            sources=sources or [],
        )

    svc.append_assistant_message.side_effect = _make_msg
    return svc


@pytest.fixture()
def mock_registry():
    return DatastoreRegistry(
        public_datastore_id="ds-public",
        internal_datastores={"A": "ds-internal-a"},
        relate_datastores={"ab": "ds-relate-ab"},
        confidential_datastore_id="ds-confidential",
    )


@pytest.fixture()
def retrieved_docs():
    return [
        RetrievedDocument(
            id="doc-001",
            struct_data=DocumentStructData(
                title="Test Document",
                access_level=AccessLevel.INTERNAL,
                department="A",
            ),
            snippet="Relevant snippet text.",
            relevance_score=0.95,
        )
    ]


class TestChatController:
    def _patch_all(self, mock_session_service, mock_registry, retrieved_docs, answer="Here is the answer."):
        de_patch = patch("app.controllers.chat_controller.DiscoveryEngineService")
        gemini_patch = patch("app.controllers.chat_controller.GeminiService")
        routing_patch = patch("app.controllers.chat_controller.DatastoreRoutingService")

        return de_patch, gemini_patch, routing_patch

    def test_chat_returns_response(
        self, user_context, mock_session_service, mock_registry, retrieved_docs
    ):
        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.GeminiService") as MockGemini,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
                DatastoreTarget("ds-internal-a", AccessLevel.INTERNAL, label="internal-A"),
            ]
            MockDE.return_value.search_targets.return_value = retrieved_docs
            MockDE.return_value.credentials = MagicMock()
            MockGemini.return_value.generate.return_value = "Here is the answer."

            controller = ChatController(session_service=mock_session_service)
            response = controller.chat(
                ChatRequest(message="What is the policy?"),
                user_context,
            )

        assert response.session_id == "session-123"
        assert response.message.content == "Here is the answer."
        assert response.grounded is True
        assert len(response.message.sources) == 1
        assert response.message.sources[0].title == "Test Document"
        assert response.message.sources[0].access_level == "internal"

    def test_chat_queries_correct_number_of_targets(
        self, user_context, mock_session_service, retrieved_docs
    ):
        """Routing service resolves 4 targets; DE receives all 4."""
        four_targets = [
            DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
            DatastoreTarget("ds-internal-a", AccessLevel.INTERNAL, label="internal-A"),
            DatastoreTarget("ds-relate-ab", AccessLevel.RELATE, label="relate-ab"),
            DatastoreTarget("ds-confidential", AccessLevel.CONFIDENTIAL, jd_code="ENG001", label="confidential"),
        ]
        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.GeminiService") as MockGemini,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = four_targets
            MockDE.return_value.search_targets.return_value = retrieved_docs
            MockDE.return_value.credentials = MagicMock()
            MockGemini.return_value.generate.return_value = "Answer."

            controller = ChatController(session_service=mock_session_service)
            controller.chat(ChatRequest(message="Tell me everything"), user_context)

            call_targets = MockDE.return_value.search_targets.call_args[1]["targets"]
            assert len(call_targets) == 4

    def test_chat_fallback_message_when_no_docs_retrieved(
        self, user_context, mock_session_service
    ):
        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.GeminiService") as MockGemini,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
            ]
            MockDE.return_value.search_targets.return_value = []
            MockDE.return_value.credentials = MagicMock()
            MockGemini.return_value.generate.return_value = (
                "No relevant information found in your authorised documents."
            )

            controller = ChatController(session_service=mock_session_service)
            response = controller.chat(
                ChatRequest(message="Unanswerable?"), user_context
            )

        assert response.grounded is False
        assert len(response.message.sources) == 0

    def test_registry_not_initialised_returns_503(
        self, user_context, mock_session_service
    ):
        with patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting:
            MockRouting.return_value.resolve_targets.side_effect = RuntimeError(
                "DatastoreRegistry not initialised"
            )
            controller = ChatController(session_service=mock_session_service)
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                controller.chat(ChatRequest(message="Hello"), user_context)
            assert exc_info.value.status_code == 503

    def test_de_failure_returns_502(self, user_context, mock_session_service):
        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
            ]
            MockDE.return_value.search_targets.side_effect = Exception("GCP unavailable")

            controller = ChatController(session_service=mock_session_service)
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc_info:
                controller.chat(ChatRequest(message="Hello"), user_context)
            assert exc_info.value.status_code == 502
