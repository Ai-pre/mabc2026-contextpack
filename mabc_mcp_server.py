# mabc_mcp_server.py — MVP용 경량 MCP stdio server
# 4개 demo connector와 workspace 업로드 문서를 MCP tool로 노출한다.
# Solar Pro 4가 Role+Task에 따라 필요한 tool을 스스로 선택·호출한다.

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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


# --- Live GitHub connector helpers ---

_GITHUB_LIVE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}

def _github_token() -> str:
    token = os.environ.get("GITHUB_MCP_TOKEN", "").strip()
    if not token or token.startswith("${"):
        raise WorkspaceValidationError("Live GitHub is not configured on this server.")
    return token


def _github_api(path: str) -> Any:
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": f"Bearer {_github_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ContextPack/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message", "")
        except Exception:
            detail = ""
        message = f"GitHub API returned HTTP {exc.code}"
        if detail:
            message += f": {detail}"
        raise WorkspaceValidationError(message) from exc
    except (urllib.error.URLError, TimeoutError, ValueError, UnicodeError) as exc:
        raise WorkspaceValidationError(f"GitHub API request failed: {exc}") from exc


def _registered_github_repositories(workspace_id: str) -> list[str]:
    workspace = _STORE.get_workspace(workspace_id)
    return [
        source["repository"]
        for source in workspace["sources"]
        if source.get("source_type") == "connector"
        and source.get("connector") == "github"
        and source.get("repository")
    ]


def _query_terms(query: str) -> list[str]:
    return list(dict.fromkeys(
        term for term in re.findall(r"[A-Za-z0-9_.-]+", query.casefold())
        if len(term) >= 2
    ))


def _text_score(value: str, terms: list[str]) -> int:
    folded = value.casefold()
    return sum(1 for term in terms if term in folded)


@server.tool()
def github_retrieve(
    workspace_id: str,
    query: str,
    repository: str = "",
    recent_limit: int = 8,
) -> str:
    """Retrieve compact live GitHub evidence in one MCP call.

    Preferred fast path for connected repositories. One call gathers repository
    metadata, recent commits/PRs, selected PR changed files, README context and
    repository structure. Repeated calls in the same Hermes session reuse the
    cached GitHub snapshot instead of repeating network requests.
    """
    _require_workspace(workspace_id)
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or "\x00" in query:
        raise WorkspaceValidationError("query must be non-empty text of at most 500 characters.")
    _integer(recent_limit, "recent_limit", 1, 15)

    repositories = _registered_github_repositories(workspace_id)
    if not repositories:
        raise WorkspaceValidationError("No GitHub repository is connected to this workspace.")

    if repository:
        normalized = WorkspaceStore._github_repository(repository)
        match = next((item for item in repositories if item.casefold() == normalized.casefold()), None)
        if not match:
            raise WorkspaceValidationError("Requested GitHub repository is not connected to this workspace.")
        repository = match
    elif len(repositories) == 1:
        repository = repositories[0]
    else:
        raise WorkspaceValidationError("repository is required when multiple GitHub repositories are connected.")

    owner, repo = repository.split("/", 1)
    base = f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}"
    cache_key = (workspace_id, repository.casefold())
    snapshot = _GITHUB_LIVE_CACHE.get(cache_key)

    if snapshot is None:
        metadata = _github_api(base)
        default_branch = metadata.get("default_branch") or "main"

        def fetch_commits():
            return _github_api(
                f"{base}/commits?sha={urllib.parse.quote(default_branch)}&per_page={recent_limit}"
            )

        def fetch_pulls():
            return _github_api(
                f"{base}/pulls?state=all&sort=updated&direction=desc&per_page={recent_limit}"
            )

        def fetch_readme():
            try:
                return _github_api(f"{base}/readme")
            except WorkspaceValidationError:
                return {}

        def fetch_tree():
            try:
                return _github_api(
                    f"{base}/git/trees/{urllib.parse.quote(default_branch)}?recursive=1"
                )
            except WorkspaceValidationError:
                return {}

        # These requests are independent once the default branch is known.
        with ThreadPoolExecutor(max_workers=4) as pool:
            commits_future = pool.submit(fetch_commits)
            pulls_future = pool.submit(fetch_pulls)
            readme_future = pool.submit(fetch_readme)
            tree_future = pool.submit(fetch_tree)
            commits = commits_future.result()
            pulls = pulls_future.result()
            readme_obj = readme_future.result()
            tree = tree_future.result()

        pull_files: dict[int, list[dict[str, Any]]] = {}
        pull_numbers = [
            item.get("number")
            for item in (pulls if isinstance(pulls, list) else [])[:3]
            if isinstance(item.get("number"), int)
        ]

        def fetch_pr_files(number: int):
            try:
                return number, _github_api(f"{base}/pulls/{number}/files?per_page=50")
            except WorkspaceValidationError:
                return number, []

        if pull_numbers:
            with ThreadPoolExecutor(max_workers=min(3, len(pull_numbers))) as pool:
                for number, files in pool.map(fetch_pr_files, pull_numbers):
                    pull_files[number] = files if isinstance(files, list) else []

        readme = ""
        if isinstance(readme_obj, dict) and readme_obj.get("content"):
            try:
                readme = base64.b64decode(readme_obj["content"]).decode(
                    "utf-8", errors="replace"
                )[:1800]
            except Exception:
                readme = ""

        entries = tree.get("tree", []) if isinstance(tree, dict) else []
        paths = [entry.get("path", "") for entry in entries if entry.get("path")]

        snapshot = {
            "metadata": metadata,
            "default_branch": default_branch,
            "commits": commits if isinstance(commits, list) else [],
            "pulls": pulls if isinstance(pulls, list) else [],
            "pull_files": pull_files,
            "readme": readme,
            "paths": paths,
        }
        _GITHUB_LIVE_CACHE[cache_key] = snapshot

    metadata = snapshot["metadata"]
    default_branch = snapshot["default_branch"]
    commits = snapshot["commits"]
    pulls = snapshot["pulls"]
    pull_files = snapshot["pull_files"]
    readme = snapshot["readme"]
    paths = snapshot["paths"]

    terms = _query_terms(query)

    compact_commits = []
    for item in commits:
        commit = item.get("commit", {}) or {}
        message = (commit.get("message") or "").split("\n", 1)[0]
        compact_commits.append({
            "sha": (item.get("sha") or "")[:12],
            "date": ((commit.get("author") or {}).get("date")),
            "message": message,
            "_score": _text_score(message, terms),
        })
    compact_commits.sort(
        key=lambda item: (item["_score"], item.get("date") or ""),
        reverse=True,
    )
    compact_commits = [
        {k: v for k, v in item.items() if k != "_score"}
        for item in compact_commits[:6]
    ]

    compact_pulls = []
    for item in pulls:
        title = item.get("title") or ""
        body = item.get("body") or ""
        combined = f"{title}\n{body}"
        number = item.get("number")
        files = pull_files.get(number, []) if isinstance(number, int) else []
        compact_pulls.append({
            "number": number,
            "state": item.get("state"),
            "draft": bool(item.get("draft")),
            "updated_at": item.get("updated_at"),
            "merged_at": item.get("merged_at"),
            "title": title,
            "body_preview": body[:450] if body else "",
            "files": [
                {
                    "filename": file.get("filename"),
                    "status": file.get("status"),
                    "additions": file.get("additions"),
                    "deletions": file.get("deletions"),
                }
                for file in files[:20]
            ],
            "_score": _text_score(combined, terms),
        })
    compact_pulls.sort(
        key=lambda item: (item["_score"], item.get("updated_at") or ""),
        reverse=True,
    )
    compact_pulls = [
        {k: v for k, v in item.items() if k != "_score"}
        for item in compact_pulls[:2]
    ]

    scored_paths = [
        (_text_score(path, terms), path)
        for path in paths
        if terms and _text_score(path, terms) > 0
    ]
    scored_paths.sort(key=lambda item: (-item[0], item[1]))

    tree_summary = {
        "root_files": sorted(path for path in paths if "/" not in path)[:20],
        "top_level_dirs": sorted({
            path.split("/", 1)[0] for path in paths if "/" in path
        })[:20],
        "query_paths": [path for _, path in scored_paths[:15]],
    }

    return json.dumps({
        "workspace_id": workspace_id,
        "repository": repository,
        "query": query,
        "repository_meta": {
            "description": metadata.get("description"),
            "default_branch": default_branch,
            "updated_at": metadata.get("updated_at"),
            "pushed_at": metadata.get("pushed_at"),
        },
        "recent_commits": compact_commits,
        "relevant_recent_pull_requests": compact_pulls,
        "tree_summary": tree_summary,
        "readme_preview": readme,
        "cache_reused": cache_key in _GITHUB_LIVE_CACHE,
    }, ensure_ascii=False, separators=(",", ":"))


