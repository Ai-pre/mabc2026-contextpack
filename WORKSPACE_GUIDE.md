# ContextPack workspace MVP

ContextPack now stores named workspaces and uploaded source documents while retaining the existing Hermes CLI → Solar Pro 4 → ContextPack skill → mabc-sources MCP → Handoff pipeline. FastAPI validates and stores inputs; MCP performs lexical retrieval; the model and skill select tools and curate evidence. No database, embeddings, new model runtime, or fixed retrieval order is introduced.

## Run locally

Run these commands in a regular PowerShell terminal from the project root. No backend server was left running during implementation.

```powershell
Set-Location 'C:\Users\jaesa\mabc2026-mvp'
& 'C:\Users\jaesa\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe' -m pip install -r requirements.txt
npm --prefix frontend run build
& 'C:\Users\jaesa\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe' `
  -m uvicorn backend.main:app `
  --host 127.0.0.1 `
  --port 8000
```

Open [ContextPack locally](http://127.0.0.1:8000). The backend serves `frontend/dist` when built, retaining the old static page as a fallback. For frontend development, run `npm --prefix frontend run dev` in another terminal; Vite proxies the API to port 8000.

The existing Hermes configuration uses provider `upstage`, model `solar-pro4`, and the registered project `mabc_mcp_server.py`. Its registration now explicitly forwards the per-run workspace scope:

```yaml
mcp_servers:
  mabc-sources:
    # Existing command, args and timeouts remain in place.
    env:
      CONTEXTPACK_WORKSPACE_ID: '${CONTEXTPACK_WORKSPACE_ID}'
```

This entry was added to `C:\Users\jaesa\AppData\Local\hermes\config.yaml`. Hermes filters MCP subprocess environments, so the explicit entry is necessary. Other configuration values and the installed ContextPack skill were preserved. New oneshot processes discover all ten tools; the current non-lazy registration needs no schema-cache deletion.

## Workspace storage

```text
workspaces/
  demo/
    manifest.json
  ws_<generated UUID hex>/
    manifest.json
    sources/
      src_<generated UUID hex>.<extension>
    normalized/
      src_<generated UUID hex>.json
