"""
Mock GitHub connector.

Loads from data/demo_project/github.json.
Provides: PRs, file contents, repo metadata.
"""

from __future__ import annotations

from typing import Any

from connectors import _load_demo_data, SourceConnector


class GitHubConnector:
    """Mock GitHub source connector."""

    def __init__(self, data_path: str | None = None):
        self._data = _load_demo_data("github.json") if data_path is None else self._load(data_path)

    @staticmethod
    def _load(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @property
    def source_name(self) -> str:
        return "GitHub"

    @property
    def repo(self) -> str:
        return self._data.get("repo", "unknown")

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        items = self._data.get("items", [])
        prs = [it for it in items if it.get("type") == "pull_request"]
        files = [it for it in items if it.get("type") == "file"]
        recent = sorted(
            [{"item_id": it["id"], "type": it["type"], "title": it.get("title", it.get("path", ""))} for it in items],
            key=lambda x: x["item_id"],
            reverse=True,
        )
        return recent[:limit]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Simple keyword search across PRs and files."""
        q = query.lower()
        items = self._data.get("items", [])
        results: list[dict[str, Any]] = []
        for it in items:
            text = " ".join(str(v) for v in it.values()).lower()
            if q in text:
                results.append({"item_id": it["id"], "type": it["type"], "title": it.get("title", it.get("path", "")), "snippet": self._snippet(it, q)})
                if len(results) >= limit:
                    break
        return results

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        for it in self._data.get("items", []):
            if it["id"] == item_id:
                return dict(it)
        return None

    def _snippet(self, item: dict, query: str) -> str:
        body = item.get("body", item.get("content", ""))
        if not body:
            return ""
        body_lower = body.lower()
        idx = body_lower.find(query)
        if idx == -1:
            return body[:200]
        start = max(0, idx - 60)
        end = min(len(body), idx + 200)
        return body[start:end]


# Singleton factory for demo use
def get_github_connector() -> GitHubConnector:
    return GitHubConnector()
