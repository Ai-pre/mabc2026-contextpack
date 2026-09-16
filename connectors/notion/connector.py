# connectors/notion/connector.py
from __future__ import annotations
import json
import os
from typing import Any

from connectors import BaseConnector, SearchResult


DATA_PATH = os.path.join(os.path.dirname(__file__), "../../data/demo_project/notion_data.json")


class NotionConnector(BaseConnector):
    @property
    def name(self) -> str:
        return "notion"

    def _load(self) -> dict[str, Any]:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        data = self._load()
        query_l = query.lower()
        results: list[SearchResult] = []

        for page in data.get("pages", []):
            text = (page.get("title", "") + " " + page.get("content", "") +
                    " " + " ".join(page.get("tags", []))).lower()
            if query_l in text:
                results.append(SearchResult(
                    source="notion",
                    title=page["title"],
                    snippet=page["content"][:400],
                    ref=f"notion:{page['id']}",
                    timestamp=page.get("last_edited"),
                    primary_links=[f"https://notion.so/{page['id']}"],
                    raw={"type": "page", **page},
                ))

        results.sort(key=lambda r: r.snippet.lower().count(query_l), reverse=True)
        return results[:limit]

    def get_item(self, ref: str) -> dict[str, Any] | None:
        if not ref.startswith("notion:"):
            return None
        page_id = ref.split(":", 1)[1]
        data = self._load()
        for page in data.get("pages", []):
            if page["id"] == page_id:
                return {"type": "page", **page}
        return None
