"""Integration tests for ChatController (services mocked)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.controllers.chat_controller import ChatController
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.document import DocumentStructData, RetrievedDocument
from app.models.user import AccessLevel, GCPIdentity, HWCUser, UserContext
from app.schemas.chat import ChatRequest


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """Provide required env vars so Settings() can be constructed."""
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("DISCOVERY_ENGINE_DATASTORE_ID", "test-store")
    monkeypatch.setenv("WORKSPACE_DOMAIN", "hello.org")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    # Reset cached singleton so each test gets a fresh Settings
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
        de_conversation_name="projects/p/locations/l/conversations/c1",
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
def retrieved_doc():
    return RetrievedDocument(
        id="doc-001",
        struct_data=DocumentStructData(
            title="Test Document",
            access_level=AccessLevel.INTERNAL,
            department="A",
        ),
        snippet="Relevant snippet text.",
        relevance_score=0.95,
    )


class TestChatController:
    def test_chat_returns_response(
        self, user_context, mock_session_service, retrieved_doc
    ):
        with patch(
            "app.controllers.chat_controller.DiscoveryEngineService"
        ) as MockDE:
            de_instance = MockDE.return_value
            de_instance.converse.return_value = ("Here is the answer.", [retrieved_doc])

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

    def test_chat_creates_conversation_if_missing(
        self, user_context, mock_session_service
    ):
        mock_session_service.get_or_create_session.return_value.de_conversation_name = None

        with patch(
            "app.controllers.chat_controller.DiscoveryEngineService"
        ) as MockDE:
            de_instance = MockDE.return_value
            de_instance.create_conversation.return_value = "projects/p/conversations/new"
            de_instance.converse.return_value = ("Answer.", [])

            controller = ChatController(session_service=mock_session_service)
            controller.chat(ChatRequest(message="Hello"), user_context)

            de_instance.create_conversation.assert_called_once()

    def test_chat_fallback_message_when_no_answer(
        self, user_context, mock_session_service
    ):
        with patch(
            "app.controllers.chat_controller.DiscoveryEngineService"
        ) as MockDE:
            de_instance = MockDE.return_value
            de_instance.converse.return_value = ("", [])

            controller = ChatController(session_service=mock_session_service)
            response = controller.chat(
                ChatRequest(message="Unanswerable question"), user_context
            )

        assert "could not find" in response.message.content.lower()
        assert response.grounded is False
