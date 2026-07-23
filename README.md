# SFDC Knowledge Article Generator

A web application that drafts **HPE Ezmeral** Salesforce Knowledge Articles (KA)
directly from support cases. It reads a case's **resolution steps**, engineer
**task steps** (the "Troubleshooting" tasks logged on the case), and **case
comments**, then produces a structured KA draft (Title / Summary / Issue / Cause /
Pre-requisites / Resolution steps) tagged for **Container Platform** or **Data
Fabric**, and can publish it back to Salesforce as a draft `Knowledge__kav`.

Drafting runs in one of two modes:

* **Template** (deterministic) — parses the case/task text into KA sections.
* **LLM** (optional) — sends the extracted content to an **offline llama3.1‑8b**
  model (running in the `rag-app` pod) to rewrite it into clean, customer‑safe
  prose. Falls back to the template on any error.

---

## Table of contents

1. [Architecture & workflow](#architecture--workflow)
2. [Repository layout](#repository-layout)
3. [Endpoints](#endpoints)
4. [Configuration](#configuration-environment-variables)
5. [Installation](#installation)
6. [Code workflow (internals)](#code-workflow-internals)
7. [Testing](#testing)
8. [Fixes & changelog](#fixes--changelog)

---

## Architecture & workflow

```
                        ┌──────────────────────────────────────────────┐
User email  ─┐          │  Flask app (app/server.py)                    │
Case number ─┼─► UI ───►│  ┌────────────┐   ┌──────────────────────┐    │
Product     ─┘          │  │ sfdc_client│──►│ ka_generator          │   │
                        │  │ (SOQL/REST)│   │  template  or  LLM     │   │
                        │  └─────┬──────┘   └───────────┬──────────┘    │
                        └────────┼──────────────────────┼──────────────┘
                                 │                       │
                    Salesforce (hp.my.salesforce.com)    │  LLM_ENDPOINT
                    case + tasks + comments        rag-app /api/llm (llama3.1-8b)
```

* **Input** — a case‑owner email (lists that user's cases) and/or a case number,
  plus the target product.
* **Case types** — `GSD CSC Case Closed`, `GSD CSC Case Open` (+ Creation) and
  `GSD Elevation` cases, selectable in the UI.
* **Resolution source priority** — `Resolution__c` on the case → Task
  `Description` (task steps) → Case Comments.
* **Product** — Container Platform / Unified Analytics or Data Fabric; controls KA
  product tagging (Product Group / Queue / Line) and Environment.
* **Output** — a structured KA draft that can be reviewed in the UI and published
  back to Salesforce as a draft `Knowledge__kav` (submitted for technical review).

---

## Repository layout

```
sfdc-ka-generator/
├── app/
│   ├── server.py            # Flask app: routes, KA field mapping, publish logic
│   ├── ka_generator.py      # Core: task parsing, template + LLM drafting, product catalog
│   ├── sfdc_client.py       # Salesforce REST/SOQL client, product tagging
│   └── templates/
│       └── index.html       # Web UI (form + result rendering)
├── helm/sfdc-ka-generator/  # Helm chart (deployment, service, ingress, istio, secret, configmap)
│   ├── Chart.yaml           # chart version / appVersion (image tag)
│   ├── values.yaml          # image, env (SF_URL, LLM_ENDPOINT), secrets (SF_SID, OPENAI_API_KEY)
│   └── templates/
├── tests/                   # pytest suite
├── Dockerfile               # Gunicorn image (timeout tuned for LLM inference)
├── requirements.txt         # runtime deps (Flask, gunicorn, requests)
├── requirements-dev.txt     # test deps (pytest)
└── README.md
```

---

## Endpoints

| Method | Path            | Purpose                                            |
| ------ | --------------- | -------------------------------------------------- |
| GET    | `/`             | Web UI                                             |
| GET    | `/healthz`      | Liveness / readiness probe                         |
| POST   | `/api/cases`    | List cases for a user email                        |
| POST   | `/api/generate` | Generate a KA draft from a case number             |
| POST   | `/api/publish`  | Create a draft `Knowledge__kav` and submit for review |

`/api/generate` body: `{ "case_number": "...", "product": "container-platform", "use_llm": true|false }`.
When `use_llm` is true and `LLM_ENDPOINT` is set, the article is drafted by the
offline LLM (`generator: rag-llm:llama3.1-8b`); otherwise the deterministic
template is used (`generator: template`).

---

## Configuration (environment variables)

| Variable         | Default                                                        | Description                                    |
| ---------------- | ------------------------------------------------------------- | ---------------------------------------------- |
| `SF_URL`         | `https://hp.my.salesforce.com`                                | Salesforce instance URL                        |
| `SF_SID`         | —                                                             | Salesforce session id (or a path to a file)    |
| `SF_SID_FILE`    | —                                                             | Path to a file containing the session id       |
| `LLM_ENDPOINT`   | `http://rag-app-service.rag-app.svc.cluster.local:80/api/llm` | Offline llama3.1‑8b endpoint (enables LLM mode)|
| `OPENAI_API_KEY` | —                                                             | Optional; OpenAI fallback drafting             |
| `KA_*_FIELD`     | GSD Issue/Solution field API names                            | Override KA publish field mapping              |
| `KA_RECORD_TYPES_JSON` | built‑in map                                            | Override Article Type → RecordTypeId map       |
| `PORT`           | `8080`                                                        | Listen port                                    |

> `SF_SID` is the `sid` cookie from an authenticated `hp.my.salesforce.com`
> browser session. It is short‑lived — refresh it when Salesforce returns
> `INVALID_SESSION_ID`.

---

## Installation

### 1. Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SF_SID = Get-Content C:\Users\malliks\sid.txt -Raw
# optional: enable LLM mode against a reachable rag-app
# $env:LLM_ENDPOINT = "http://<rag-app-host>/api/llm"
python -m app.server
# open http://localhost:8080
```

### 2. Docker

```powershell
docker build -t sabya610/sfdc-ka-generator:0.1.20 .
docker login -u sabya610
docker push sabya610/sfdc-ka-generator:0.1.20
```

> The image runs Gunicorn with `--timeout 600` because a full‑KA LLM inference on
> CPU can take 2–4 minutes.

### 3. Helm / Kubernetes

```powershell
# Package the chart (produces sfdc-ka-generator-0.3.2.tgz)
helm package helm/sfdc-ka-generator

# Install / upgrade
helm upgrade --install ka-gen ./sfdc-ka-generator-0.3.2.tgz `
  --namespace ka-sfdc-app --create-namespace `
  --set image.repository=sabya610/sfdc-ka-generator `
  --set image.tag=0.1.20 `
  --set secret.SF_SID=<your-sid>
```

Rotate the Salesforce session id on a running deployment:

```bash
kubectl -n ka-sfdc-app patch secret sfdc-ka-generator --type merge \
  -p "{\"data\":{\"SF_SID\":\"$(printf %s '<sid>' | base64 -w0)\"}}"
kubectl -n ka-sfdc-app rollout restart deployment/sfdc-ka-generator
```

---

## Code workflow (internals)

1. **Fetch** — `sfdc_client.Salesforce` resolves the case by number, then loads
   its Tasks and Case Comments via SOQL.
2. **Parse** — `ka_generator._parse_structured_task` extracts Issue / Cause /
   Resolution from each task's `Description`. It supports **two formats**:
   * *Divider style* — `Issue Summary` / `Root Cause Analysis` / `Resolution`
     separated by `====` / `----` lines.
   * *Colon style* — `Issue:` / `Root cause:` / `Resolution:` headers with no
     dividers.
   HTML rich‑text is first normalised (block tags → newlines) so sections don't
   collapse. Ignore‑headers (`Troubleshooting Steps Performed`, `How we fixed
   it`, `Note`, …) terminate a section. Identical resolution bodies across tasks
   are de‑duplicated.
3. **Assemble** — `build_template_article` builds a `KnowledgeArticle` with
   title, summary, issue, cause, numbered steps, and product metadata from
   `PRODUCT_CATALOG`.
4. **LLM polish (optional)** — `generate_article(use_llm=True)` dispatches to
   `build_rag_llm_article`, which sends the template's issue/cause/resolution to
   the `rag-app` LLM, parses the returned `TITLE/SUMMARY/ISSUE/CAUSE/RESOLUTION`
   sections, renumbers steps, and masks IPs/usernames. Any exception falls back
   to the template.
5. **Publish** — `/api/publish` creates a draft `Knowledge__kav`, maps Issue /
   Cause / Resolution to the GSD KM Issue/Solution rich‑text fields, sets product
   tagging via a **two‑step** `KM_ProductAttribute__c` + `KM_ProductAttributeTag__c`
   create, assigns the owner by email, and submits it for technical review.

---

## Testing

```powershell
pip install -r requirements-dev.txt
pytest -q
```

---

## Fixes & changelog

| Version | Change |
| ------- | ------ |
| 0.1.20  | **LLM path end‑to‑end**: raise Gunicorn `--timeout` to 600s and LLM `urlopen` timeout to 360s, `max_tokens` 1024 — full‑KA llama3.1‑8b inference (~220s on CPU) no longer 502s. |
| 0.1.19  | Restore colon‑format parser; de‑duplicate identical resolution steps; product tagging via two‑step `KM_ProductAttribute__c` + `KM_ProductAttributeTag__c`. |
| 0.1.16  | Parse colon‑style task sections with no dividers; ignore `Troubleshooting Steps` / `How we fixed it` / `Note` headers. |
| 0.1.15  | Populate KA metadata (Product Group/Queue/Line, Disclosure, IC Check, Internal Notes = case #, Article Type record types); fix Data Fabric product line to `PU`. |
| 0.1.14  | Remove `/api/debug` diagnostic endpoint. |
| 0.1.13  | Set KA owner by email + submit for technical review; return article number/link on publish. |
| 0.1.12  | Publish to GSD Issue/Solution KA fields (fixes `INVALID_FIELD Details__c`). |
| 0.1.11  | Remove owner‑email filter — select KA task by Resolution section, not case owner. |
| 0.1.9   | Fix HTML task `Description` parsing (block tags → newlines). |
| 0.1.8   | Skip tasks without a Resolution section — no debugging junk in steps. |
| 0.1.7   | Offline LLM via `rag-app` `/api/llm`; step formatting (numbered/bullet/code/log). |
| 0.1.0   | Initial: app, tests, Dockerfile, Helm chart. |

---

## License

Internal HPE tooling.
