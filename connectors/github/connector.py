# connectors/github/connector.py
from __future__ import annotations
import json
import os
from typing import Any

from connectors import BaseConnector, SearchResult


DATA_PATH = os.path.join(os.path.dirname(__file__), "../../data/demo_project/github_data.json")


class GitHubConnector(BaseConnector):
    @property
    def name(self) -> str:
        return "github"

    def _load(self) -> dict[str, Any]:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        data = self._load()
        query_l = query.lower()
        results: list[SearchResult] = []

        # PR 및 코드 파일에서 검색
        for pr in data.get("pr", []):
            text = (pr.get("title", "") + " " + pr.get("body", "") + " " +
                    " ".join(f.get("path", "") for f in pr.get("files", []))).lower()
            if query_l in text:
                results.append(SearchResult(
                    source="github",
                    title=f"PR #{pr['id']}: {pr['title']}",
                    snippet=pr["body"][:400],
                    ref=f"pr:{pr['id']}",
                    timestamp=pr.get("merged_at"),
                    primary_links=[f"https://github.com/org/repo/pull/{pr['id']}"],
                    raw={"type": "pull_request", **pr},
                ))

        for cf in data.get("code_files", []):
            text = (cf.get("path", "") + " " + cf.get("content", "")).lower()
            if query_l in text:
                results.append(SearchResult(
                    source="github",
                    title=f"Code: {cf['path']}",
                    snippet=cf["content"][:400],
                    ref=f"file:{cf['path']}",
                    primary_links=[f"https://github.com/org/repo/blob/main/{cf['path']}"],
                    raw={"type": "code_file", **cf},
                ))

        # 관련도 단순 정렬: 제목/코드에 쿼리 단어가 포함된 항목을 위로
        results.sort(key=lambda r: (query_l not in r.title.lower(), r.snippet.lower().count(query_l)),
                     reverse=True)
        return results[:limit]

    def get_item(self, ref: str) -> dict[str, Any] | None:
        if not ref.startswith("pr:"):
            return None
        pr_id = int(ref.split(":", 1)[1])
        data = self._load()
        for pr in data.get("pr", []):
            if pr["id"] == pr_id:
                return {"type": "pull_request", **pr}
        return None
