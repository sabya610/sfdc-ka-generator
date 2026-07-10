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
KA_*_FIELD      Override GSD Issue/Solution Knowledge__kav field API names
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


@app.errorhandler(Exception)
def handle_unhandled(exc: Exception):
    """Return JSON for all unhandled exceptions so the browser never gets HTML."""
    app.logger.exception("Unhandled exception: %s", exc)
    return jsonify({"error": str(exc), "type": type(exc).__name__}), 500


SF_URL = os.environ.get("SF_URL", "https://hp.my.salesforce.com")
# GSD KM Issue/Solution Knowledge__kav field API names (override via env if needed).
KA_ISSUE_FIELD = os.environ.get("KA_ISSUE_FIELD", "GSD_KM_Issue_Solution_Issue__c")
KA_CAUSE_FIELD = os.environ.get("KA_CAUSE_FIELD", "GSD_KM_Issue_Solution_Cause__c")
KA_RESOLUTION_FIELD = os.environ.get("KA_RESOLUTION_FIELD", "GSD_KM_Issue_Solution_Resolution__c")
KA_ENVIRONMENT_FIELD = os.environ.get("KA_ENVIRONMENT_FIELD", "GSD_KM_Issue_Solution_Environment__c")
KA_PRODUCT_FIELD = os.environ.get("KA_PRODUCT_FIELD", "GSD_KM_Issue_Solution_Product__c")


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


@app.post("/api/debug")
def api_debug():
    """Diagnostic: dump raw tasks + per-filter decisions for a case number."""
    from .ka_generator import (
        _clean,
        _is_useful_task,
        _is_substantive,
        _parse_structured_task,
    )

    data = request.get_json(silent=True) or {}
    case_number = (data.get("case_number") or "").strip()
    if not case_number:
        return jsonify({"error": "case_number is required"}), 400

    sf = _client()
    bundle = _load_case_bundle(sf, case_number)
    case = bundle["case"]
    tasks = bundle["tasks"]

    owner_email = (
        (case.get("Case_Owner_eMail__c") or "")
        or ((case.get("Owner") or {}).get("Email") or "")
    ).strip().lower()

    task_report = []
    for t in tasks:
        raw_desc = t.get("Description") or ""
        cleaned = _clean(raw_desc)
        parsed = _parse_structured_task(cleaned)
        task_report.append({
            "subject": t.get("Subject"),
            "status": t.get("Status"),
            "type": t.get("Type"),
            "record_type": (t.get("RecordType") or {}).get("Name"),
            "owner_email": (t.get("Owner") or {}).get("Email"),
            "raw_desc_len": len(raw_desc),
            "raw_desc_head": raw_desc[:300],
            "cleaned_head": cleaned[:300],
            "is_useful": _is_useful_task(t),
            "is_substantive": _is_substantive(cleaned),
            "parsed_issue": parsed["issue"][:100],
            "parsed_cause": parsed["cause"][:100],
            "parsed_resolution": parsed["resolution"][:100],
        })

    return jsonify({
        "case_owner_email": owner_email,
        "case_status": case.get("Status"),
        "task_count": len(tasks),
        "tasks": task_report,
    })


@app.post("/api/publish")
def api_publish():
    """Create a draft Knowledge__kav record using GSD Issue/Solution fields."""
    data = request.get_json(silent=True) or {}
    required = ("title", "url_name", "summary")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"missing fields: {', '.join(missing)}"}), 400

    # Map the article sections to the org's GSD Issue/Solution rich-text fields.
    fields = {
        KA_ISSUE_FIELD: data.get("issue_html") or "",
        KA_CAUSE_FIELD: data.get("cause_html") or "",
        KA_RESOLUTION_FIELD: data.get("resolution_html") or "",
        KA_ENVIRONMENT_FIELD: data.get("environment_html") or "",
        KA_PRODUCT_FIELD: data.get("product_html") or "",
    }
    # When submit_for_review is set, move the draft into the review queue so it
    # appears in "Article Awaiting Review" and can be approved/published.
    if data.get("submit_for_review"):
        fields["ValidationStatus"] = os.environ.get(
            "KA_REVIEW_STATUS", "Awaiting Technical Review"
        )

    try:
        sf = _client()
        # Assign ownership to a real user (by email) instead of the integration user.
        owner_email = (data.get("owner_email") or os.environ.get("KA_DEFAULT_OWNER_EMAIL") or "").strip()
        owner_warning = None
        if owner_email:
            owner_id = sf.get_user_id_by_email(owner_email)
            if owner_id:
                fields["OwnerId"] = owner_id
            else:
                owner_warning = f"No active SFDC user found for '{owner_email}'; owner left as integration user."

        result = sf.create_knowledge_draft(
            title=data["title"],
            url_name=data["url_name"],
            summary=data["summary"],
            fields=fields,
        )
    except SalesforceError as exc:
        return jsonify({"error": str(exc), "status": exc.status}), 502

    # Enrich the response so the user can locate the draft in SFDC.
    kav_id = (result or {}).get("id")
    article_number = None
    if kav_id:
        try:
            q = sf.query(
                "SELECT ArticleNumber, ValidationStatus, PublishStatus "
                f"FROM Knowledge__kav WHERE Id = '{kav_id}'"
            )
            recs = q.get("records") or []
            if recs:
                article_number = recs[0].get("ArticleNumber")
                result["validation_status"] = recs[0].get("ValidationStatus")
                result["publish_status"] = recs[0].get("PublishStatus")
        except SalesforceError:
            pass

    article_url = f"{SF_URL}/lightning/r/Knowledge__kav/{kav_id}/view" if kav_id else None
    return jsonify({
        "created": True,
        "result": result,
        "article_number": article_number,
        "article_url": article_url,
        "owner_warning": owner_warning,
    })


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)  # noqa: S104 - container binds all ifaces


if __name__ == "__main__":
    main()
