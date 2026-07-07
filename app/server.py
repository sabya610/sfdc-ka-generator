#!/usr/bin/env python3
"""
Flask web server for the SFDC Knowledge Article generator.

Endpoints
---------
GET  /                 - Web UI (form)
GET  /healthz          - Liveness/readiness probe
POST /api/cases        - List cases for a user email (JSON)
POST /api/generate     - Generate a KA draft from a case number (JSON)
POST /api/publish      - Best-effort create a draft Knowledge__kav in SFDC

Configuration (environment variables)
-------------------------------------
SF_URL          Salesforce instance URL (default https://hp.my.salesforce.com)
SF_SID          Salesforce session id, or a path to a file containing it
SF_SID_FILE     Path to a file containing the session id
OPENAI_API_KEY  Optional; enables LLM-assisted drafting
KA_BODY_FIELD   API name of the KA rich-text body field (default: Details__c)
PORT            Listen port (default 8080)
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from flask import Flask, jsonify, render_template, request

from .ka_generator import (
    ARTICLE_TYPES,
    PRODUCT_CATALOG,
    generate_article,
)
from .sfdc_client import (
    ALL_RECORD_TYPES,
    RECORD_TYPE_GROUPS,
    Salesforce,
    SalesforceError,
)

app = Flask(__name__)

SF_URL = os.environ.get("SF_URL", "https://hp.my.salesforce.com")
KA_BODY_FIELD = os.environ.get("KA_BODY_FIELD", "Details__c")


def _client() -> Salesforce:
    return Salesforce(instance_url=SF_URL)


def _selected_record_types(groups: List[str]) -> List[str]:
    if not groups:
        return ALL_RECORD_TYPES
    selected: List[str] = []
    for g in groups:
        selected.extend(RECORD_TYPE_GROUPS.get(g, []))
    return selected or ALL_RECORD_TYPES


@app.get("/")
def index():
    return render_template(
        "index.html",
        products=PRODUCT_CATALOG,
        record_type_groups=RECORD_TYPE_GROUPS,
        article_types=ARTICLE_TYPES,
    )


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.post("/api/cases")
def api_cases():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "email is required"}), 400

    record_types = _selected_record_types(data.get("record_type_groups") or [])
    start_date = data.get("start_date") or None
    end_date = data.get("end_date") or None

    try:
        sf = _client()
        cases = sf.get_cases_by_owner(email, record_types, start_date, end_date)
    except SalesforceError as exc:
        return jsonify({"error": str(exc), "status": exc.status}), 502

    return jsonify(
        {
            "count": len(cases),
            "cases": [
                {
                    "case_number": c.get("CaseNumber"),
                    "subject": c.get("Subject"),
                    "status": c.get("Status"),
                    "severity": c.get("Severity__c"),
                    "record_type": (c.get("RecordType") or {}).get("Name"),
                    "created_date": c.get("CreatedDate"),
                }
                for c in cases
            ],
        }
    )


def _load_case_bundle(sf: Salesforce, case_number: str) -> Dict[str, Any]:
    case = sf.get_case_by_number(case_number)
    if not case:
        raise SalesforceError(404, f"Case {case_number} not found")
    case_id = case["Id"]
    tasks = sf.get_tasks_for_case(case_id)
    comments = sf.get_comments_for_case(case_id)
    return {"case": case, "tasks": tasks, "comments": comments}


@app.post("/api/generate")
def api_generate():
    data = request.get_json(silent=True) or {}
    case_number = (data.get("case_number") or "").strip()
    product = (data.get("product") or "container-platform").strip()
    use_llm = bool(data.get("use_llm", True))
    if not case_number:
        return jsonify({"error": "case_number is required"}), 400

    try:
        sf = _client()
        bundle = _load_case_bundle(sf, case_number)
    except SalesforceError as exc:
        return jsonify({"error": str(exc), "status": exc.status}), exc.status if exc.status == 404 else 502

    article = generate_article(
        bundle["case"], bundle["tasks"], bundle["comments"],
        product_key=product, use_llm=use_llm,
    )
    result = article.to_dict()
    result["body_html"] = article.body_html()
    result["body_text"] = article.body_text()
    result["task_count"] = len(bundle["tasks"])
    result["comment_count"] = len(bundle["comments"])
    return jsonify(result)


@app.post("/api/publish")
def api_publish():
    """Best-effort creation of a draft Knowledge__kav record."""
    data = request.get_json(silent=True) or {}
    required = ("title", "url_name", "summary", "body_html")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"missing fields: {', '.join(missing)}"}), 400

    try:
        sf = _client()
        result = sf.create_knowledge_draft(
            title=data["title"],
            url_name=data["url_name"],
            summary=data["summary"],
            body_field=data.get("body_field") or KA_BODY_FIELD,
            body_html=data["body_html"],
        )
    except SalesforceError as exc:
        return jsonify({"error": str(exc), "status": exc.status}), 502

    return jsonify({"created": True, "result": result})


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)  # noqa: S104 - container binds all ifaces


if __name__ == "__main__":
    main()
