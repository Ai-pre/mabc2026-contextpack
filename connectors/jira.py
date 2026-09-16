"""
Mock Jira connector.

Loads from data/demo_project/jira.json.
Provides: issues, comments, status.
"""

from __future__ import annotations

from typing import Any

from connectors import _load_demo_data, SourceConnector


class JiraConnector:
    """Mock Jira source connector."""

    def __init__(self, data_path: str | None = None):
        if data_path is None:
            self._data = _load_demo_data("jira.json")
        else:
            with open(data_path, encoding="utf-8") as f:
                self._data = json.load(f)

    @property
    def source_name(self) -> str:
        return "Jira"

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        issues = self._data.get("issues", [])
        recent = sorted(
            [{"item_id": it["id"], "type": "issue", "title": it.get("summary", "")} for it in issues],
            key=lambda x: x["item_id"],
            reverse=True,
        )
        return recent[:limit]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        q = query.lower()
        issues = self._data.get("issues", [])
        results: list[dict[str, Any]] = []
        for it in issues:
            text = " ".join(str(v) for v in it.values()).lower()
            if q in text:
                results.append({
                    "item_id": it["id"],
                    "type": "issue",
                    "title": it.get("summary", ""),
                    "status": it.get("status", ""),
                    "snippet": self._snippet(it, q),
                })
                if len(results) >= limit:
                    break
        return results

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        for it in self._data.get("issues", []):
            if it["id"] == item_id:
                return dict(it)
        return None

    def _snippet(self, issue: dict, query: str) -> str:
        text = f"{issue.get('summary', '')} {issue.get('description', '')}"
        for c in issue.get("comments", []):
            text += f" {c.get('body', '')}"
        text_lower = text.lower()
        idx = text_lower.find(query)
        if idx == -1:
            return text[:200]
        start = max(0, idx - 60)
        end = min(len(text), idx + 200)
        return text[start:end]


def get_jira_connector() -> JiraConnector:
    return JiraConnector()
