"""Unit tests for the InMemorySessionRepository."""

from __future__ import annotations

import pytest

from app.models.chat import ChatSession
from app.repositories.session_repository import InMemorySessionRepository


@pytest.fixture()
def repo():
    return InMemorySessionRepository()


@pytest.fixture()
def session_a():
    return ChatSession(
        id="s1",
        user_email="userA@hello.org",
        department="A",
        jd_code="ENG001",
    )


@pytest.fixture()
def session_b():
    return ChatSession(
        id="s2",
        user_email="userA@hello.org",
        department="A",
        jd_code="ENG001",
    )


def test_save_and_get(repo, session_a):
    repo.save(session_a)
    result = repo.get("s1")
    assert result is not None
    assert result.id == "s1"


def test_get_missing_returns_none(repo):
    assert repo.get("nonexistent") is None


def test_delete(repo, session_a):
    repo.save(session_a)
    repo.delete("s1")
    assert repo.get("s1") is None


def test_list_by_user(repo, session_a, session_b):
    repo.save(session_a)
    repo.save(session_b)
    results = repo.list_by_user("userA@hello.org")
    assert len(results) == 2


def test_list_by_user_empty_for_other_user(repo, session_a):
    repo.save(session_a)
    results = repo.list_by_user("userB@hello.org")
    assert len(results) == 0
