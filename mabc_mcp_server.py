# mabc_mcp_server.py — MVP용 경량 MCP stdio server
# 4개 demo connector와 workspace 업로드 문서를 MCP tool로 노출한다.
# Solar Pro 4가 Role+Task에 따라 필요한 tool을 스스로 선택·호출한다.

from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Any

from mcp.server import MCPServer
from backend.workspace_store import WorkspaceStore, WorkspaceValidationError


# ---------------------------------------------------------------------------
# 데이터 로딩 (demo_project/ mock 데이터)
# ---------------------------------------------------------------------------

_BASE = pathlib.Path(__file__).resolve().parent / "data" / "demo_project"
_STORE = WorkspaceStore()


def _require_workspace(workspace_id: str | None = None, *, demo_only: bool = False) -> str:
    """Bind every invocation to the corpus chosen by the parent Hermes process."""
    scope = os.environ.get("CONTEXTPACK_WORKSPACE_ID", "")
    if scope in {"", "${CONTEXTPACK_WORKSPACE_ID}"}:
        scope = "demo"
    _STORE.get_workspace(scope)
    if workspace_id is not None and workspace_id != scope:
        raise WorkspaceValidationError("Requested workspace does not match this analysis workspace.")
    if demo_only and scope != "demo":
        raise WorkspaceValidationError("Demo fixture tools are only available in the demo workspace.")
    return scope


def _load_json(name: str) -> dict:
    with open(_BASE / name, encoding="utf-8") as f:
        return json.load(f)


def _load_md(name: str) -> str:
    with open(_BASE / name, encoding="utf-8") as f:
        return f.read()


_GITHUB = _load_json("github.json")
_JIRA = _load_json("jira.json")
_SLACK = _load_json("slack.json")
_NOTION = _load_md("notion.md")


