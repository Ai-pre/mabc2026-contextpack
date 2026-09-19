# ContextPack

> **Task-conditioned Context Handoff Agent**  
> 흩어진 프로젝트 자료에서 현재 Task에 필요한 맥락만 골라, 다음 작업 주체가 바로 사용할 수 있는 Handoff Context로 전달합니다.

🏆 **MABC 2026 Finals — NIA 원장상 수상**

## Overview

요즘은 AI에게 단순히 질문하는 것을 넘어, 실제 업무 일부를 Agent에게 맡기는 방식으로 사용 형태가 바뀌고 있습니다.  
하지만 Agent에게 일을 맡기기 전에는 사람이 여전히 관련 자료를 다시 찾고, 최신 정보와 충돌하는 결정사항을 구분하고, 필요한 제약조건을 프롬프트로 다시 정리해야 합니다.

**ContextPack**은 이 "업무를 맡기기 전 맥락 정리 과정"을 자동화합니다.

사용자가 **Role + Task + Project Sources**를 입력하면, ContextPack은 모든 자료를 요약하는 대신 현재 Task에 필요한 Evidence를 탐색하고 다음과 같이 구조화합니다.

- 확정된 정보
- 제약사항
- 서로 충돌하는 정보
- 최신성 검증이 필요한 정보
- 현재 자료에 없는 정보
- 각 정보의 원본 출처

즉, 단순 문서 요약이 아니라 **다음 작업을 위한 Agent-ready Context를 컴파일하는 서비스**입니다.

## Core Output

ContextPack은 항상 아래 8개 섹션으로 Handoff Context를 생성합니다.

```text
[TASK]
[MUST KNOW]
[CONSTRAINTS]
[USEFUL IF SPACE ALLOWS]
[UNRESOLVED CONFLICTS]
[VERIFY BEFORE USE]
[DO NOT ASSUME]
[SOURCE MAP]
```

핵심 원칙은 **검색된 정보를 전부 넘기지 않고, 다음 작업 수행에 필요한 최소 맥락만 안전하게 전달하는 것**입니다.

## Key Features

### 1. Task-conditioned Retrieval
같은 자료라도 Role과 Task가 달라지면 필요한 Context가 달라집니다.  
ContextPack은 현재 작업에 필요한 Source와 Evidence만 탐색합니다.

### 2. Handoff Safety
- **CONFLICT**: 충돌하는 정보를 임의로 하나로 합치지 않음
- **STALE**: 오래되었거나 최신성 확인이 필요한 정보는 검증 대상으로 분리
- **MISSING**: 자료에 없는 내용은 추정하지 않고 `DO NOT ASSUME`으로 명시
- **SOURCE MAP**: 최종 Context의 근거를 추적 가능하게 유지

### 3. Workspace Isolation
각 Workspace는 자신의 corpus 안에서만 검색됩니다.  
다른 프로젝트의 Source가 섞이지 않도록 `workspace_id` 기준으로 retrieval scope를 제한합니다.

## Architecture

```text
User
  ↓
Google Cloud Compute Engine
  ↓
Docker
  ↓
FastAPI
  ↓
Hermes Agent Runtime
  ↓
Solar Pro 4
  ↕
MCP Tools
  ↓
ContextPack Skill
  ↓
Agent-ready Handoff Context
```

### Component Roles

- **FastAPI** — Workspace / Source / Analyze API
- **Hermes** — Agent runtime, Skill activation, MCP tool-use orchestration
- **Solar Pro 4** — Role/Task 이해, 검색 query 생성, tool selection 및 Evidence 해석
- **MCP Server** — Source별 검색/조회 Tool 제공
- **ContextPack Skill** — Evidence 선별, conflict/stale/missing 처리, 최종 Handoff 생성

## MCP Tools

현재 MCP server는 다음 Tool interface를 제공합니다.

