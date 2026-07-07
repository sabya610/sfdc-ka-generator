"""Tests for the SFDC client and Flask endpoints using mocks (no real network)."""

import json

import pytest

import app.server as server
from app.sfdc_client import Salesforce, SalesforceError, resolve_session_id


def test_resolve_session_id_from_arg():
    assert resolve_session_id("  abc123  ") == "abc123"


def test_resolve_session_id_from_env(monkeypatch):
    monkeypatch.delenv("SF_SID_FILE", raising=False)
    monkeypatch.setenv("SF_SID", "envsid")
    assert resolve_session_id() == "envsid"


def test_resolve_session_id_from_file(tmp_path, monkeypatch):
    f = tmp_path / "sid.txt"
    f.write_text("filesid\n")
    monkeypatch.delenv("SF_SID", raising=False)
    monkeypatch.setenv("SF_SID_FILE", str(f))
    assert resolve_session_id() == "filesid"


def test_resolve_session_id_missing(monkeypatch):
    monkeypatch.delenv("SF_SID", raising=False)
    monkeypatch.delenv("SF_SID_FILE", raising=False)
    with pytest.raises(SalesforceError):
        resolve_session_id()


def test_record_type_clause():
    clause = Salesforce._record_type_clause(["GSD CSC Case Closed", "GSD CSC Case Open"])
    assert clause == "RecordType.Name IN ('GSD CSC Case Closed', 'GSD CSC Case Open')"


# ---------------------------------------------------------------------------
# Flask endpoint tests with a fake Salesforce client
# ---------------------------------------------------------------------------
class FakeSF:
    def __init__(self, case=None, tasks=None, comments=None, cases=None):
        self._case = case
        self._tasks = tasks or []
        self._comments = comments or []
        self._cases = cases or []

    def get_cases_by_owner(self, email, record_types, start_date, end_date):
        return self._cases

    def get_case_by_number(self, case_number):
        return self._case

    def get_tasks_for_case(self, case_id):
        return self._tasks

    def get_comments_for_case(self, case_id):
        return self._comments


@pytest.fixture
def client(monkeypatch):
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Knowledge Article Generator" in resp.data


def test_api_cases_requires_email(client):
    resp = client.post("/api/cases", json={})
    assert resp.status_code == 400


def test_api_cases_ok(client, monkeypatch, sample_case):
    fake = FakeSF(cases=[sample_case])
    monkeypatch.setattr(server, "_client", lambda: fake)
    resp = client.post("/api/cases", json={"email": "a@hpe.com"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 1
    assert body["cases"][0]["case_number"] == "5400813446"


def test_api_generate_requires_case_number(client):
    resp = client.post("/api/generate", json={})
    assert resp.status_code == 400


def test_api_generate_ok(client, monkeypatch, sample_case, sample_tasks, sample_comments):
    fake = FakeSF(case=sample_case, tasks=sample_tasks, comments=sample_comments)
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    resp = client.post(
        "/api/generate",
        json={"case_number": "5400813446", "product": "container-platform", "use_llm": False},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["source_case_number"] == "5400813446"
    assert body["product_line"] == "CONT PLT SW (RM)"
    assert body["body_text"]
    assert body["task_count"] == 1


def test_api_generate_case_not_found(client, monkeypatch):
    fake = FakeSF(case=None)
    monkeypatch.setattr(server, "_client", lambda: fake)
    resp = client.post("/api/generate", json={"case_number": "0000000000"})
    assert resp.status_code == 404