def _split_notion_sections(md: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    current_id: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []
    for line in md.splitlines():
        if line.startswith("# "):
            if current_id is not None:
                sections.append({"id": current_id, "title": current_title or "Untitled",
                                 "content": "\n".join(current_lines)})
            current_id = line.strip().replace("# ", "").replace(" ", "-").lower()
            current_title = line.strip().replace("# ", "")
            current_lines = []
        else:
            current_lines.append(line)
    if current_id is not None:
        sections.append({"id": current_id, "title": current_title or "Untitled",
                         "content": "\n".join(current_lines)})
    return sections


_NOTION_SECTIONS = _split_notion_sections(_NOTION)


# ---------------------------------------------------------------------------
# tool 정의
# ---------------------------------------------------------------------------

server = MCPServer("mabc-sources", "1.0.0",
                   description="ContextPack workspace source connector. " +
                   "업로드 문서와 demo workspace의 GitHub, Jira, Slack, Notion 데이터를 제공한다. " +
                   "Solar Agent가 Role+Task에 따라 필요한 tool을 선택하여 호출한다.")


def _tool(name: str, description: str, fn):
    """서버에 tool 등록."""
    server.add_tool(fn, name=name, description=description)


# --- Demo fast path ---

@server.tool()
def demo_context_retrieve(query: str) -> str:
    """Return the task-relevant demo evidence bundle in one MCP call.

    The demo fixture is intentionally small and fixed. This fast path preserves
    source boundaries while avoiding repeated Solar -> search/get -> Solar
    round trips across GitHub, Jira, Slack and Notion.

    It excludes known irrelevant frontend/marketing/SRE fixtures and returns the
    backend/payment evidence needed to detect stale, conflicting and missing
    information for the partial-refund scenario.
    """
    _require_workspace(demo_only=True)
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or "\x00" in query:
        raise WorkspaceValidationError("query must be non-empty text of at most 500 characters.")

    github_ids = {"pr-148", "pr-152", "payment_service.py", "refund_service.py"}
    jira_ids = {"PAY-176", "PAY-179", "PAY-181", "PAY-183"}

    github = [item for item in _GITHUB.get("items", []) if item.get("id") in github_ids]
    jira = [item for item in _JIRA.get("issues", []) if item.get("id") in jira_ids]
    slack = [
        {"channel": "payment-eng", **message}
        for message in _SLACK.get("channels", {}).get("payment-eng", [])
    ]
    notion = [
        {"id": section["id"], "title": section["title"], "content": section["content"]}
        for section in _NOTION_SECTIONS
    ]

    return json.dumps({
        "workspace_id": "demo",
        "query": query,
        "sources": {
            "github": github,
            "jira": jira,
            "slack": slack,
            "notion": notion,
        },
        "provenance": {
            "github": "data/demo_project/github.json",
            "jira": "data/demo_project/jira.json",
            "slack": "data/demo_project/slack.json#payment-eng",
            "notion": "data/demo_project/notion.md",
        },
    }, ensure_ascii=False, indent=2)


# --- GitHub ---

@server.tool()
def github_search(query: str, limit: int = 10) -> str:
    """GitHub (mock)에서 쿼리 검색. PR, 코드 파일, diff 요약 반환.

    사용 예:
    - PG API 버전 확인 → query="v3" 또는 "PG API v3 migration"
    - partial refund 관련 PR → query="PARTIAL_REFUND"
    - payment_service 코드 확인 → query="payment_service"
    """
    _require_workspace(demo_only=True)
    q = query.lower()
    items = _GITHUB.get("items", [])
    results: list[dict[str, Any]] = []
    for it in items:
        text = " ".join(str(v) for v in it.values()).lower()
        if q in text:
            results.append({"item_id": it["id"], "type": it["type"],
                            "title": it.get("title", it.get("path", "")),
                            "status": it.get("status", ""),
                            "snippet": _github_snippet(it, q)})
            if len(results) >= limit:
                break
    return json.dumps(results, ensure_ascii=False, indent=2)


def _github_snippet(it: dict, q: str) -> str:
    body = it.get("body", it.get("content", ""))
    if not body:
        return ""
    idx = body.lower().find(q)
    if idx == -1:
        return body[:200]
    s = max(0, idx - 60); e = min(len(body), idx + 200)
    return body[s:e]


@server.tool()
def github_get(item_id: str) -> str:
    """GitHub mock 항목 상세 조회. item_id 예: pr-148, pr-152, payment_service.py.

    PG API 버전, 부분환불 관련 코드 변경, PR 내용 확인에 사용.
    """
    _require_workspace(demo_only=True)
    for it in _GITHUB.get("items", []):
        if it["id"] == item_id:
            out = dict(it)
            # 긴 파일 내용은 축약
            if out.get("type") == "file" and out.get("content"):
                out["content_preview"] = out["content"][:1500]
                del out["content"]
            return json.dumps(out, ensure_ascii=False, indent=2)
    return json.dumps({"error": f"item_id '{item_id}' not found"}, ensure_ascii=False)


# --- Jira ---

@server.tool()
def jira_search(query: str, limit: int = 10) -> str:
    """Jira (mock)에서 쿼리 검색. 이슈, 요구사항, 상태, 코멘트 포함.

    사용 예:
    - 부분환불 요구사항 → query="partial refund" 또는 "PAY-183"
    - 환불 기간 변경 → query="refund window" 또는 "14일"
    - PG 마이그레이션 → query="v3" 또는 "PAY-176"
    - 특정 결제 유형 예외 → query="overseas" 또는 "해외결제"
    """
    _require_workspace(demo_only=True)
    q = query.lower()
    issues = _JIRA.get("issues", [])
    results: list[dict[str, Any]] = []
    for it in issues:
        text = " ".join(str(v) for v in it.values()).lower()
        if q in text:
            results.append({"item_id": it["id"], "type": "issue",
                            "summary": it.get("summary", ""),
                            "status": it.get("status", ""),
                            "priority": it.get("priority", ""),
                            "snippet": _jira_snippet(it, q)})
            if len(results) >= limit:
                break
    return json.dumps(results, ensure_ascii=False, indent=2)


def _jira_snippet(issue: dict, q: str) -> str:
    text = f"{issue.get('summary','')} {issue.get('description','')}"
    for c in issue.get("comments", []):
        text += f" [댓글] {c.get('body','')}"
    idx = text.lower().find(q)
    if idx == -1:
        return text[:200]
    s = max(0, idx - 60); e = min(len(text), idx + 200)
    return text[s:e]


@server.tool()
def jira_get(item_id: str) -> str:
    """Jira mock 이슈 상세 조회. item_id 예: PAY-183, PAY-179, PAY-181.

    상태, 코멘트, 요구사항 상세 확인에 사용.
    """
    _require_workspace(demo_only=True)
    for it in _JIRA.get("issues", []):
        if it["id"] == item_id:
            return json.dumps(it, ensure_ascii=False, indent=2)
    return json.dumps({"error": f"item_id '{item_id}' not found"}, ensure_ascii=False)


# --- Slack ---

@server.tool()
def slack_search(query: str, limit: int = 10) -> str:
    """Slack (mock)에서 쿼리 검색. 채널 메시지, 결정사항, 예외 정책 포함.

    사용 예:
    - PG v3 결정 확인 → query="v3"
    - 부분환불 논의 → query="partial refund" 또는 "PARTIAL_REFUND"
    - 환불 기간 확정/논의 → query="14일" 또는 "환불 가능 기간"
    - 해외결제 정책 → query="해외결제"
    - 예외 정책 → query="예외" 또는 "특정 결제 유형"
    """
    _require_workspace(demo_only=True)
    q = query.lower()
    results: list[dict[str, Any]] = []
    for channel, msgs in _SLACK.get("channels", {}).items():
        for m in msgs:
            if q in m["text"].lower():
                results.append({"item_id": f"{channel}/{m['timestamp']}",
                                "type": "message", "channel": channel,
                                "user": m.get("user", ""), "timestamp": m.get("timestamp", ""),
                                "text": m["text"]})
                if len(results) >= limit:
                    return json.dumps(results, ensure_ascii=False, indent=2)
    return json.dumps(results, ensure_ascii=False, indent=2)


@server.tool()
def slack_get(item_id: str) -> str:
    """Slack mock 메시지 상세 조회. item_id 예: payment-eng/2026-09-09T09:15:00Z.

    특정 메시지의 전체 컨텍스트 확인에 사용.
    """
    _require_workspace(demo_only=True)
    if "/" not in item_id:
        return json.dumps({"error": "item_id 형식: channel/timestamp"}, ensure_ascii=False)
    channel, ts = item_id.split("/", 1)
    for ch, msgs in _SLACK.get("channels", {}).items():
        if ch == channel:
            for m in msgs:
                if m.get("timestamp") == ts:
                    return json.dumps({"item_id": item_id, "channel": channel,
                                       "message": m}, ensure_ascii=False, indent=2)
    return json.dumps({"error": f"item_id '{item_id}' not found"}, ensure_ascii=False)


# --- Notion ---

@server.tool()
def notion_search(query: str, limit: int = 10) -> str:
    """Notion (mock)에서 쿼리 검색. 정책 문서, 아키텍처, 설계 문서.

    사용 예:
    - 기존 환불 정책 → query="환불 정책" 또는 "환불 가능 기간"
    - PG API 문서 → query="v2" 또는 "PG API"
    - 결제 아키텍처 → query="아키텍처"
    - 부분환불 정책 → query="부분환불" 또는 "미지원"
    """
    _require_workspace(demo_only=True)
    q = query.lower()
    results: list[dict[str, Any]] = []
    for s in _NOTION_SECTIONS:
        if q in s["content"].lower():
            results.append({"item_id": s["id"], "type": "page",
                            "title": s["title"],
                            "snippet": _notion_snippet(s["content"], q)})
            if len(results) >= limit:
                break
    return json.dumps(results, ensure_ascii=False, indent=2)


def _notion_snippet(content: str, q: str) -> str:
    idx = content.lower().find(q)
    if idx == -1:
        return content[:200]
    s = max(0, idx - 60); e = min(len(content), idx + 200)
    return content[s:e]


@server.tool()
def notion_get(item_id: str) -> str:
    """Notion mock 페이지 상세 조회. item_id 예: 결제-시스템-아키텍처, 환불-정책-문서.

    전체 문서 내용을 확인할 때 사용. 오래된 버전의 v2 문서 등 확인 가능.
    """
    _require_workspace(demo_only=True)
    for s in _NOTION_SECTIONS:
        if s["id"] == item_id:
            return json.dumps({"item_id": item_id, "type": "page",
                               "title": s["title"], "content": s["content"]},
                              ensure_ascii=False, indent=2)
    return json.dumps({"error": f"item_id '{item_id}' not found"}, ensure_ascii=False)


def _integer(value: int, name: str, minimum: int, maximum: int | None = None) -> int:
    if (type(value) is not int or value < minimum
            or (maximum is not None and value > maximum)):
        bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise WorkspaceValidationError(f"{name} must be an integer {bound}.")
    return value


def _original_offset(text: str, folded_offset: int) -> int:
    """Case folding can expand a character; tool offsets always address the original body."""
    consumed = 0
    for index, char in enumerate(text):
        consumed += len(char.casefold())
        if consumed > folded_offset:
            return index
    return len(text)


@server.tool()
def document_search(workspace_id: str, query: str, limit: int = 10, offset: int = 0) -> str:
    """Search registered uploaded documents in the current analysis workspace.

    Use Korean and/or English query terms. Results are ranked by lexical matches;
    an empty query lists document metadata. limit is 1..50; offset paginates results.
    Each result's offset locates its snippet in the original body for document_get.
    next_offset is the next result-page offset, not a document-body offset.
    """
    if not isinstance(workspace_id, str):
        raise WorkspaceValidationError("workspace_id must be text.")
    _require_workspace(workspace_id)
    _integer(limit, "limit", 1, 50)
    _integer(offset, "offset", 0)
    if not isinstance(query, str) or len(query) > 500 or "\x00" in query:
        raise WorkspaceValidationError("query must be text of at most 500 characters.")
    terms = list(dict.fromkeys(re.findall(r"[^\W_]+", query.casefold())))
    if query.strip() and not terms:
        raise WorkspaceValidationError("query must contain letters or numbers, or be empty to list documents.")
    results = []
    for document in _STORE.list_documents(workspace_id):
        body = document["body"]
        folded_body = body.casefold()
        title = document["title"]
        folded_title = title.casefold()
        positions = [folded_body.find(term) for term in terms]
        matched = [term for term, position in zip(terms, positions)
                   if position >= 0 or term in folded_title]
        if terms and not matched:
            continue
        score = sum(10 + 3 * (term in folded_title) + min(folded_body.count(term), 20)
                    for term in matched)
        first_match = min((position for position in positions if position >= 0), default=0)
        start = max(0, _original_offset(body, first_match) - 80) if terms else 0
        results.append({
            "item_id": document["id"], "document_id": document["id"],
            "title": title, "source_type": document["source_type"],
            "timestamp": document.get("timestamp"),
            "snippet": body[start:start + 400] if terms else "", "offset": start, "score": score,
        })
    results.sort(key=lambda result: (-result["score"], result["title"].casefold(), result["document_id"]))
    if offset > len(results):
        raise WorkspaceValidationError("offset exceeds the number of matching documents.")
    end = min(offset + limit, len(results))
    return json.dumps({
        "workspace_id": workspace_id, "query": query, "results": results[offset:end],
        "total_matches": len(results), "next_offset": end if end < len(results) else None,
    }, ensure_ascii=False, indent=2)


@server.tool()
def document_retrieve(
    workspace_id: str,
    query: str,
    top_k: int = 3,
    max_chars_per_doc: int = 3000,
) -> str:
    """Retrieve ranked document evidence in one MCP call.

    This is the fast path for ContextPack. It combines lexical search and bounded
    document reads so the agent does not need a separate search -> model -> get
    round trip for ordinary uploaded-document tasks.

    Use document_search/document_get only when this result is insufficient or a
    longer continuation is explicitly required.
    """
    _require_workspace(workspace_id)
    _integer(top_k, "top_k", 1, 5)
    _integer(max_chars_per_doc, "max_chars_per_doc", 500, 6000)

    searched = json.loads(document_search(workspace_id, query, limit=top_k, offset=0))
    evidence = []

    for result in searched["results"]:
        fetched = json.loads(document_get(
            workspace_id,
            result["document_id"],
            offset=result["offset"],
            max_chars=max_chars_per_doc,
        ))
        evidence.append({
            "document_id": result["document_id"],
            "title": result["title"],
            "timestamp": result.get("timestamp"),
            "score": result["score"],
            "offset": fetched["offset"],
            "body": fetched["body"],
            "next_offset": fetched["next_offset"],
            "truncated": fetched["truncated"],
        })

    return json.dumps({
        "workspace_id": workspace_id,
        "query": query,
        "results": evidence,
        "total_matches": searched["total_matches"],
    }, ensure_ascii=False, indent=2)


@server.tool()
def document_get(workspace_id: str, document_id: str, offset: int = 0, max_chars: int = 12000) -> str:
    """Read one registered uploaded document in the current analysis workspace.

    offset and max_chars use Unicode character positions (max_chars is 1..20000).
    Follow next_offset until null to read the remaining body; truncated explicitly
    indicates a partial document. Page/block ranges remain absolute body offsets.
    Only page/block metadata overlapping this slice is returned; warnings are retained.
    """
    if not isinstance(workspace_id, str):
        raise WorkspaceValidationError("workspace_id must be text.")
    _require_workspace(workspace_id)
    _integer(offset, "offset", 0)
    _integer(max_chars, "max_chars", 1, 20000)
    document = _STORE.get_document(workspace_id, document_id)
    body = document["body"]
    if offset > len(body):
        raise WorkspaceValidationError("offset exceeds the document length.")
    end = min(offset + max_chars, len(body))
    metadata = dict(document.get("metadata", {}))
    for key in ("pages", "blocks"):
        if key in metadata:
            metadata[key] = [item for item in metadata[key]
                             if item.get("start", 0) < end and item.get("end", 0) > offset]
    return json.dumps({
        **document, "body": body[offset:end], "metadata": metadata,
        "total_chars": len(body), "offset": offset,
        "next_offset": end if end < len(body) else None,
        "truncated": offset > 0 or end < len(body),
    }, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 엔트리 포인트
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # stdio로 실행 → Hermes가 subprocess로 연결
    server.run()