```text
document_search / document_get
github_search   / github_get
jira_search     / jira_get
slack_search    / slack_get
notion_search   / notion_get
```

### Current MVP Scope

- **Uploaded documents**: 실제 동작
  - PDF
  - DOCX
  - TXT
  - MD
  - JSON
- **Uploaded documents**: 자체 MCP server의 `document_search/document_get`으로 실제 동작
- **GitHub**: Workspace-scoped `github_retrieve` MCP Tool이 GitHub API의 live read-only evidence를 한 번에 수집
- **Jira / Slack / Notion**: 현재 demo용 mock connector
  - Agent가 Task에 따라 필요한 Source/Tool을 선택하는 흐름 검증

GitHub 연결은 Workspace에 `owner/repo`를 Source로 등록하고, Hermes가 `mabc-sources` MCP의 `github_retrieve`를 우선 호출하도록 구성됩니다. 이 Tool은 recent commits/PRs, 관련 PR 변경 파일, repository tree 요약, README 맥락을 한 번에 반환해 granular tool loop를 줄입니다. 공식 GitHub Remote MCP는 정확한 세부 확인이 필요한 경우에만 opt-in fallback으로 사용할 수 있습니다. Jira/Slack/Notion도 같은 retrieve-first Connector 패턴으로 확장할 예정입니다.

## Real Workspace Path

The production-oriented path is source-driven rather than demo-specific.

```text
Workspace
├── Uploaded documents → mabc-sources / document_retrieve
├── Connected GitHub   → mabc-sources / github_retrieve
├── Connected Slack    → mabc-sources / slack_retrieve
└── Connected Notion   → mabc-sources / notion_retrieve
                         ↓
                      Hermes
                         ↓
                    Solar Pro 4
                         ↓
                  ContextPack Skill
                         ↓
                  Handoff Context
```

A real workspace only exposes evidence from sources registered to that workspace. All live connectors follow a retrieve-first design: each connector returns a bounded task-relevant evidence bundle instead of forcing the model through long search/get loops.

- `github_retrieve`: recent commits/PRs, selected PR changed files, repository structure and README context
- `slack_retrieve`: metadata and recent messages from a registered Slack channel, ranked locally against the task
- `notion_retrieve`: metadata and recursive block content from a registered Notion page, compacted to relevant blocks
- `document_retrieve`: lexical search plus bounded reads for uploaded files

The official granular GitHub Remote MCP remains an opt-in detail fallback with `GITHUB_REMOTE_MCP_ENABLED=1`; it is disabled by default. Jira remains demo-only for now.

Completed analyses return an execution trace containing the exact MCP tool names that were used. The UI shows this trace so live-connector E2E runs can be verified without inferring tool usage from the generated Handoff.

## Demo Scenario

대표 데모는 **3주 만에 프로젝트에 복귀한 Backend Developer** 시나리오입니다.

```text
Role:
3주 만에 프로젝트에 복귀한 백엔드 개발자

Task:
결제 모듈의 부분환불 기능 수정
```

데모 데이터에는 의도적으로 다음 상황을 섞었습니다.

- 오래된 Notion 문서: PG API v2 / 부분환불 미지원
- 최신 GitHub PR: PG API v3 전환 / PARTIAL_REFUND 추가
- Jira와 Slack 간 환불 기간 정책 충돌
- 해외결제 부분환불 정책 미정
- Frontend / Marketing / SRE 등 현재 Task와 무관한 정보

이를 통해 **STALE / CONFLICT / MISSING / IRRELEVANT** 처리와 Source-bounded Handoff를 검증합니다.

## Retrieval

현재 업로드 문서 검색은 **lexical search** 기반입니다.

Solar가 Role과 Task를 기반으로 검색어를 생성하고, 결과가 부족하면 다른 query 또는 Source를 사용해 추가 탐색할 수 있습니다.

현재 버전은 embedding 기반 semantic retriever를 포함하지 않습니다.

## Safety Principles