```

Workspace manifests contain `workspace_id`, `name`, `role`, `task`, `is_demo`, `created_at`, `updated_at`, and `sources`. The demo manifest is created lazily and references the four original fixtures in `data/demo_project`; those fixtures are unchanged. Upload directories are created when needed. Demo uploads are allowed, but the demo name and its fixture source records are protected.

Uploads are stored under generated source IDs, never user filenames. The display basename is preserved in metadata. Reads validate workspace IDs, resolved paths, source registration, and normalized document identity. Writes replace complete JSON files atomically and retry brief Windows sharing conflicts. The store uses one API process with in-process write locking; multiple Uvicorn workers are not supported.

## API

| Method and path | Request | Response |
|---|---|---|
| `GET /workspaces` | — | `{ "workspaces": [manifest, ...] }` |
| `POST /workspaces` | JSON: `name`, optional `role`, `task` | `201`, manifest |
| `GET /workspaces/{id}` | — | manifest |
| `PATCH /workspaces/{id}` | JSON: changed `name`, `role`, `task` | updated manifest |
| `GET /workspaces/{id}/sources` | — | `{ "sources": [...] }` |
| `POST /workspaces/{id}/sources` | Multipart field **`file`**, one file per request | `201`, `{ "success": true, "source": {...}, "warnings": [...] }` |
| `DELETE /workspaces/{id}/sources/{source_id}` | — | `{ "success": true, "source": {...} }` |
| `POST /analyze` | JSON: `role`, `task`, optional `workspace_id` | Existing result fields plus `workspace_id` |

Names are limited to 120 characters, roles to 200, and tasks to 4,000. Empty uploaded workspaces cannot be analyzed. Upload errors return JSON `detail`: 400 for empty/invalid input, 413 for size limits, 415 for unsupported extensions, and 422 for parsing failures. Unknown registered resources return 404.

The old request `{ "role": "...", "task": "..." }` resolves to `demo`. The response retains:

```json
{
  "success": true,
  "workspace_id": "demo",
  "duration_sec": 0,
  "handoff": "<actual CLI handoff>",
  "trace": { "skill_used": false, "mcp_tool_calls": 0 }
}
```

The numbers above illustrate the schema only. Runtime metrics come from the existing CLI runner and trace parser. Unobserved CLI trace events cannot be inferred from a successful answer.

## Parsing and normalized documents

Supported formats: **PDF, DOCX, TXT, MD, JSON**, at most **10 MiB per file** and **500,000 extracted characters**. Files exceeding extraction limits are rejected rather than silently shortened.

```json
{
  "id": "src_<UUID hex>",
  "workspace_id": "ws_<UUID hex>",
  "source_type": "upload",
  "title": "original-name.md",
  "body": "<normalized text>",
  "timestamp": null,
  "metadata": {
    "original_filename": "original-name.md",
    "mime_type": "text/markdown",
    "size_bytes": 123,
    "character_count": 456,
    "offset_unit": "unicode_code_points",
    "blocks": [{ "kind": "markdown", "start": 0, "end": 456 }]
  }
}
```

- PDF: extracted text with page numbers and body offsets; missing text on some pages produces warnings. Encrypted and image-only PDFs are rejected. There is no OCR. Extraction uses [pypdf](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).
- DOCX: paragraphs, heading styles, and tables are retained in document order with block offsets, using [python-docx](https://python-docx.readthedocs.io/en/latest/api/document.html). Package expansion and nesting are bounded.
- TXT/MD: strict UTF-8, optional BOM, normalized line endings.
- JSON: meaningful scalar values retain their key paths and order. Invalid values, duplicate object keys, excessive nesting, and oversized output are rejected.

Upload time is stored as source `created_at`; it is never presented as the document's effective date. Dates and versions in the content remain evidence for the skill to interpret.

## MCP contracts and scope

All tools retain JSON text responses. The original eight demo tool signatures and successful outputs remain unchanged within the demo scope:

| Tool | Inputs | Existing output |
|---|---|---|
| `github_search` | `query`, `limit=10` | Array: `item_id`, `type`, `title`, `status`, `snippet` |
| `github_get` | `item_id` | Original item; file content uses `content_preview` capped at 1,500 characters |
| `jira_search` | `query`, `limit=10` | Array: `item_id`, `type`, `summary`, `status`, `priority`, `snippet` |
| `jira_get` | `item_id` | Original issue including comments |
| `slack_search` | `query`, `limit=10` | Array: `item_id`, `type`, `channel`, `user`, `timestamp`, `text` |
| `slack_get` | `item_id` | `item_id`, `channel`, original `message` |
| `notion_search` | `query`, `limit=10` | Array: `item_id`, `type`, `title`, `snippet` |
| `notion_get` | `item_id` | `item_id`, `type`, `title`, `content` |

`document_search(workspace_id, query, limit=10, offset=0)` returns `results`, `total_matches`, and `next_offset`. Results contain source IDs, title, timestamp, snippet, lexical score, and a body offset. Empty queries list document metadata; search terms support Korean and English. Limits: query 500 characters, result limit 1–50.

`document_get(workspace_id, document_id, offset=0, max_chars=12000)` returns the normalized document with a body slice, relevant page/block metadata, warnings, `total_chars`, `next_offset`, and an explicit `truncated` flag. `max_chars` is limited to 20,000; offsets use Unicode code points.

The parent Hermes process supplies `CONTEXTPACK_WORKSPACE_ID`. Each tool invocation checks that bound scope; document tools cannot switch workspaces by changing an argument, and demo tools reject uploaded-workspace runs. Only an absent/empty scope or the exact unresolved registration placeholder defaults to demo for legacy direct usage. Malformed scopes fail closed.

The prompt provides workspace metadata and available tool names, not raw documents. Hermes chooses queries and tools. The unchanged ContextPack skill still decides relevance, stale information, conflicts, missing facts, and the final Handoff. The generic prompt no longer forces fabricated nonempty conflict/missing sections for unrelated tasks.

## Frontend and verification

The existing dark shell is retained. Workspace selection, inline naming, upload/removal, saved role/task, and a clear distinction between uploaded files and demo fixtures are part of the workspace flow. Analysis sends the selected workspace ID. Loading is a temporary assistant card; only elapsed time is dynamic. Errors stay in the shell and allow retry. Copy Handoff copies the raw returned Handoff.

The result-collapse cause was `workspace-area` using a column layout while the inspector claimed almost a viewport of height, with main content clipped by hidden overflow. The fix uses a row layout with a flexible `min-width: 0` main panel and a 380–420px nonshrinking inspector. Smaller screens stack the panels. Sidebar space and scrolling are explicit. The empty Role input and the missing opening bracket in `SECTION_RE` were also fixed; all eight original sections now parse, including constraints and the source map.

Run automated checks without launching an HTTP server or an LLM:

```powershell
& 'C:\Users\jaesa\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe' -m unittest discover -s tests -v
npm --prefix frontend run build
npm --prefix frontend run lint
node --test frontend/src/utils.test.js
```

Tests use temporary stores/in-memory samples and the existing demo fixtures. They cover workspace/API contracts, format parsing, path and identity isolation, legacy analysis compatibility, CLI invocation arguments, and actual MCP stdio discovery/retrieval/scope rejection. CLI analysis tests substitute the runner boundary; they do not claim a fresh Solar-generated result.

For the final live check, launch the backend manually using the command above. Run **Load Demo → Build Context**, then create a workspace and upload your own current, outdated, conflicting, and irrelevant sources through **Add Sources**. Enter the intended role/task and build again. Check the returned evidence, STALE/CONFLICT/MISSING handling, exclusions, source references, trace metrics, processing card, inspector, and clipboard. No sample user documents were seeded into the app.

Remaining limits: local single-process storage; lexical search without embeddings; no live SaaS accounts, authentication, OCR, or analysis history persistence. Complex document layouts may not extract perfectly. A fresh full Hermes/Solar end-to-end run and user-document curation remain manual validation steps under the requested server-launch rule.

## Implementation inventory

Created core files: `backend/workspace_store.py`, `backend/document_parser.py`, and `requirements.txt`. Added this guide and five Python test modules under `tests/`, plus `frontend/src/utils.test.js`.

Modified runtime files: `backend/main.py`, the root `hermes_runner.py` actually imported by that API, and `mabc_mcp_server.py`. The separate pre-existing `backend/hermes_runner.py` and legacy root `main.py` were inspected and left intact.

Modified frontend files: `frontend/src/App.jsx`, `frontend/src/index.css`, `frontend/src/utils.js`, `frontend/src/components/AgentComposer.jsx`, `AssistantCard.jsx`/`.css`, `ContextInspector.jsx`/`.css`, `Sidebar.jsx`/`.css`, and `frontend/vite.config.js`. Production assets under `frontend/dist` are regenerated by the build.

New direct dependencies: `pypdf==6.18.1`, `python-docx==1.2.0`. Installation also added `lxml==6.1.3`, required by python-docx. Existing FastAPI, Uvicorn, Pydantic, MCP, and multipart versions are recorded in the requirements file.

The four demo fixture checksums and the installed ContextPack skill checksum were verified unchanged. New implementation filenames use product/responsibility names only.
