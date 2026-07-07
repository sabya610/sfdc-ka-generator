#!/usr/bin/env python3
"""
Knowledge Article generator.

Transforms a Salesforce case (plus its resolution steps / task steps / comments)
into a structured SFDC Knowledge Article draft that mirrors the layout used in
the HPE Ezmeral knowledge base:

    Article Type (Troubleshooting / How To / Informational)
    Title / URL Name / Summary
    Product tagging (Product Group / Queue / Line)
    Environment
    Issue
    Cause
    Resolution (pre-requisites + solution steps)

Two generation strategies are supported:

* ``llm``      - uses OpenAI (if OPENAI_API_KEY is configured) to write prose.
* ``template`` - deterministic assembly from the case fields (default fallback).
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Product catalog -> KA tagging (derived from HPE Ezmeral KA examples)
# ---------------------------------------------------------------------------
PRODUCT_CATALOG: Dict[str, Dict[str, Any]] = {
    "container-platform": {
        "label": "HPE Ezmeral Container Platform / Unified Analytics",
        "product_group": "Enterprise Solutions",
        "product_queue": "HPE Ezmeral",
        "product_line": "CONT PLT SW (RM)",
        "environments": ["AIE 1.x", "PCAI 1.x", "EZUA 1.x"],
    },
    "datafabric": {
        "label": "HPE Ezmeral Data Fabric",
        "product_group": "Enterprise Solutions",
        "product_queue": "HPE Ezmeral",
        "product_line": "DATA FABRIC SW (RM)",
        "environments": ["Data Fabric 7.x"],
    },
}

DEFAULT_PRODUCT = "container-platform"

ARTICLE_TYPES = ("Troubleshooting", "How To", "Informational")


@dataclass
class KnowledgeArticle:
    """Structured Knowledge Article ready for SFDC entry."""

    article_type: str = "Troubleshooting"
    title: str = ""
    url_name: str = ""
    summary: str = ""
    product_key: str = DEFAULT_PRODUCT
    product_label: str = ""
    product_group: str = ""
    product_queue: str = ""
    product_line: str = ""
    environment: List[str] = field(default_factory=list)
    issue: str = ""
    cause: str = ""
    resolution: str = ""
    prerequisites: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    source_case_number: str = ""
    source_case_subject: str = ""
    generator: str = "template"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def body_html(self) -> str:
        """Render the article body as HTML (for the SFDC rich-text field)."""
        def esc(text: str) -> str:
            return html.escape(text or "").replace("\n", "<br/>")

        parts: List[str] = []
        if self.issue:
            parts.append(f"<h2>Issue</h2><p>{esc(self.issue)}</p>")
        if self.cause:
            parts.append(f"<h2>Cause</h2><p>{esc(self.cause)}</p>")
        if self.prerequisites:
            items = "".join(f"<li>{esc(p)}</li>" for p in self.prerequisites)
            parts.append(f"<h2>Pre-requisites</h2><ol>{items}</ol>")
        if self.steps:
            items = "".join(f"<li>{esc(s)}</li>" for s in self.steps)
            parts.append(f"<h2>Resolution Steps</h2><ol>{items}</ol>")
        elif self.resolution:
            parts.append(f"<h2>Resolution</h2><p>{esc(self.resolution)}</p>")
        parts.append(
            f"<hr/><p><em>Derived from case {esc(self.source_case_number)}.</em></p>"
        )
        return "".join(parts)

    def body_text(self) -> str:
        lines: List[str] = [
            f"Article Type: {self.article_type}",
            f"Title: {self.title}",
            f"URL Name: {self.url_name}",
            "",
            f"Product Group: {self.product_group}",
            f"Product Queue: {self.product_queue}",
            f"Product Line: {self.product_line}",
            f"Environment: {', '.join(self.environment)}",
            "",
            "Summary:",
            self.summary,
            "",
            "Issue:",
            self.issue,
            "",
            "Cause:",
            self.cause,
            "",
        ]
        if self.prerequisites:
            lines.append("Pre-requisites:")
            lines.extend(f"  {i}. {p}" for i, p in enumerate(self.prerequisites, 1))
            lines.append("")
        lines.append("Resolution Steps:")
        if self.steps:
            lines.extend(f"  {i}. {s}" for i, s in enumerate(self.steps, 1))
        else:
            lines.append(self.resolution)
        lines.append("")
        lines.append(f"Derived from case {self.source_case_number}: {self.source_case_subject}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def slugify(text: str, max_len: int = 80) -> str:
    """Produce a URL-safe article name (lowercase, hyphen separated)."""
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower())
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len].rstrip("-") or "knowledge-article"


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    # Strip simple HTML tags that may appear in rich-text fields.
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _split_steps(text: str) -> List[str]:
    """Split resolution text into ordered steps using numbered / bullet markers."""
    if not text:
        return []
    # Prefer explicit numbered markers like "1." "2)" or "Step 1", whether they
    # appear at the start of a line or inline (preceded by whitespace).
    parts = re.split(r"(?:(?<=\s)|^)(?:step\s*)?\d+[.)]\s+", text, flags=re.IGNORECASE)
    parts = [p.strip(" -\t\r\n") for p in parts if p.strip(" -\t\r\n")]
    if len(parts) >= 2:
        return parts
    # Fall back to newline / bullet splitting.
    lines = [ln.strip(" -*\t\r") for ln in text.splitlines()]
    return [ln for ln in lines if ln]


def collect_resolution_text(case: Dict[str, Any],
                            tasks: List[Dict[str, Any]],
                            comments: List[Dict[str, Any]]) -> str:
    """Assemble the best available resolution / task-steps text for the case."""
    chunks: List[str] = []
    if case.get("Resolution__c"):
        chunks.append(_clean(case["Resolution__c"]))
    for task in tasks:
        desc = _clean(task.get("Description"))
        if desc:
            subject = _clean(task.get("Subject"))
            header = f"[Task: {subject}]" if subject else "[Task]"
            chunks.append(f"{header}\n{desc}")
    if not chunks:
        for comment in comments:
            body = _clean(comment.get("CommentBody"))
            if body:
                chunks.append(body)
    return "\n\n".join(c for c in chunks if c).strip()


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def _resolve_product(product_key: Optional[str]) -> Any:
    key = (product_key or DEFAULT_PRODUCT).lower()
    if key not in PRODUCT_CATALOG:
        key = DEFAULT_PRODUCT
    return PRODUCT_CATALOG[key], key


def build_template_article(
    case: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
    product_key: str = DEFAULT_PRODUCT,
) -> KnowledgeArticle:
    """Deterministically assemble a KA from case data (no LLM required)."""
    catalog, key = _resolve_product(product_key)
    subject = _clean(case.get("Subject"))
    issue = _clean(case.get("Issue__c")) or _clean(case.get("Case_description__c"))
    cause = _clean(case.get("Cause__c"))
    resolution_text = collect_resolution_text(case, tasks, comments)
    steps = _split_steps(resolution_text)

    summary = issue[:255] if issue else subject[:255]

    return KnowledgeArticle(
        article_type="Troubleshooting",
        title=subject or f"Knowledge Article for case {case.get('CaseNumber', '')}",
        url_name=slugify(subject or case.get("CaseNumber", "")),
        summary=summary,
        product_key=key,
        product_label=catalog["label"],
        product_group=catalog["product_group"],
        product_queue=catalog["product_queue"],
        product_line=catalog["product_line"],
        environment=list(catalog["environments"]),
        issue=issue,
        cause=cause,
        resolution=resolution_text,
        steps=steps,
        source_case_number=str(case.get("CaseNumber", "")),
        source_case_subject=subject,
        generator="template",
    )


LLM_SYSTEM_PROMPT = (
    "You are an HPE Ezmeral support engineer writing a formal Knowledge Article. "
    "Given a support case and its resolution/task steps, produce a clear, "
    "customer-safe troubleshooting article. Return STRICT JSON with keys: "
    "title, summary, issue, cause, prerequisites (array of strings), "
    "steps (array of strings). Do not include secrets, hostnames, IPs or emails."
)


def build_llm_article(
    case: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
    product_key: str = DEFAULT_PRODUCT,
    api_key: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> KnowledgeArticle:
    """Use OpenAI to draft the article; falls back to template on any failure."""
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    base = build_template_article(case, tasks, comments, product_key)
    if not api_key:
        return base

    try:
        import json as _json

        from openai import OpenAI  # imported lazily; optional dependency

        client = OpenAI(api_key=api_key)
        user_payload = {
            "subject": base.source_case_subject,
            "issue": base.issue,
            "cause": base.cause,
            "resolution_text": base.resolution,
            "product": base.product_label,
        }
        resp = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": _json.dumps(user_payload)},
            ],
        )
        data = _json.loads(resp.choices[0].message.content)
        base.title = data.get("title") or base.title
        base.summary = (data.get("summary") or base.summary)[:255]
        base.issue = data.get("issue") or base.issue
        base.cause = data.get("cause") or base.cause
        base.prerequisites = list(data.get("prerequisites") or [])
        base.steps = list(data.get("steps") or base.steps)
        base.url_name = slugify(base.title)
        base.generator = f"openai:{model}"
    except Exception:  # noqa: BLE001 - never fail generation because of the LLM
        return base
    return base


def generate_article(
    case: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
    product_key: str = DEFAULT_PRODUCT,
    use_llm: bool = True,
) -> KnowledgeArticle:
    """Top-level entry point: choose LLM or template generation."""
    if use_llm and os.environ.get("OPENAI_API_KEY"):
        return build_llm_article(case, tasks, comments, product_key)
    return build_template_article(case, tasks, comments, product_key)
