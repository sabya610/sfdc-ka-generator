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
#
# product_tags: list of HPE product series numbers to set on KMProductAttribute.
#   PCAI / AIE / EZUA  →  R4T71AAE  (HPE Ezmeral Machine Learning Ops Software)
#   Data Fabric        →  R9E30AAE  (HPE Ezmeral Data Fabric Software – Customer Managed)
#                         plus the full series list attached automatically when
#                         tag_all_datafabric_series=True is passed to publish.
# ---------------------------------------------------------------------------
PRODUCT_CATALOG: Dict[str, Dict[str, Any]] = {
    "container-platform": {
        "label": "HPE Ezmeral Container Platform / Unified Analytics (PCAI / AIE / EZUA)",
        "product_group": "Enterprise Solutions",
        "product_queue": "HPE Ezmeral",
        "product_line": "CONT PLT SW (RM)",
        "environments": ["AIE 1.x", "PCAI 1.x", "EZUA 1.x"],
        # Product Tagging (KM_ProductAttributeTag__c) → HPE Ezmeral Runtime Enterprise.
        "product_tag": {
            "name": "HPEEZMRNESSPRE",
            "description": "HPE Ezmeral Runtime Enterprise Software",
            "hierarchy": "aGS1V000000g6UlWAI",
            "product_line_name": "CONT PLT SW",
        },
    },
    "datafabric": {
        "label": "HPE Ezmeral Data Fabric",
        "product_group": "Enterprise Solutions",
        "product_queue": "HPE Ezmeral",
        "product_line": "DATA FABRIC SW (PU)",
        "environments": ["Data Fabric 7.x"],
        "product_tag": {
            "name": "HPEEZMRNESSPRE",
            "description": "HPE Ezmeral Runtime Enterprise Software",
            "hierarchy": "aGS1V000000g6UlWAI",
            "product_line_name": "CONT PLT SW",
        },
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
    # Block-level HTML elements must become newlines so that structured
    # sections (separated by lines of ====) survive the cleaning step.
    text = re.sub(
        r"<br\s*/?>|</?(?:p|div|li|tr|td|th|h[1-6]|blockquote|pre)\b[^>]*>",
        "\n",
        text,
        flags=re.IGNORECASE,
    )
    # Strip remaining inline tags without adding spaces (avoids gluing words)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)         # collapse horizontal whitespace
    text = re.sub(r"\n[ \t]+", "\n", text)      # strip leading spaces from lines
    text = re.sub(r"\n{3,}", "\n\n", text)      # collapse excessive blank lines
    return text.strip()


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


# ---------------------------------------------------------------------------
# Sensitive-data masking
# ---------------------------------------------------------------------------
_SENSITIVE_PATTERNS = [
    # IPv4 addresses (e.g. 192.168.1.1 → <IP_ADDR>)
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<IP_ADDR>"),
    # SHA-256 / container/image hashes (40–64 hex chars)
    (re.compile(r"\b[0-9a-f]{40,64}\b", re.IGNORECASE), "<HASH>"),
    # UUIDs / pod UIDs
    (re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    ), "<UID>"),
]


def _mask_sensitive(text: str) -> str:
    """Mask IP addresses, UUIDs and hash strings from log / resolution text."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Structured task description parser
# ---------------------------------------------------------------------------
# HPE task descriptions come in two shapes:
#   1. Divider style : "Issue Summary" / "Root Cause Analysis" / "Resolution"
#                      sections separated by lines of ==== or ---- dividers.
#   2. Colon style   : "Issue:" / "Root cause:" / "Resolution:" inline headers
#                      (optionally with text on the same line), no dividers.
# The parser below supports both.
_DIVIDER_RE = re.compile(r"^[=\-]{5,}\s*$")
# Section headers we capture. Each allows an optional trailing ": inline text".
_SECTION_MAP: Dict[str, re.Pattern] = {
    "issue": re.compile(r"^\s*issue(?:\s+summary)?\s*(?::\s*(.*))?$", re.IGNORECASE),
    "cause": re.compile(
        r"^\s*(?:root\s+cause(?:\s+analysis)?|rca|cause)\s*(?::\s*(.*))?$",
        re.IGNORECASE,
    ),
    "resolution": re.compile(
        r"^\s*(?:resolution(?:\s+steps?)?|solution(?:\s+steps?)?|fix)\s*(?::\s*(.*))?$",
        re.IGNORECASE,
    ),
}
# Headers that terminate the current section but are NOT captured (debug notes,
# prose summaries, caveats). Prevents these from polluting Issue/Cause/Resolution.
_IGNORE_HEADER = re.compile(
    r"^\s*(?:troubleshooting\s+steps?(?:\s+performed)?|steps?\s+performed"
    r"|how\s+we\s+fixed\s+it|analysis|investigation|notes?"
    r"|environment|observations?|next\s+action(?:\s+plan)?)\s*(?::\s*.*)?$",
    re.IGNORECASE,
)


def _parse_structured_task(description: str) -> Dict[str, str]:
    """Parse a structured task description into named sections.

    Returns a dict with keys ``issue``, ``cause``, ``resolution``.
    Empty strings are returned for sections not found.
    """
    sections: Dict[str, str] = {"issue": "", "cause": "", "resolution": ""}
    current_section: Optional[str] = None
    buf: List[str] = []

    def _flush() -> None:
        nonlocal current_section, buf
        text = "\n".join(buf).strip()
        if current_section and text and not sections[current_section]:
            sections[current_section] = text
        buf = []

    for line in description.splitlines():
        stripped = line.strip()
        # Divider lines reset the section (divider-style format).
        if _DIVIDER_RE.match(stripped):
            _flush()
            current_section = None
            continue
        # Non-captured headers (debug notes / summaries) end the current section.
        if _IGNORE_HEADER.match(stripped):
            _flush()
            current_section = None
            continue
        # Captured section headers (Issue / Cause / Resolution).
        matched = False
        for sec_key, pattern in _SECTION_MAP.items():
            m = pattern.match(stripped)
            if m:
                _flush()
                current_section = sec_key
                inline = (m.group(1) or "").strip()
                if inline:
                    buf.append(inline)
                matched = True
                break
        if not matched:
            buf.append(line)
    _flush()
    return sections


# Task types that are email correspondence — never useful for a KA.
_EMAIL_SUBJECT_RE = re.compile(
    r"^(?:email:|re:\s*hpe\s+support\s+case|fw:|fwd:)",
    re.IGNORECASE,
)
# Match any "Standard Task" record type (covers CSC and Elevation variants).
_STANDARD_TASK_RE = re.compile(r"standard\s+task", re.IGNORECASE)


def _is_useful_task(task: Dict[str, Any]) -> bool:
    """Return True only for completed Standard Tasks; skip all email tasks."""
    # Must be Completed
    status = (task.get("Status") or "").strip().lower()
    if status and status != "completed":
        return False
    # Exclude email type
    task_type = (task.get("Type") or "").strip().lower()
    if task_type == "email":
        return False
    # Exclude email-like subjects
    subject = (task.get("Subject") or "").strip()
    if _EMAIL_SUBJECT_RE.match(subject):
        return False
    # RecordType: allow if absent OR if it's a "Standard Task" (CSC or Elevation)
    record_type = ((task.get("RecordType") or {}).get("Name") or "").strip()
    if record_type and not _STANDARD_TASK_RE.search(record_type):
        return False
    return True


def _is_substantive(desc: str) -> bool:
    """Return True if a task description contains meaningful troubleshooting content.

    Rejects one-liner engagement logs like "I initially worked on this case..."
    that add no value to a KA.  The threshold is intentionally generous so
    genuine short steps are not dropped.
    """
    text = desc.strip()
    if len(text) < 80:
        return False
    # Reject pure engagement / initial-contact phrasing
    _ENGAGEMENT_RE = re.compile(
        r"^(i\s+)?(initially\s+worked|contacted\s+customer|reached\s+out|"
        r"requested\s+(the\s+)?(appropriate\s+)?details|awaiting\s+response|"
        r"following\s+up|case\s+reassigned)",
        re.IGNORECASE,
    )
    return not _ENGAGEMENT_RE.match(text)


def collect_resolution_steps(
    case: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
) -> tuple:
    """Return ``(resolution_text, steps)`` with one step entry per task.

    Key design principle: each completed Standard Task description becomes
    exactly ONE step verbatim.  We do NOT call ``_split_steps`` on the
    assembled text — doing so misinterprets numbered markers *inside* task
    descriptions (e.g. "2 of 3 pods…") as step boundaries, corrupting output.

    Strategy:
    - Completed Standard Tasks (GSD CSC or Elevation) → one step each.
    - A task is included ONLY if it has an explicit structured "Resolution"
      section — this reliably selects the KA-worthy task (the one that
      documents the actual fix) regardless of which engineer owns it.
      Raw debugging-note tasks and monitoring follow-ups are naturally
      excluded because they lack a Resolution section.
    - Case Resolution__c → prepended text for closed cases only.
    - Case Comments → last-resort fallback when no tasks found.
    """
    useful = [t for t in tasks if _is_useful_task(t)]

    steps: List[str] = []
    seen_resolutions: set = set()
    resolution_prefix: List[str] = []
    extracted_issue: str = ""
    extracted_cause: str = ""

    # Resolution__c is only reliable for closed cases.
    case_status = (case.get("Status") or "").strip().lower()
    if case_status == "closed" and case.get("Resolution__c"):
        r = _mask_sensitive(_clean(case["Resolution__c"]))
        if r:
            resolution_prefix.append(r)

    for task in useful:
        desc = _clean(task.get("Description") or "")
        if not desc or not _is_substantive(desc):
            continue

        # Parse structured format (Issue Summary / Root Cause / Resolution sections)
        parsed = _parse_structured_task(desc)

        # Harvest issue / cause from the first task that provides them (even if
        # this task's Resolution section is empty — the next task may have it).
        if parsed["issue"] and not extracted_issue:
            extracted_issue = _mask_sensitive(parsed["issue"])
        if parsed["cause"] and not extracted_cause:
            extracted_cause = _mask_sensitive(parsed["cause"])

        # STRICT: only include tasks that have an explicit "Resolution" section.
        # Tasks without it contain raw debugging notes (junk), not resolution steps.
        if not parsed["resolution"]:
            continue

        step_body = _mask_sensitive(parsed["resolution"])

        # De-duplicate: multiple tasks (e.g. "Root Cause analysis" + the main
        # task) often carry an identical Resolution section. Keep only the first.
        norm = re.sub(r"\s+", " ", step_body).strip().lower()
        if norm in seen_resolutions:
            continue
        seen_resolutions.add(norm)

        subject = _clean(task.get("Subject") or "")
        header = f"[Task: {subject}]" if subject else "[Task]"
        steps.append(f"{header}\n{step_body}")

    # Fallback: case comments when no task steps found
    if not steps and not resolution_prefix:
        for comment in comments:
            body = _clean(comment.get("CommentBody") or "")
            if body:
                steps.append(body)

    all_chunks = resolution_prefix + steps
    resolution_text = "\n\n".join(c for c in all_chunks if c).strip()

    # If no task steps but we have resolution text, split it the old way
    if not steps and resolution_text:
        steps = _split_steps(resolution_text)

    return resolution_text, steps, extracted_issue, extracted_cause


def collect_resolution_text(
    case: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
) -> str:
    """Convenience wrapper returning just the full resolution text string."""
    text, _, _, _ = collect_resolution_steps(case, tasks, comments)
    return text


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
    resolution_text, steps, extracted_issue, extracted_cause = collect_resolution_steps(case, tasks, comments)
    # Fall back to task-extracted issue/cause when the case fields are empty
    issue = issue or extracted_issue
    cause = cause or extracted_cause

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


# ---------------------------------------------------------------------------
# Local LLM (rag-app llama.cpp endpoint) — structured KA prompt
# ---------------------------------------------------------------------------

_LLM_KA_PROMPT = """\
You are an HPE Ezmeral support engineer writing a formal Knowledge Article.
Convert the support case data below into a clean, customer-safe article.
Remove all IP addresses, hostnames, customer names and email addresses — replace with generic terms.

CASE SUBJECT: {subject}

ISSUE (raw from case/task):
{issue}

ROOT CAUSE (raw from case/task):
{cause}

RESOLUTION STEPS (from closed task):
{resolution}

PRODUCT: {product}

Write the article using EXACTLY these labeled sections (no extra commentary):

TITLE: <concise one-line title>
SUMMARY: <2-3 sentence summary of the problem and fix>
ISSUE:
<clear description of the symptom visible to the customer>
CAUSE:
<technical root cause explanation>
RESOLUTION:
1. <step one — use `command` notation for CLI>
2. <step two>
...

<END>"""


def _parse_llm_sections(text: str) -> Dict[str, str]:
    """Extract TITLE/SUMMARY/ISSUE/CAUSE/RESOLUTION sections from LLM plain-text output."""
    patterns = {
        "title":      re.compile(r"TITLE:\s*(.+?)(?=\n(?:SUMMARY|ISSUE|CAUSE|RESOLUTION):|$)", re.S | re.I),
        "summary":    re.compile(r"SUMMARY:\s*(.+?)(?=\n(?:TITLE|ISSUE|CAUSE|RESOLUTION):|$)", re.S | re.I),
        "issue":      re.compile(r"ISSUE:\s*(.+?)(?=\n(?:TITLE|SUMMARY|CAUSE|RESOLUTION):|$)", re.S | re.I),
        "cause":      re.compile(r"CAUSE:\s*(.+?)(?=\n(?:TITLE|SUMMARY|ISSUE|RESOLUTION):|$)", re.S | re.I),
        "resolution": re.compile(r"RESOLUTION:\s*(.+?)(?=\n(?:TITLE|SUMMARY|ISSUE|CAUSE):|$)", re.S | re.I),
    }
    return {k: (m.group(1).strip() if (m := p.search(text)) else "") for k, p in patterns.items()}


def build_rag_llm_article(
    case: Dict[str, Any],
    tasks: List[Dict[str, Any]],
    comments: List[Dict[str, Any]],
    product_key: str = DEFAULT_PRODUCT,
    llm_endpoint: Optional[str] = None,
) -> KnowledgeArticle:
    """Call the rag-app /api/llm endpoint to draft the KA; falls back to template."""
    import urllib.request, json as _json

    endpoint = llm_endpoint or os.environ.get("LLM_ENDPOINT", "")
    base = build_template_article(case, tasks, comments, product_key)
    if not endpoint:
        return base

    prompt = _LLM_KA_PROMPT.format(
        subject=base.source_case_subject or case.get("Subject", ""),
        issue=base.issue or "(not captured)",
        cause=base.cause or "(not captured)",
        resolution=base.resolution or "(not captured)",
        product=base.product_label,
    )

    try:
        payload = _json.dumps({"prompt": prompt, "max_tokens": 1500}).encode()
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = _json.loads(resp.read())
        text = result.get("response", "")
        if not text:
            return base

        secs = _parse_llm_sections(text)
        if secs.get("title"):
            base.title = secs["title"][:200]
            base.url_name = slugify(base.title)
        if secs.get("summary"):
            base.summary = secs["summary"][:255]
        if secs.get("issue"):
            base.issue = secs["issue"]
        if secs.get("cause"):
            base.cause = secs["cause"]
        if secs.get("resolution"):
            # Split numbered steps into list
            step_lines = [
                re.sub(r"^\d+\.\s*", "", ln).strip()
                for ln in secs["resolution"].splitlines()
                if ln.strip() and re.match(r"^\d+\.", ln.strip())
            ]
            if step_lines:
                base.steps = step_lines
            base.resolution = secs["resolution"]
        base.generator = "rag-llm:llama3.1-8b"
    except Exception:  # never fail KA generation due to LLM error
        return base
    return base


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
    """Top-level entry point: prefer local rag-app LLM → OpenAI → template."""
    if use_llm:
        llm_endpoint = os.environ.get("LLM_ENDPOINT", "")
        if llm_endpoint:
            return build_rag_llm_article(case, tasks, comments, product_key, llm_endpoint)
        if os.environ.get("OPENAI_API_KEY"):
            return build_llm_article(case, tasks, comments, product_key)
    return build_template_article(case, tasks, comments, product_key)
