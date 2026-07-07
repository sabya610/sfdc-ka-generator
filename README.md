# SFDC Knowledge Article Generator

A web app that drafts **HPE Ezmeral** Salesforce Knowledge Articles (KA) from
support cases. It reads a case's **resolution steps**, **task steps** (e.g. the
"Troubleshooting" tasks logged by engineers), and **case comments**, then
produces a structured KA draft (Issue / Cause / Pre-requisites / Resolution
steps) tagged for **Container Platform** or **Data Fabric**.

## How it works

```
User email  ─┐
Case number ─┼─► Salesforce (SOQL) ─► case + tasks + comments ─► KA generator ─► KA draft
Product     ─┘                                                     (template or LLM)
```

* **Input**: a case-owner email (lists that user's cases) and/or a case number.
* **Case types**: `GSD CSC Case Closed`, `GSD CSC Case Open` (+ Creation), and
  `GSD Elevation` cases — selectable in the UI.
* **Resolution source**: `Resolution__c` on the case, then Task `Description`
  (task steps), then Case Comments — in that priority order.
* **Product**: Container Platform / Unified Analytics, or Data Fabric — controls
  the KA product tagging (Product Group / Queue / Line) and Environment.
* **Drafting**: deterministic template by default; set `OPENAI_API_KEY` to have
  an LLM polish the prose (falls back to template on any error).

## Endpoints

| Method | Path            | Purpose                                        |
| ------ | --------------- | ---------------------------------------------- |
| GET    | `/`             | Web UI                                         |
| GET    | `/healthz`      | Health probe                                   |
| POST   | `/api/cases`    | List cases for a user email                    |
| POST   | `/api/generate` | Generate a KA draft from a case number         |
| POST   | `/api/publish`  | Best-effort create a draft `Knowledge__kav`    |

## Configuration (environment variables)

| Variable         | Default                        | Description                                   |
| ---------------- | ------------------------------ | --------------------------------------------- |
| `SF_URL`         | `https://hp.my.salesforce.com` | Salesforce instance URL                       |
| `SF_SID`         | —                              | Salesforce session id (or a path to a file)   |
| `SF_SID_FILE`    | —                              | Path to a file containing the session id      |
| `OPENAI_API_KEY` | —                              | Optional; enables LLM drafting                |
| `KA_BODY_FIELD`  | `Details__c`                   | API name of the KA rich-text body field       |
| `PORT`           | `8080`                         | Listen port                                   |

> The Salesforce session id is the `sid` cookie from an authenticated
> `hp.my.salesforce.com` browser session. It is short-lived; refresh as needed.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SF_SID = Get-Content C:\Users\malliks\sid.txt -Raw
python -m app.server
# open http://localhost:8080
```

## Test

```powershell
pip install -r requirements-dev.txt
pytest -q
```

## Docker

```powershell
docker build -t sabya610/sfdc-ka-generator:0.1.0 .
docker login -u sabya610
docker push sabya610/sfdc-ka-generator:0.1.0
```

## Helm

```powershell
# Package the chart (produces sfdc-ka-generator-0.1.0.tgz)
helm package helm/sfdc-ka-generator

# Install
helm install ka-gen ./sfdc-ka-generator-0.1.0.tgz `
  --set secret.SF_SID=<your-sid> `
  --set image.repository=sabya610/sfdc-ka-generator `
  --set image.tag=0.1.0
```

## License

Internal HPE tooling.
