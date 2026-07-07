#!/usr/bin/env python3
"""
Minimal Salesforce REST client for the KA generator.

Reuses the session-ID (Bearer token) authentication pattern used across the
edf-support-tools case exporters. It supports:

* SOQL queries (single page, pagination, auto-paginate)
* Fetching cases by owner email or by case number, filtered by record type
* Fetching resolution steps from Task records and Case Comments
* Creating a draft Knowledge Article (Knowledge__kav) via the REST sObject API

Authentication:
    Session id is read (in priority order) from:
      1. Explicit `session_id` argument
      2. `SF_SID` environment variable (may be a raw sid OR a path to a file)
      3. `SF_SID_FILE` environment variable (path to a file containing the sid)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlparse

import requests

DEFAULT_SF_URL = "https://hp.my.salesforce.com"
DEFAULT_API_VERSION = "59.0"

# Record types the app understands (matches SFDC GSD case record types).
RECORD_TYPE_GROUPS: Dict[str, List[str]] = {
    "gsd_csc_closed": ["GSD CSC Case Closed"],
    "gsd_csc_open": ["GSD CSC Case Open", "GSD CSC Case Creation"],
    "gsd_elevation": [
        "GSD Elevation Case Closed",
        "GSD Elevation Case Open",
        "GSD Elevation Case Cancelled",
    ],
}

ALL_RECORD_TYPES: List[str] = [rt for grp in RECORD_TYPE_GROUPS.values() for rt in grp]

CASE_FIELDS = (
    "Id, CaseNumber, Subject, Issue__c, Case_description__c, "
    "Software_Category__c, Software_Version__c, Cause__c, Resolution__c, "
    "Case_Owner_eMail__c, OwnerId, Owner.Name, Owner.Email, "
    "Severity__c, Priority, Status, AccountName__c, RecordType.Name, "
    "Environment__c, CreatedDate, ClosedDate"
)

TASK_FIELDS = (
    "Id, WhatId, Subject, Description, Status, Type, Category__c, "
    "Log_Action_Type__c, Owner.Name, Owner.Email, CreatedDate, CompletedDateTime"
)


class SalesforceError(Exception):
    """Raised when the Salesforce API returns an error response."""

    def __init__(self, status: int, content: str, url: str = ""):
        self.status = status
        self.content = content
        self.url = url
        super().__init__(f"Salesforce API error {status}: {content}")


def resolve_session_id(session_id: Optional[str] = None) -> str:
    """Resolve a Salesforce session id from argument, env var, or file."""
    if session_id:
        return session_id.strip()

    file_path = os.environ.get("SF_SID_FILE")
    if file_path and os.path.isfile(os.path.expanduser(file_path)):
        with open(os.path.expanduser(file_path), "r", encoding="utf-8") as fh:
            return fh.read().strip()

    env_sid = os.environ.get("SF_SID")
    if env_sid:
        # SF_SID may itself point to a file.
        maybe_path = os.path.expanduser(env_sid)
        if os.path.isfile(maybe_path):
            with open(maybe_path, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        return env_sid.strip()

    raise SalesforceError(401, "No Salesforce session id found (set SF_SID or SF_SID_FILE).")


class Salesforce:
    """Minimal Salesforce REST API client (session-id / Bearer token auth)."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        instance_url: str = DEFAULT_SF_URL,
        version: str = DEFAULT_API_VERSION,
        timeout: int = 30,
    ):
        self.session_id = resolve_session_id(session_id)
        self.sf_version = version
        self.timeout = timeout

        parsed = urlparse(instance_url or DEFAULT_SF_URL)
        self.sf_instance = parsed.hostname
        if parsed.port and parsed.port != 443:
            self.sf_instance = f"{self.sf_instance}:{parsed.port}"

        self.base_url = f"https://{self.sf_instance}/services/data/v{self.sf_version}/"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.session_id}",
            "X-PrettyPrint": "1",
        }
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # Low-level HTTP
    # ------------------------------------------------------------------ #
    def _call(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        headers = self.headers.copy()
        headers.update(kwargs.pop("headers", {}) or {})
        result = self.session.request(
            method, url, headers=headers, timeout=self.timeout, **kwargs
        )
        if result.status_code >= 300:
            try:
                error_content = result.json()
            except Exception:
                error_content = result.text
            raise SalesforceError(result.status_code, str(error_content), url)
        return result

    # ------------------------------------------------------------------ #
    # SOQL
    # ------------------------------------------------------------------ #
    def query(self, soql: str) -> Dict[str, Any]:
        url = self.base_url + "query/"
        return self._call("GET", url, params={"q": soql}).json()

    def query_more(self, next_records_url: str) -> Dict[str, Any]:
        url = f"https://{self.sf_instance}{next_records_url}"
        return self._call("GET", url).json()

    def query_all_iter(self, soql: str) -> Iterator[Dict[str, Any]]:
        result = self.query(soql)
        while True:
            for record in result.get("records", []):
                yield record
            if result.get("done", True):
                return
            result = self.query_more(result["nextRecordsUrl"])

    def query_all(self, soql: str) -> List[Dict[str, Any]]:
        return list(self.query_all_iter(soql))

    # ------------------------------------------------------------------ #
    # sObject create (for KA drafts)
    # ------------------------------------------------------------------ #
    def create_sobject(self, sobject: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}sobjects/{sobject}/"
        return self._call("POST", url, json=payload).json()

    # ------------------------------------------------------------------ #
    # High-level helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _record_type_clause(record_types: List[str]) -> str:
        rt = "', '".join(t.replace("'", r"\'") for t in record_types)
        return f"RecordType.Name IN ('{rt}')"

    def get_cases_by_owner(
        self,
        owner_email: str,
        record_types: Optional[List[str]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch cases owned by ``owner_email`` filtered by record type / date."""
        record_types = record_types or ALL_RECORD_TYPES
        clauses = [
            f"Case_Owner_eMail__c = '{owner_email.replace(chr(39), '')}'",
            self._record_type_clause(record_types),
        ]
        if start_date:
            clauses.append(f"CreatedDate >= {start_date}T00:00:00Z")
        if end_date:
            clauses.append(f"CreatedDate <= {end_date}T23:59:59Z")
        where = " AND ".join(clauses)
        soql = f"SELECT {CASE_FIELDS} FROM Case WHERE {where} ORDER BY CreatedDate DESC"
        return self.query_all(soql)

    def get_case_by_number(self, case_number: str) -> Optional[Dict[str, Any]]:
        safe = case_number.replace("'", "")
        soql = f"SELECT {CASE_FIELDS} FROM Case WHERE CaseNumber = '{safe}'"
        records = self.query_all(soql)
        return records[0] if records else None

    def get_tasks_for_case(self, case_id: str) -> List[Dict[str, Any]]:
        safe = case_id.replace("'", "")
        soql = (
            f"SELECT {TASK_FIELDS} FROM Task WHERE WhatId = '{safe}' "
            f"ORDER BY CreatedDate ASC"
        )
        return self.query_all(soql)

    def get_comments_for_case(self, case_id: str) -> List[Dict[str, Any]]:
        safe = case_id.replace("'", "")
        soql = (
            "SELECT Id, ParentId, CommentBody, CreatedById, CreatedDate "
            f"FROM CaseComment WHERE ParentId = '{safe}' ORDER BY CreatedDate ASC"
        )
        return self.query_all(soql)

    def create_knowledge_draft(
        self,
        title: str,
        url_name: str,
        summary: str,
        body_field: str,
        body_html: str,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a draft Knowledge__kav record. Best-effort; field API names vary."""
        payload: Dict[str, Any] = {
            "Title": title,
            "UrlName": url_name,
            "Summary": summary,
            body_field: body_html,
        }
        if extra_fields:
            payload.update(extra_fields)
        return self.create_sobject("Knowledge__kav", payload)
