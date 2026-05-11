"""Integration tests for ChatController (all external services mocked)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.controllers.chat_controller import ChatController
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.datastore import DatastoreTarget
from app.models.document import DocumentStructData, RetrievedDocument
from app.models.user import AccessLevel, GCPIdentity, HWCUser, UserContext
from app.schemas.chat import ChatRequest
from app.services.discovery_engine_service import AnswerResult


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("DISCOVERY_ENGINE_ENGINE_ID", "test-engine")
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
        de_session_name=None,
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

    def test_chat_returns_grounded_response(
        self, user_context, mock_session_service, retrieved_docs
    ):
        """answer_query() returns a grounded answer with references."""
        result = AnswerResult(
            answer_text="Here is the answer based on documents.",
            references=retrieved_docs,
            session_name="projects/p/engines/e/sessions/s1",
            grounded=True,
        )

        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
                DatastoreTarget("ds-internal-a", AccessLevel.INTERNAL, label="internal-A"),
            ]
            MockDE.return_value.answer_query.return_value = result

            controller = ChatController(session_service=mock_session_service)
            response = controller.chat(
                ChatRequest(message="What is the policy?"),
                user_context,
            )

        assert response.session_id == "session-123"
        assert response.message.content == "Here is the answer based on documents."
        assert response.grounded is True
        assert len(response.message.sources) == 1
        assert response.message.sources[0].title == "Test Document"
        assert response.message.sources[0].access_level == "internal"

    def test_chat_passes_de_session_name_from_chat_session(
        self, user_context, mock_session_service
    ):
        """Existing DE session name is forwarded to answer_query()."""
        existing_session = "projects/p/engines/e/sessions/existing"
        mock_session_service.get_or_create_session.return_value.de_session_name = existing_session

        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
            ]
            MockDE.return_value.answer_query.return_value = AnswerResult(
                answer_text="Answer.", references=[], session_name="", grounded=False
            )

            controller = ChatController(session_service=mock_session_service)
            controller.chat(ChatRequest(message="Hello"), user_context)

            call_kwargs = MockDE.return_value.answer_query.call_args[1]
            assert call_kwargs["existing_session"] == existing_session

    def test_chat_passes_empty_session_when_none(
        self, user_context, mock_session_service
    ):
        """None de_session_name is converted to empty string for answer_query()."""
        mock_session_service.get_or_create_session.return_value.de_session_name = None

        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
            ]
            MockDE.return_value.answer_query.return_value = AnswerResult(
                answer_text="Answer.", references=[], session_name="", grounded=False
            )

            controller = ChatController(session_service=mock_session_service)
            controller.chat(ChatRequest(message="Hello"), user_context)

            call_kwargs = MockDE.return_value.answer_query.call_args[1]
            assert call_kwargs["existing_session"] == ""

    def test_chat_updates_de_session_after_response(
        self, user_context, mock_session_service
    ):
        """Returned session name from DE is persisted on the ChatSession."""
        new_session = "projects/p/engines/e/sessions/new-session"

        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-internal-a", AccessLevel.INTERNAL, label="internal-A"),
            ]
            MockDE.return_value.answer_query.return_value = AnswerResult(
                answer_text="Answer.",
                references=[],
                session_name=new_session,
                grounded=False,
            )

            controller = ChatController(session_service=mock_session_service)
            controller.chat(ChatRequest(message="Follow-up"), user_context)

        mock_session_service.update_de_session.assert_called_once()
        _, stored_name = mock_session_service.update_de_session.call_args[0]
        assert stored_name == new_session

    def test_chat_all_four_buckets_queried(
        self, user_context, mock_session_service, retrieved_docs
    ):
        """Controller passes all 4 target types to answer_query()."""
        four_targets = [
            DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
            DatastoreTarget("ds-internal-a", AccessLevel.INTERNAL, label="internal-A"),
            DatastoreTarget("ds-relate-ab", AccessLevel.RELATE, label="relate-ab"),
            DatastoreTarget("ds-confidential", AccessLevel.CONFIDENTIAL, jd_code="ENG001", label="confidential"),
        ]

        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = four_targets
            MockDE.return_value.answer_query.return_value = AnswerResult(
                answer_text="Answer.", references=retrieved_docs,
                session_name="", grounded=True,
            )

            controller = ChatController(session_service=mock_session_service)
            controller.chat(ChatRequest(message="Everything"), user_context)

            call_targets = MockDE.return_value.answer_query.call_args[1]["targets"]
            assert len(call_targets) == 4

    def test_chat_fallback_when_no_answer(self, user_context, mock_session_service):
        """Empty answer_text produces a fallback message; grounded=False."""
        with (
            patch("app.controllers.chat_controller.DiscoveryEngineService") as MockDE,
            patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting,
        ):
            MockRouting.return_value.resolve_targets.return_value = [
                DatastoreTarget("ds-public", AccessLevel.PUBLIC, label="public"),
            ]
            MockDE.return_value.answer_query.return_value = AnswerResult(
                answer_text="", references=[], session_name="", grounded=False
            )

            controller = ChatController(session_service=mock_session_service)
            response = controller.chat(
                ChatRequest(message="Unanswerable"), user_context
            )

        assert response.grounded is False
        assert "could not find" in response.message.content.lower()

    def test_registry_not_initialised_returns_503(
        self, user_context, mock_session_service
    ):
        with patch("app.controllers.chat_controller.DatastoreRoutingService") as MockRouting:
            MockRouting.return_value.resolve_targets.side_effect = RuntimeError(
                "DatastoreRegistry not initialised"
            )
            from fastapi import HTTPException
            controller = ChatController(session_service=mock_session_service)
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
            MockDE.return_value.answer_query.side_effect = Exception("GCP unavailable")

            from fastapi import HTTPException
            controller = ChatController(session_service=mock_session_service)
            with pytest.raises(HTTPException) as exc_info:
                controller.chat(ChatRequest(message="Hello"), user_context)
            assert exc_info.value.status_code == 502
