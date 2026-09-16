# connectors/jira/connector.py
from __future__ import annotations
import json
import os
import re
from typing import Any

from connectors import BaseConnector, SearchResult


DATA_PATH = os.path.join(os.path.dirname(__file__), "../../data/demo_project/jira_data.json")


class JiraConnector(BaseConnector):
    @property
    def name(self) -> str:
        return "jira"

    def _load(self) -> dict[str, Any]:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def _text_for_issue(self, issue: dict[str, Any]) -> str:
        parts = [issue.get("summary", ""), issue.get("description", "")]
        for c in issue.get("comments", []):
            parts.append(c.get("author", "") + ": " + c.get("body", ""))
        return " ".join(parts)

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        data = self._load()
        query_l = query.lower()
        results: list[SearchResult] = []

        for issue in data.get("issues", []):
            text = self._text_for_issue(issue).lower()
            if query_l in text or query_l in issue.get("key", "").lower():
                # 댓글에서 관련 발췌
                snippet = issue.get("description", "")[:300]
                results.append(SearchResult(
                    source="jira",
                    title=f"{issue['key']}: {issue['summary']}",
                    snippet=snippet,
                    ref=f"jira:{issue['key']}",
                    timestamp=issue.get("updated"),
                    primary_links=[f"https://jira.example.com/browse/{issue['key']}"],
                    raw={"type": "issue", **issue},
                ))

        results.sort(key=lambda r: (query_l not in r.title.lower(), r.snippet.lower().count(query_l)),
                     reverse=True)
        return results[:limit]

    def get_item(self, ref: str) -> dict[str, Any] | None:
        if not ref.startswith("jira:"):
            return None
        key = ref.split(":", 1)[1]
        data = self._load()
        for issue in data.get("issues", []):
            if issue["key"] == key:
                return {"type": "issue", **issue}
        return None