ContextPack Skill은 다음 원칙을 따릅니다.

- **Source-bounded** — 제공된 자료에 없는 사실을 채우지 않음
- **Retrieval ≠ Inclusion** — 검색됐다고 모두 포함하지 않음
- **Conflict-aware** — 충돌을 조용히 하나로 합치지 않음
- **Freshness-aware** — 날짜/버전을 보존하고 최신 여부를 임의로 추정하지 않음
- **No information laundering** — "검토 중"을 "확정"으로 바꾸지 않음
- **Least Context Principle** — 다음 Agent에게 필요한 최소 Context만 전달

## Validation

- Workspace A/B corpus isolation
- 실제 MCP stdio tool discovery / retrieval
- cross-workspace access rejection
- PDF / DOCX / TXT / MD / JSON parsing
- search / get pagination
- irrelevant query → zero-match retrieval
- fixed 8-section Handoff format
- Hermes CLI + ContextPack Skill invocation

## Run Locally

### 1. Install dependencies

```bash
pip install -r requirements.txt
npm --prefix frontend install
```

### 2. Build frontend

```bash
npm --prefix frontend run build
```

### 3. Configure live connectors

All credentials stay server-side and are passed to the custom `mabc-sources` MCP process through environment variables.

```bash
export GITHUB_MCP_TOKEN=github_read_token
export SLACK_BOT_TOKEN=xoxb-slack_bot_token
export NOTION_API_KEY=notion_token
```

GitHub should use a fine-grained token with read-only access to only the repositories ContextPack is allowed to inspect.

For Slack, install a Slack app in the target workspace and give its bot the history scope required by the channel type (for example `channels:history` for public channels and `groups:history` for private channels). Add the bot to the channel before registering that channel in ContextPack. `channels:read` / `groups:read` are optional and only improve channel metadata such as the human-readable name/topic.

For Notion, create a connection/integration with read-content access and grant it access to the page that will be registered in ContextPack. ContextPack uses Notion API version `2026-03-11`.

The optional granular GitHub Remote MCP fallback is disabled by default. Enable it only when exact detail lookup is needed:

```bash
export GITHUB_REMOTE_MCP_ENABLED=1
```

### 4. Run FastAPI

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

> Hermes configuration and an Upstage API key for Solar Pro 4 are required for full analysis execution.

## Project Structure

```text
.
├── backend/                  # FastAPI, workspace storage, document parser
├── frontend/                 # React/Vite UI
├── deploy/hermes/            # Hermes config + frozen ContextPack Skill
├── data/demo_project/        # GitHub/Jira/Slack/Notion demo fixtures
├── tests/                    # API, parser, MCP, workspace tests
├── workspaces/               # Local workspace manifests/sources
├── mabc_mcp_server.py        # Custom MCP server
├── hermes_runner.py          # Hermes CLI runner
├── Dockerfile
└── WORKSPACE_GUIDE.md
```

## Limitations & Roadmap

Current MVP limitations:

- lexical retrieval only
- GitHub, Slack and Notion currently use server-side credentials; per-user OAuth/App authorization is not implemented yet
- Slack live retrieval is channel-scoped and requires the installed bot to have access to that channel
- Notion live retrieval currently supports registered pages; database/data-source-specific retrieval is not implemented yet
- Jira remains demo-only
- local single-process workspace storage
- no OCR for image-only PDFs
- no enterprise authentication / permission model
- latency optimization remains

Next steps:

- embedding-based semantic retrieval
- GitHub App/OAuth, Slack OAuth and Notion public-connection authorization
- Jira live connector
- GCS/DB-based persistent workspace storage
- retrieval caching and parallel tool calls
- downstream task-quality evaluation

## Live Demo

```text
http://34.47.84.95/
```

## Award

**MABC 2026 Finals — NIA 원장상**

---

ContextPack focuses on one question:

> **What is the minimum reliable context the next agent needs to do the task correctly?**