# --- Demo fast path ---

@server.tool()
def demo_context_retrieve(query: str) -> str:
    """Return the compact, task-relevant demo evidence bundle in one MCP call.

    The demo fixture is fixed and intentionally contains stale, conflicting,
    missing and irrelevant information. This fast path keeps only the claims
    needed for the partial-refund handoff while preserving source/date/provenance.
    """
    _require_workspace(demo_only=True)
    if not isinstance(query, str) or not query.strip() or len(query) > 500 or "\x00" in query:
        raise WorkspaceValidationError("query must be non-empty text of at most 500 characters.")

    evidence = [
        {
            "source": "github",
            "ref": "PR #148",
            "date": "2026-09-02",
            "claim": "PG API v2→v3 migration merged; v2 endpoint deprecated/removed; existing transaction compatibility and idempotency retained.",
        },
        {
            "source": "github",
            "ref": "PR #152",
            "date": "2026-09-09",
            "claim": "PARTIAL_REFUND status and partial-refund support merged; PG API v3 is used; existing refund transaction idempotency must be retained.",
        },
        {
            "source": "github",
            "ref": "refund_service.py",
            "date": None,
            "claim": "partial_refund(payment_id, amount, idempotency_key) calls the PG v3 partial-refund endpoint; overseas-payment partial-refund business policy is not defined.",
        },
        {
            "source": "jira",
            "ref": "PAY-183",
            "date": "2026-09-10",
            "claim": "Partial refund support is Done; PR #152 merged; PARTIAL_REFUND and idempotency requirements are confirmed.",
        },
        {
            "source": "jira",
            "ref": "PAY-181",
            "date": "2026-09-11",
            "claim": "Subscription partial refunds can use the existing policy; overseas partial refunds require a separate policy that is still undefined.",
        },
        {
            "source": "jira",
            "ref": "PAY-179 / comment by po-jang",
            "date": "2026-09-12",
            "claim": "General-payment refund window was finally approved as 14 days, effective 2026-10-01.",
            "certainty": "explicit final/approved claim",
        },
        {
            "source": "slack",
            "ref": "payment-eng / legal-minsu",
            "date": "2026-09-12",
            "claim": "Legal review final decision: keep the general-payment refund window at 7 days; the 14-day change was cancelled.",
            "certainty": "explicit final decision claim",
        },
        {
            "source": "slack",
            "ref": "payment-eng / developer-yuki",
            "date": "2026-09-11",
            "claim": "Partial-refund implementation must use PG API v3; v2 is deprecated.",
        },
        {
            "source": "notion",
            "ref": "결제 시스템 아키텍처",
            "date": "2026-08-10",
            "claim": "Older document says PG API v2, partial refunds unsupported, and refund window 7 days.",
            "status": "stale_candidate",
        },
        {
            "source": "notion",
            "ref": "2026 Q3 결제 시스템 변경 요약",
            "date": "2026-09-05",
            "claim": "Summary says PG API v3 migration completed, PARTIAL_REFUND added, partial refunds supported, while 7→14-day refund-window change was still under discussion.",
        },
    ]

    return json.dumps({
        "workspace_id": "demo",
        "query": query,
        "evidence": evidence,
        "rules": {
            "preserve_conflicts": True,
            "do_not_assume_missing_policy": ["overseas partial refund"],
            "ignore_irrelevant_demo_items": ["frontend", "marketing", "site-reliability"],
        },
    }, ensure_ascii=False, separators=(",", ":"))


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
