"""Unit tests for the KA generator (template mode, no network / no LLM)."""

from app.ka_generator import (
    PRODUCT_CATALOG,
    build_template_article,
    collect_resolution_text,
    slugify,
    _split_steps,
    generate_article,
)


def test_slugify():
    assert slugify("ezkf-agent starting issue after reboot of node") == (
        "ezkf-agent-starting-issue-after-reboot-of-node"
    )
    assert slugify("") == "knowledge-article"
    assert slugify("!!!") == "knowledge-article"


def test_split_steps_numbered():
    text = "1. First step. 2. Second step. 3. Third step."
    steps = _split_steps(text)
    assert len(steps) == 3
    assert steps[0].startswith("First")


def test_collect_resolution_prefers_resolution_field(sample_case, sample_tasks, sample_comments):
    text = collect_resolution_text(sample_case, sample_tasks, sample_comments)
    assert "ezkf-rules.v4" in text
    assert "[Task: Troubleshooting]" in text


def test_collect_resolution_falls_back_to_comments():
    case = {"Resolution__c": ""}
    comments = [{"CommentBody": "Only a comment here."}]
    text = collect_resolution_text(case, [], comments)
    assert "Only a comment here." in text


def test_build_template_article_container_platform(sample_case, sample_tasks, sample_comments):
    ka = build_template_article(sample_case, sample_tasks, sample_comments, "container-platform")
    assert ka.article_type == "Troubleshooting"
    assert ka.product_line == PRODUCT_CATALOG["container-platform"]["product_line"]
    assert ka.source_case_number == "5400813446"
    assert ka.title
    assert ka.steps  # resolution split into steps
    assert "Issue" in ka.body_html()
    assert "5400813446" in ka.body_text()


def test_build_template_article_datafabric(sample_case, sample_tasks, sample_comments):
    ka = build_template_article(sample_case, sample_tasks, sample_comments, "datafabric")
    assert ka.product_line == PRODUCT_CATALOG["datafabric"]["product_line"]
    assert "Data Fabric 7.x" in ka.environment


def test_unknown_product_defaults(sample_case, sample_tasks, sample_comments):
    ka = build_template_article(sample_case, sample_tasks, sample_comments, "nope")
    assert ka.product_key == "container-platform"


def test_generate_article_without_llm(monkeypatch, sample_case, sample_tasks, sample_comments):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ka = generate_article(sample_case, sample_tasks, sample_comments, "container-platform", use_llm=True)
    assert ka.generator == "template"
